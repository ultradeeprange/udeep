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

def frame_file(tmp_path, noise=3.0, record=None, step=None, name="frame.png", cmap="grey", named=True):
    """A 12-bit frame as a 16-bit PQ PNG with its declaration; `record` and `step` override what is true, and
    `named=False` leaves out the SdrDigest that the profile requires of it (9.1.2). Beside it, the SDR figure it
    names, as an 8-bit PNG (`<name>_sdr.png`)."""
    import dataclasses

    from udeep import Declaration, to_xmp, write_png_sdr
    from udeep.metadata import digest_data, digest_sdr
    from udeep.view import hdr_view
    data = np.clip(np.round(np.random.default_rng(4).uniform(0.0, 4095.0, (40, 52))), 0.0, 4095.0)
    view = hdr_view(data, "extend", 1000.0, STRETCH, 4.0, cmap)
    steps = data_steps_pq16(view.mapping)
    against = rounding_to_noise(view.mapping, ("constant", noise), steps=steps)
    declared = Declaration(**dataclasses.asdict(view.mapping), data_unit="counts", knee_rule="preset; white at 1,000 counts",
                           noise_model="constant", noise_parameter=noise, rounding_to_noise=against["worst"],
                           max_data_step=float(steps[1].max()) if step is None else step,
                           record=record or ("data-record" if against["meets"] else "rendering"), data_digest=digest_data(data),
                           sdr_digest=digest_sdr(view.sdr) if named else None)
    write_png_pq(view.hdr, tmp_path / name, xmp=to_xmp(declared))
    write_png_sdr(view.sdr, tmp_path / name.replace(".png", "_sdr.png"))
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


# ---- white (profile 9.1.1), the SDR figure a PNG names (9.1.2), and data read back through any colour map

@pytest.mark.parametrize("on", [True, False], ids=["core", "reference"])
def test_a_declared_file_is_written_with_white_at_203_and_no_other(on, tmp_path):
    if on and not (core.available() and core.png()):
        pytest.skip("the core was not built with its png feature")
    core.enable(on)
    from udeep import to_xmp
    path, _ = frame_file(tmp_path)
    xmp = to_xmp(read_metadata(path))
    with pytest.raises(ValueError, match="white at 203 cd/m2"):
        write_png_pq(np.ones((4, 5, 3)), tmp_path / "bright.png", sdr_white_nits=240.0, xmp=xmp)
    assert not (tmp_path / "bright.png").exists()
    write_png_pq(np.ones((4, 5, 3)), tmp_path / "mine.png", sdr_white_nits=240.0)   # no declaration: for one's own screen
    write_png_pq(np.ones((4, 5, 3)), tmp_path / "other.png", sdr_white_nits=240.0, xmp=b"<x:xmpmeta>another</x:xmpmeta>")
    assert np.allclose(read_png_pq(tmp_path / "mine.png", 240.0), 1.0, atol=1e-3)


