"""The 16-bit PQ PNG, the form in which a file can be a data record: the two writers store the same codes, the two
readers read either, the levels the core works out are the reference's, and the declaration travels in the file."""
import numpy as np
import pytest

from udeep import Mapping, core, read_metadata, read_png_pq, write_png_pq
from udeep.export import pq16_codes, pq_decode, pq_encode
from udeep.metadata import data_steps, data_steps_pq16, rounding_to_noise

needs_core = pytest.mark.skipif(not (core.available() and core.png()), reason="the core was not built with its png feature")
STRETCH = {"kind": "linear", "lo": 0.0, "hi": 4095.0}


@pytest.fixture(autouse=True)
def core_back_on():
    """Each test may turn the core off: put back what was there before it, which is no core at all
    where none was built."""
    was = core.enabled()
    yield
    core.enable(was)


def picture(height=37, width=53):
    rng = np.random.default_rng(11)
    rgb = rng.uniform(0.0, 4.0, (height, width, 3))
    rgb[3, 4] = [0.0, 0.0, 0.0]; rgb[5, 6] = [4.0, 4.0, 4.0]; rgb[7, 8, 1] = np.nan
    return rgb


def codes_in(path):
    """The file's 16-bit codes, by a reader that is neither of the package's two: the bytes, straight."""
    import struct
    import zlib
    raw = path.read_bytes()
    at, parts, size = 8, [], None
    while at < len(raw):
        length, tag = struct.unpack(">I4s", raw[at:at + 8])
        if tag == b"IHDR":
            size = struct.unpack(">II", raw[at + 8:at + 16])
        elif tag == b"IDAT":
            parts.append(raw[at + 8:at + 8 + length])
        at += 12 + length
    rows = np.frombuffer(zlib.decompress(b"".join(parts)), dtype=np.uint8).reshape(size[1], 1 + size[0] * 6)
    assert not rows[:, 0].any()                                  # both writers leave the rows unfiltered
    return rows[:, 1:].copy().view(">u2").reshape(size[1], size[0], 3)


def test_pq_decode_is_the_inverse_of_pq_encode():
    nits = np.array([0.0, 0.005, 1.0, 100.0, 203.0, 812.0, 4000.0, 10000.0])
    assert np.allclose(pq_decode(pq_encode(nits)), nits, rtol=1e-9, atol=1e-9)


@needs_core
def test_the_two_writers_store_the_same_codes(tmp_path):
    rgb = picture()
    for name, on in (("core", True), ("reference", False)):
        core.enable(on)
        write_png_pq(rgb, tmp_path / f"{name}.png", xmp=b"<x:xmpmeta>a packet</x:xmpmeta>")
    a, b = codes_in(tmp_path / "core.png"), codes_in(tmp_path / "reference.png")
    assert np.array_equal(b, pq16_codes(rgb).astype(">u2"))
    # the core's sum of three products is taken in another order than NumPy's: a code may differ by one, rarely
    off = a.astype(int) - b.astype(int)
    assert np.abs(off).max() <= 1 and (off != 0).mean() < 1e-3
    for name in ("core", "reference"):
        raw = (tmp_path / f"{name}.png").read_bytes()
        assert b"cICP" in raw and b"XML:com.adobe.xmp" in raw and b"a packet" in raw


@pytest.mark.parametrize("writer", [True, False], ids=["core writes", "reference writes"])
@pytest.mark.parametrize("reader", [True, False], ids=["core reads", "reference reads"])
def test_either_reader_reads_either_writers_file(writer, reader, tmp_path):
    if (writer or reader) and not (core.available() and core.png()):
        pytest.skip("the core was not built with its png feature")
    rgb = picture()
    core.enable(writer); write_png_pq(rgb, tmp_path / "f.png")
    core.enable(reader); back = read_png_pq(tmp_path / "f.png")
    assert back.shape == rgb.shape and back.dtype == np.float64
    assert np.abs(back - np.nan_to_num(rgb)).max() < 6e-4 * 5       # a 16-bit PQ step, through the matrix


