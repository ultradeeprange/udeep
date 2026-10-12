"""The live view (docs/view/): its copies are the project's own files, and its shader draws the declared mapping.

The page itself needs a browser. Its fragment shader does not: here it is compiled with the reference shader, as
the page compiles it, and run on a GPU into a float texture, which is read back and held against the float64
reference. Skipped where wgpu finds no adapter.
"""
import importlib.util
import struct
from pathlib import Path

import numpy as np
import pytest

from udeep import Mapping, gpu
from udeep.view import hdr_view

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "docs" / "view"


def make_view():
    spec = importlib.util.spec_from_file_location("make_view", ROOT / "tools" / "make_view.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_pages_copies_are_the_projects_own_files():
    """The page can only load what is in docs/. What it carries must be the reference shader, the JavaScript port
    and the registered tables, byte for byte: run tools/make_view.py after changing any of them."""
    copies = make_view().copies()
    assert len(copies) == 2 + 2 * 4
    for copy, source in copies.items():
        assert copy.is_file(), f"{copy.name} is missing: run tools/make_view.py"
        assert copy.read_bytes() == source.read_bytes(), f"{copy.name} differs from {source}: run tools/make_view.py"
    page, script = (VIEW / "index.html").read_text(encoding="utf-8"), (VIEW / "view.js").read_text(encoding="utf-8")
    assert '<script src="udeepcore.js">' in page and '<script src="view.js">' in page
    assert 'fetch("mapping.wgsl")' in script and 'fetch("view.wgsl")' in script and '"tables/"' in script
    # every map the page offers has its tables there, at every headroom it offers
    import re
    maps = [m for m in re.findall(r'<option value="([a-z]+/\d)">', page)]
    heads = re.search(r'<select id="headroom">(.*?)</select>', page, re.DOTALL).group(1)
    for name in maps:
        for headroom in re.findall(r'value="(\d+)"', heads):
            assert (VIEW / "tables" / f"{name.replace('/', '-')}-h{headroom}.f32").is_file(), (name, headroom)
    assert maps == ["fire/3", "ice/3"]


# ---- the viewer's shader, on a GPU

def device_and_pipeline():
    wgpu = pytest.importorskip("wgpu")
    try:
        adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
    except Exception as err:                                  # no GPU, no driver: nothing to test here
        pytest.skip(f"no GPU adapter: {err}")
    device = adapter.request_device_sync()
    code = (VIEW / "mapping.wgsl").read_text(encoding="utf-8") + "\n" + (VIEW / "view.wgsl").read_text(encoding="utf-8")
    module = device.create_shader_module(code=code)
    pipeline = device.create_render_pipeline(
        layout="auto", vertex={"module": module, "entry_point": "view_vertex"},
        fragment={"module": module, "entry_point": "view_fragment", "targets": [{"format": wgpu.TextureFormat.rgba16float}]},
        primitive={"topology": wgpu.PrimitiveTopology.triangle_list})
    return wgpu, device, pipeline


def draw(mapping, data, held=None, sdr_only=False, canvas=None, bar=(0.0, 0.0)):
    """What the page's shader puts on a float canvas of `canvas` (width, height) pixels: linear RGB, H x W x 3, and
    which pixels it drew at all."""
    wgpu, device, pipeline = device_and_pipeline()
    height, width = data.shape
    canvas = canvas or (width, height)
    use = wgpu.BufferUsage
    table = mapping.table
    nodes = np.zeros(6, np.float32) if table is None else np.frombuffer(table.to_bytes(), dtype="<f4")
    mask = np.zeros(max(data.size, 4), np.uint32) if held is None else np.ascontiguousarray(held, dtype=np.uint32).reshape(-1)
    view = struct.pack("<4I4f", width, height, int(sdr_only), int(held is not None), float(canvas[0]), float(canvas[1]), *bar)
    buffers = [device.create_buffer_with_data(data=gpu._pack(mapping), usage=use.UNIFORM),
               device.create_buffer_with_data(data=nodes.tobytes(), usage=use.STORAGE),
               device.create_buffer_with_data(data=np.ascontiguousarray(data, dtype=np.float32).tobytes(), usage=use.STORAGE)]
    extra = [device.create_buffer_with_data(data=view, usage=use.UNIFORM),
             device.create_buffer_with_data(data=mask.tobytes(), usage=use.STORAGE)]
    groups = [device.create_bind_group(layout=pipeline.get_bind_group_layout(0), entries=[
                  {"binding": i, "resource": {"buffer": b, "offset": 0, "size": b.size}} for i, b in enumerate(buffers)]),
              device.create_bind_group(layout=pipeline.get_bind_group_layout(1), entries=[
                  {"binding": i, "resource": {"buffer": b, "offset": 0, "size": b.size}} for i, b in enumerate(extra)])]
    target = device.create_texture(size=(canvas[0], canvas[1], 1), format=wgpu.TextureFormat.rgba16float,
                                   usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC)
    encoder = device.create_command_encoder()
    render = encoder.begin_render_pass(color_attachments=[{
        "view": target.create_view(), "clear_value": (0, 0, 0, 0), "load_op": wgpu.LoadOp.clear, "store_op": wgpu.StoreOp.store}])
    render.set_pipeline(pipeline)
    render.set_bind_group(0, groups[0]); render.set_bind_group(1, groups[1])
    render.draw(3)
    render.end()
    device.queue.submit([encoder.finish()])
    raw = device.queue.read_texture({"texture": target}, {"bytes_per_row": canvas[0] * 8}, (canvas[0], canvas[1], 1))
    signal = np.frombuffer(raw, dtype="<f2").reshape(canvas[1], canvas[0], 4).astype(np.float64)
    c = signal[..., :3]
    linear = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)        # the sRGB curve, carried past white
    return linear, signal[..., 3] > 0.5