@needs_core
def test_the_core_refuses_a_declaration_at_another_white_itself(tmp_path):
    """The C interface refuses it too, for a host that calls the core without the Python checks in front of it."""
    from udeep import to_xmp
    path, _ = frame_file(tmp_path)
    with pytest.raises(ValueError, match="the core refused"):
        core.write_png(np.ones((4, 5, 3)), 240.0, to_xmp(read_metadata(path)))
    assert core.write_png(np.ones((4, 5, 3)), 203.0, to_xmp(read_metadata(path)))[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_png_must_name_its_sdr_figure(tmp_path):
    from udeep.validate import validate
    path, data = frame_file(tmp_path, named=False)
    report = validate(path, data=data)
    assert not report.ok and report.status("metadata") == "fail" and "udeep:SdrDigest" in str(report)
    assert "9.1.2" in str(report) and report.status("data read back") == "pass"      # the rest is still checked


def test_the_sdr_figure_a_png_names_is_checked_by_its_pixels_or_by_its_own_declaration(tmp_path):
    from udeep import write_gainmap_jpeg
    from udeep.metadata import description
    from udeep.validate import main, validate
    path, data = frame_file(tmp_path)
    assert validate(path).status("sdr link") == "skipped"
    report = validate(path, sdr=tmp_path / "frame_sdr.png")                          # an 8-bit image: its pixels
    assert report.ok and report.status("sdr link") == "pass"
    d = read_metadata(path)
    from udeep.view import hdr_view
    view = hdr_view(data, "extend", 1000.0, STRETCH, 4.0, "grey")
    write_gainmap_jpeg(view.sdr, view.hdr, tmp_path / "frame.jpg", 4.0, description=description(d))
    report = validate(path, sdr=tmp_path / "frame.jpg")                              # the figure's JPEG: its declaration
    assert report.status("sdr link") == "pass" and "compared by the SdrDigest it declares" in str(report)
    assert main([str(path), "--sdr", str(tmp_path / "frame.jpg")]) == 0
    other, _ = frame_file(tmp_path, name="other.png", cmap="fire")                   # another figure's SDR
    assert validate(path, sdr=tmp_path / "other_sdr.png").status("sdr link") == "fail"
    assert validate(path, sdr=path).status("sdr link") == "fail"                     # the PNG holds no SDR figure
    # a JPEG that states this file's SdrDigest but is of another figure: its declaration is not taken on trust
    import dataclasses
    stranger = dataclasses.replace(read_metadata(other), sdr_digest=d.sdr_digest)
    write_gainmap_jpeg(view.sdr, view.hdr, tmp_path / "stranger.jpg", 4.0, description=description(stranger))
    report = validate(path, sdr=tmp_path / "stranger.jpg")
    assert report.status("sdr link") == "fail" and "declares another figure" in str(report)


def test_codes_come_back_exactly_from_the_light_either_reader_gives(tmp_path):
    """values_pq16 takes light, as read_png_pq gives it, and works out the file's codes again: they must be the
    codes in the file, every one."""
    path, _ = frame_file(tmp_path, cmap="fire")
    for on in (True, False):
        if on and not (core.available() and core.png()):
            continue
        core.enable(on)
        assert np.array_equal(pq16_codes(read_png_pq(path)), codes_in(path).astype(np.uint16))


@pytest.mark.parametrize("cmap", ["grey", "fire", "ice"])
def test_a_data_record_comes_back_as_data_whatever_its_colour_map(cmap, tmp_path):
    from udeep import read_png_data
    from udeep.validate import validate
    path, data = frame_file(tmp_path, cmap=cmap)
    back, d = read_png_data(path)
    assert d.record == "data-record" and d.colour_map.split("/")[0] == cmap
    assert np.abs(back - data).max() <= d.max_data_step / 2 * 1.01 and np.array_equal(np.round(back), data)
    report = validate(path, data=data)
    assert report.ok and report.status("data read back") == "pass" and f"colour map {d.colour_map}" in str(report)
    wrong = data.copy(); wrong[5, 6] += 2.0
    assert validate(path, data=wrong).status("data read back") == "fail"


def test_a_code_a_step_off_takes_the_nearest_level_and_one_far_off_is_no_value(tmp_path):
    from udeep.color import _SRGB_TO_BT2020
    from udeep.metadata import values_pq16
    path, data = frame_file(tmp_path, cmap="fire")
    d = read_metadata(path)
    codes = codes_in(path).astype(np.int64)
    codes[2, 3, 1] += 1                                          # as a writer that rounds differently might
    codes[4, 5] = [65535, 0, 0]                                  # a colour no data value is given
    light = np.einsum("ij,...j->...i", np.linalg.inv(_SRGB_TO_BT2020), pq_decode(codes / 65535.0) / 203.0)
    back = values_pq16(d.mapping, light)
    assert abs(back[2, 3] - data[2, 3]) <= d.max_data_step and np.isnan(back[4, 5])
    others = np.ones(data.shape, bool); others[2, 3] = others[4, 5] = False
    assert np.array_equal(np.round(back[others]), data[others])


def test_read_png_data_refuses_a_file_without_a_declaration(tmp_path):
    from udeep import read_png_data
    write_png_pq(np.ones((4, 5, 3)), tmp_path / "bare.png")
    with pytest.raises(ValueError, match="no udeep declaration"):
        read_png_data(tmp_path / "bare.png")


def test_digest_sdr_is_taken_over_the_codes_the_writers_store():
    from udeep.metadata import digest_pixels, digest_sdr
    white_then_black = np.array([[[1.0, 1.0, 1.0], [0.0, 0.0, 0.0]]])                # the example of profile 8.6
    assert digest_sdr(white_then_black) == "sha256:72f4b9b910f818d01b50d3c137ab5cc22cd48b94b1bb1bf6c69b85655751c7a1"
    sdr = np.random.default_rng(8).uniform(0.0, 1.0, (6, 7, 3)); sdr[1, 2, 0] = np.nan
    from udeep.export import srgb_oetf
    codes = np.round(srgb_oetf(np.nan_to_num(sdr)) * 255.0).astype(np.uint8)
    assert digest_sdr(sdr) == digest_pixels(codes)


def test_a_png_data_record_with_its_data_and_its_sdr_figure_is_reproduced(tmp_path):
    """The JPEG-only checks do not apply to a PNG and are left out of its levels, so it can be Reproduced."""
    from udeep.validate import validate
    path, data = frame_file(tmp_path)
    r = validate(path, data=data, sdr=tmp_path / "frame_sdr.png")
    assert {v.name: v.status for v in r.levels}["Reproduced"] == "pass", str(r)
    assert {v.name: v.status for v in validate(path).levels}["Reproduced"] == "unverified"