def test_a_png_of_another_kind_is_refused(tmp_path):
    from PIL import Image
    Image.fromarray(np.zeros((4, 5, 3), np.uint8)).save(tmp_path / "eight.png")
    for on in (True, False):
        if on and not (core.available() and core.png()):
            continue
        core.enable(on)
        with pytest.raises(ValueError, match="not a 16-bit PQ PNG"):
            read_png_pq(tmp_path / "eight.png")
    with pytest.raises(ValueError, match="height x width x 3"):
        write_png_pq(np.zeros((4, 5)), tmp_path / "flat.png")


def test_the_reference_reader_undoes_every_row_filter():
    from udeep.export import _unfilter
    rng = np.random.default_rng(5)
    pixels = rng.integers(0, 256, (6, 5 * 6), dtype=np.uint8)
    for kind in range(5):
        rows = np.zeros((6, 1 + 30), dtype=np.uint8)
        for y in range(6):
            for i in range(30):
                left = int(pixels[y, i - 6]) if i >= 6 else 0
                up = int(pixels[y - 1, i]) if y else 0
                corner = int(pixels[y - 1, i - 6]) if y and i >= 6 else 0
                p = left + up - corner
                paeth = left if abs(p - left) <= abs(p - up) and abs(p - left) <= abs(p - corner) else \
                    up if abs(p - up) <= abs(p - corner) else corner
                predicted = [0, left, up, (left + up) // 2, paeth][kind]
                rows[y, 0], rows[y, 1 + i] = kind, (int(pixels[y, i]) - predicted) & 255
        assert np.array_equal(_unfilter(rows), pixels), kind


@pytest.mark.parametrize(("cmap", "noise"), [("grey", ("constant", 3.0)), ("fire", ("poisson", 1.0)),
                                             ("ice", ("tolerance", 0.5)), ("grey", ("unknown", None))])
@needs_core
def test_the_core_finds_the_levels_the_reference_finds(cmap, noise):
    m = Mapping.from_stretch(STRETCH, 1000.0, 4.0, cmap)
    steps = data_steps_pq16(m, samples=1 << 18)
    found = core.levels_pq16(m, noise, samples=1 << 18)
    assert abs(found["levels"] - len(steps[1])) <= max(2, len(steps[1]) // 2000)     # a level at a rounding tie
    assert found["max_data_step"] == pytest.approx(float(steps[1].max()), rel=0.02)
    reference = rounding_to_noise(m, noise, steps=steps)
    if reference is None:
        assert found["worst"] is None
    else:
        assert found["worst"] == pytest.approx(min(reference["worst"], 1e9), rel=0.02)


def test_a_twelve_bit_frame_is_a_data_record_in_sixteen_bits_and_not_in_eight(tmp_path):
    import dataclasses

    from udeep import Declaration, to_xmp
    from udeep.metadata import digest_data
    from udeep.view import hdr_view
    rng = np.random.default_rng(2)
    data = np.clip(np.round(rng.uniform(0.0, 4095.0, (48, 64))), 0.0, 4095.0)
    view = hdr_view(data, "extend", 1000.0, STRETCH, 4.0, "grey")
    noise = ("constant", 3.0)
    fine, coarse = data_steps_pq16(view.mapping), data_steps(view.mapping, scaled_by=4.0)
    assert rounding_to_noise(view.mapping, noise, steps=fine)["meets"]
    assert not rounding_to_noise(view.mapping, noise, steps=coarse)["meets"]
    assert len(fine[1]) > 30_000 and len(coarse[1]) < 500 and fine[1].max() < 1.0 < coarse[1].max()
    declared = Declaration(**dataclasses.asdict(view.mapping), data_unit="counts", knee_rule="preset; white at 1,000 counts",
                           noise_model="constant", noise_parameter=3.0, max_data_step=float(fine[1].max()),
                           rounding_to_noise=rounding_to_noise(view.mapping, noise, steps=fine)["worst"],
                           record="data-record", data_digest=digest_data(data))
    write_png_pq(view.hdr, tmp_path / "frame.png", xmp=to_xmp(declared))
    assert read_metadata(tmp_path / "frame.png") == declared
    back = view.mapping.value(read_png_pq(tmp_path / "frame.png")[..., 1])            # grey: any channel is the light
    assert np.array_equal(np.round(back), data) and np.abs(back - data).max() <= fine[1].max() / 2 + 1e-9


# ---- the validator: a file that calls itself a data record must be one

def frame_file(tmp_path, noise=3.0, record=None, step=None, name="frame.png"):
    """A 12-bit frame as a 16-bit PQ PNG with its declaration; `record` and `step` override what is true."""
    import dataclasses

    from udeep import Declaration, to_xmp
    from udeep.metadata import digest_data
    from udeep.view import hdr_view
    data = np.clip(np.round(np.random.default_rng(4).uniform(0.0, 4095.0, (40, 52))), 0.0, 4095.0)
    view = hdr_view(data, "extend", 1000.0, STRETCH, 4.0, "grey")
    steps = data_steps_pq16(view.mapping)
    against = rounding_to_noise(view.mapping, ("constant", noise), steps=steps)
    declared = Declaration(**dataclasses.asdict(view.mapping), data_unit="counts", knee_rule="preset; white at 1,000 counts",
                           noise_model="constant", noise_parameter=noise, rounding_to_noise=against["worst"],
                           max_data_step=float(steps[1].max()) if step is None else step,
                           record=record or ("data-record" if against["meets"] else "rendering"), data_digest=digest_data(data))
    write_png_pq(view.hdr, tmp_path / name, xmp=to_xmp(declared))
    return tmp_path / name, data


def test_the_validator_confirms_a_true_data_record_and_reads_the_data_back(tmp_path):
    from udeep.validate import main, validate
    path, data = frame_file(tmp_path)
    report = validate(path, data=data)
    assert report.ok, str(report)
    assert [report.status(n) for n in ("metadata", "mapping", "headroom", "precision", "data link", "data read back")] == ["pass"] * 6
    assert "a data record, as declared" in str(report) and report.status("sdr base") == "skipped"
    assert validate(path).status("data read back") == "skipped" and validate(path).ok        # the file alone
    assert main([str(path)]) == 0


def test_the_validator_refuses_a_false_claim_to_be_a_data_record(tmp_path):
    from udeep.validate import main, validate
    path, data = frame_file(tmp_path, noise=0.2, record="data-record")      # steps of half a count, noise of a fifth
    report = validate(path, data=data)
    assert not report.ok and report.status("precision") == "fail"
    assert "declared a data record, but rounding is" in str(report) and "7.3.1" in str(report)
    assert main([str(path)]) == 1
    honest, _ = frame_file(tmp_path, noise=0.2, name="honest.png")          # the same file, calling itself a rendering
    assert validate(honest).ok and "declared a rendering" in str(validate(honest))


def test_the_validator_catches_a_wrong_step_and_data_that_is_not_the_files(tmp_path):
    from udeep.validate import validate
    path, data = frame_file(tmp_path, step=0.1)
    assert validate(path).status("precision") == "fail" and "MaxDataStep is stated as 0.1" in str(validate(path))
    path, data = frame_file(tmp_path)
    other = data.copy(); other[3, 4] += 40.0
    report = validate(path, data=other)
    assert report.status("data link") == "fail" and report.status("data read back") == "fail"
    assert validate(path, data=data[:10]).status("data read back") == "fail"                 # another shape


def test_the_validator_says_what_a_png_of_another_kind_is(tmp_path):
    from PIL import Image

    from udeep.validate import validate
    Image.fromarray(np.zeros((4, 5, 3), np.uint8)).save(tmp_path / "eight.png")
    report = validate(tmp_path / "eight.png")
    assert not report.ok and "carries no udeep metadata" in str(report)
    write_png_pq(np.ones((4, 5, 3)), tmp_path / "bare.png")                  # a PQ PNG that declares nothing
    assert not validate(tmp_path / "bare.png").ok
    (tmp_path / "nothing.bin").write_bytes(b"neither kind of file")
    assert "neither a gain-map JPEG nor a 16-bit PQ PNG" in str(validate(tmp_path / "nothing.bin")) or \
        "carries no udeep metadata" in str(validate(tmp_path / "nothing.bin"))