STRETCH = {"kind": "asinh", "lo": 0.0, "hi": 100.0, "width": 5.0}
CLOSE = {"rtol": 3e-3, "atol": 3e-3}                     # a 16-bit float canvas holds about 11 bits


@pytest.mark.parametrize("cmap", ["grey", "fire", "ice"])
@pytest.mark.parametrize("headroom", [2.0, 4.0, 16.0])
def test_the_views_shader_draws_the_declared_mapping(cmap, headroom):
    m = Mapping.from_stretch(STRETCH, 30.0, headroom, cmap)
    data = np.random.default_rng(5).uniform(-10.0, 120.0, (48, 64)).astype(np.float32)       # some outside the range
    shown, drawn = draw(m, data)
    assert drawn.all() and shown.shape == (48, 64, 3)
    want = m.colours(m.light(data.astype(np.float64)))                    # the reference, on the numbers the card had
    assert np.allclose(shown, want, **CLOSE)
    assert shown.max() > 0.9 * headroom and want.max() == pytest.approx(headroom, rel=1e-3)   # above white is drawn
    fair, _ = draw(m, data, sdr_only=True)
    assert np.allclose(fair, m.colours(m.light(data.astype(np.float64), headroom=1.0)), **CLOSE)
    assert fair.max() <= 1.0 + 3e-3


def test_the_view_holds_and_blanks_the_pixels_it_is_told_to():
    """What the page's Highlight preset relies on: 1 holds a pixel at its fair SDR light, 2 draws it black."""
    m = Mapping.from_stretch(STRETCH, 30.0, 4.0, "fire")
    data = np.random.default_rng(6).exponential(20.0, (40, 56)).astype(np.float32)
    held = np.zeros(data.shape, np.uint32); held[5:9, 5:20] = 1; held[20:24, 30:40] = 2
    shown, _ = draw(m, data, held)
    d = data.astype(np.float64)
    want = m.colours(m.light(d)); want[held == 1] = m.colours(m.light(d, headroom=1.0))[held == 1]; want[held == 2] = 0.0
    assert np.allclose(shown, want, **CLOSE)
    assert (m.light(d)[held == 1] > 1.0).any() and shown[held == 1].max() <= 1.0 + 3e-3   # some of those would be lifted
    # and it is the page's own rule for which pixels those are: the reference view's derived mask
    view = hdr_view(d, "highlight", 30.0, STRETCH, 4.0, "fire")
    from udeep import masks
    lone = masks.isolated(m.position(d) > m.knee_position)
    assert lone.sum() == view.derived_masked > 0
    shown, _ = draw(m, data, lone.astype(np.uint32))
    assert np.allclose(shown, view.hdr, **CLOSE)


def test_the_view_draws_nearest_pixels_and_an_even_colour_bar():
    m = Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 100.0}, 50.0, 4.0, "ice")
    data = np.arange(12, dtype=np.float32).reshape(3, 4) * 9.0
    width, height, bar, gap = 4 * 16 + 20, 3 * 16, 12.0, 8.0
    shown, drawn = draw(m, data, canvas=(width, height), bar=(bar, gap))
    image = shown[:, :64]
    blocks = image.reshape(3, 16, 4, 16, 3)
    assert np.allclose(blocks, blocks[:, :1, :, :1], atol=1e-6)                     # each data pixel is one flat block
    assert np.allclose(blocks[:, 0, :, 0], m.colours(m.light(data.astype(np.float64))), **CLOSE)
    assert drawn[:, :64].all() and not drawn[:, 64:72].any() and drawn[:, 72:].all()   # image, gap, bar
    t = 1.0 - (np.arange(height) + 0.5) / height                                    # the top of the range at the top
    assert np.allclose(shown[:, 72:], m.colours(m.light_at(t))[:, None, :], **CLOSE)
    assert shown[0, -1].max() > 3.5 and shown[-1, -1].max() < 0.1
