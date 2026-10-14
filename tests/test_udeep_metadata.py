"""udeep.metadata: the profile's declared metadata (section 8) as XMP, the generated caption, MaxDataStep."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest
from PIL import Image

from udeep import Mapping, read_gainmap_jpeg, write_png_pq
from udeep import mpl as hp
from udeep.metadata import NAMESPACE, Declaration, caption, max_data_step, read_metadata, to_xmp

STRETCH = {"kind": "linear", "lo": 0.0, "hi": 100.0}


def declaration(**kw):
    base = {"data_unit": "HU", "noise_model": "constant", "noise_parameter": 10.0, "stretch": "linear", "range_low": -160.0,
                "range_high": 700.0, "stretch_width": 1.0, "stretch_offset": 0.0, "knee": 240.0,
                "knee_rule": "preset; top of the W400/L40 soft-tissue window", "above_knee": "hermite; exit=1",
                "reference_headroom": 2.0, "masked": 12, "max_data_step": 3.5, "record": "rendering", "preset": "extend"}
    return Declaration(**{**base, **kw})


def figure(knee=50.0, unit="counts", knee_rule="physical; half of full scale", **kw):
    d = np.full((40, 60), 20.0); d[10:20, 10:30] = 95.0
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)
    img = hp.imshow(ax, d, mode="extend", knee=knee, stretch=STRETCH, cmap="grey", unit=unit, knee_rule=knee_rule, **kw)
    return fig, ax, img


def test_xmp_round_trip(tmp_path):
    d = declaration()
    p = tmp_path / "packet.xmp"; p.write_bytes(to_xmp(d))
    assert NAMESPACE.encode() in p.read_bytes()
    assert read_metadata(p) == d


def test_text_is_escaped_in_xmp(tmp_path):
    d = declaration(data_unit='W m^-2 & "x" <y>')
    p = tmp_path / "packet.xmp"; p.write_bytes(to_xmp(d))
    assert read_metadata(p).data_unit == d.data_unit


def test_invalid_declarations_are_refused():
    with pytest.raises(ValueError, match="knee_rule"):
        declaration(knee_rule="because it looked good")
    with pytest.raises(ValueError, match="noise_model"):
        declaration(noise_model="gaussian")
    with pytest.raises(ValueError, match="noise_parameter"):
        declaration(noise_model="constant", noise_parameter=None)
    with pytest.raises(ValueError, match="knee"):
        declaration(knee=900.0)                              # outside the declared range
    with pytest.raises(ValueError, match="data_unit"):
        declaration(data_unit="")
    with pytest.raises(ValueError, match="record"):
        declaration(noise_model="unknown", noise_parameter=None, record="data-record")   # profile 7.3.1


@pytest.mark.parametrize("value, text", [
    (4.0, "4"), (1e16, "1e+16"), (1e-5, "1e-05"), (0.1, "0.1"), (1700000002.3456, "1700000002.3456"),
    (1e15, "1000000000000000"), (0.0001, "0.0001"), (-160.0, "-160"), (1 / 3, "0.3333333333333333"),
    (1.5e-7, "1.5e-07"), (123456789012345680.0, "1.2345678901234568e+17"), (-0.0, "-0"), (np.float64(2.5), "2.5")])
def test_a_float_is_written_as_the_shortest_text_that_reads_back(value, text):
    from udeep.metadata import float_text

    assert float_text(value) == text and float(text) == value


def test_a_float_that_is_not_finite_has_no_text():
    from udeep.metadata import float_text

    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError, match="finite"):
            float_text(bad)


@pytest.mark.parametrize("kw", [
    {"range_low": 1700000000.0, "range_high": 1700000002.3456, "knee": 1700000001.23456},     # Unix time, 2 s
    {"range_low": 1700000000.0, "range_high": 1700000000.2, "knee": 1700000000.1},            # and 0.2 s
    {"range_low": 2460000.5, "range_high": 2460000.50004, "knee": 2460000.50002},             # Julian date
    {"range_low": 0.0, "range_high": 1 / 3, "knee": 2 / 3, "stretch_offset": 1 / 3},          # the knee at the edge
    {"max_data_step": 1e-05, "noise_parameter": 1e16, "rounding_to_noise": 1.5e-07}])
def test_every_declared_number_survives_the_file_exactly(kw):
    """A range that is narrow beside the values themselves used to be rounded away by ten-digit text."""
    from udeep.metadata import parse

    d = declaration(**kw)
    back = parse(to_xmp(d))
    assert back == d
    x = np.linspace(d.range_low + d.stretch_offset, d.range_high + d.stretch_offset, 101)
    assert np.array_equal(back.mapping.light(x), d.mapping.light(x))


def test_the_writer_refuses_a_declaration_that_would_not_read_back(monkeypatch):
    from udeep import metadata

    with pytest.raises(ValueError, match="does not read back.*RangeHigh"):       # an integer no float64 holds
        to_xmp(declaration(range_low=0, range_high=2 ** 53 + 1, knee=2 ** 52))
    with pytest.raises(ValueError, match="does not read back"):
        metadata.description(declaration(data_unit="a\x00b"))                    # not a character XML can carry
    monkeypatch.setattr(metadata, "float_text", lambda v: f"{v:.10g}")           # the old ten-digit text
    with pytest.raises(ValueError, match="does not read back.*RangeHigh"):
        to_xmp(declaration(range_low=1700000000.0, range_high=1700000002.3456, knee=1700000001.23456))
    assert to_xmp(declaration())                                                 # ordinary numbers pass either way


@pytest.mark.parametrize("kw, message", [
    ({"version": "9.9"}, "version"), ({"image_state": "display-referred"}, "image_state"),
    ({"max_data_step": 0.0}, "max_data_step"), ({"max_data_step": -1.0}, "max_data_step"),
    ({"max_data_step": float("nan")}, "max_data_step"), ({"max_data_step": float("inf")}, "max_data_step"),
    ({"noise_parameter": 0.0}, "noise_parameter"), ({"noise_parameter": -2.0}, "noise_parameter"),
    ({"noise_parameter": float("inf")}, "noise_parameter"), ({"noise_parameter": float("nan")}, "noise_parameter"),
    ({"masked": -5}, "masked"), ({"derived_masked": -1, "derived_mask": "isolated-3x3"}, "derived_masked"),
    ({"derived_masked": 7}, "derived_mask"),
    ({"rounding_to_noise": float("nan")}, "rounding_to_noise"), ({"rounding_to_noise": -0.1}, "rounding_to_noise"),
    ({"rounding_to_noise": float("inf")}, "rounding_to_noise")])
def test_values_the_profile_fixes_or_that_mean_nothing_are_refused(kw, message):
    with pytest.raises(ValueError, match=message):
        declaration(**kw)


def test_values_at_the_edge_of_what_is_allowed_are_accepted():
    d = declaration(masked=0, derived_mask="isolated-3x3", derived_masked=0, rounding_to_noise=0.0, max_data_step=1e-300)
    assert (d.derived_masked, d.rounding_to_noise) == (0, 0.0)
    assert declaration(noise_model="unknown", noise_parameter=None).noise_parameter is None


def test_the_required_properties_a_packet_lacks_are_named():
    from udeep.metadata import missing_properties

    full = to_xmp(declaration(colour_map="grey"))
    assert missing_properties(full) == []
    assert missing_properties(to_xmp(declaration())) == ["ColourMap"]            # the helper above declares no map
    for name in ("Version", "ImageState", "NoiseModel", "Record", "Masked", "AboveKnee", "StretchWidth", "MaxDataStep"):
        assert full.count(f" udeep:{name}=".encode()) == 1
        assert missing_properties(full.replace(f" udeep:{name}=".encode(), f" udeep:X{name}=".encode())) == [name]
    assert missing_properties(to_xmp(declaration(noise_model="unknown", noise_parameter=None, preset=None,
                                                 colour_map="fire/1"))) == []     # optional ones may be absent
    assert missing_properties(b"<x:xmpmeta xmlns:x='adobe:ns:meta/'/>") is None   # no declaration at all


def test_savefig_hdr_writes_the_declaration(tmp_path):
    fig, _ax, _img = figure(noise=("poisson", 1.0))
    p = tmp_path / "f.jpg"
    with pytest.warns(hp.PrecisionWarning, match="over the 0.1 a data record needs"):    # counts from zero: sigma is 0 there
        hp.savefig_hdr(fig, p, headroom=2.0)
    d = read_metadata(p)
    assert (d.version, d.image_state) == ("0.3", "data-referred")
    assert (d.data_unit, d.noise_model, d.noise_parameter) == ("counts", "poisson", 1.0)
    assert (d.stretch, d.range_low, d.range_high, d.knee) == ("linear", 0.0, 100.0, 50.0)
    assert d.knee_rule == "physical; half of full scale"
    assert (d.above_knee, d.reference_headroom, d.masked) == ("hermite; exit=1", 2.0, 0)
    assert (d.record, d.preset) == ("rendering", "extend")     # an 8-bit gain-map JPEG is always a rendering
    assert d.max_data_step == pytest.approx(max_data_step(Mapping.from_stretch(STRETCH, 50.0, 2.0, "grey")))
    plt.close(fig)


def test_the_file_is_still_an_ordinary_jpeg_and_gain_map(tmp_path):
    fig, _ax, _img = figure()
    with_md, without = tmp_path / "a.jpg", tmp_path / "b.jpg"
    hp.savefig_hdr(fig, with_md, headroom=2.0); hp.savefig_hdr(fig, without, headroom=2.0, metadata=False)
    assert read_metadata(without) is None
    assert Image.open(with_md).size == Image.open(without).size
    for boost in (1.0, 2.0):                                   # the metadata changes no pixel
        assert np.array_equal(read_gainmap_jpeg(with_md, boost), read_gainmap_jpeg(without, boost))
    assert read_gainmap_jpeg(with_md, 2.0).max() > 1.5
    plt.close(fig)


def test_unit_and_knee_rule_must_be_declared(tmp_path):
    fig, _ax, _img = figure(unit=None)
    with pytest.raises(ValueError, match="unit"):
        hp.savefig_hdr(fig, tmp_path / "f.jpg")
    plt.close(fig)
    fig, _ax, _img = figure(knee_rule=None)
    with pytest.raises(ValueError, match="knee_rule"):
        hp.savefig_hdr(fig, tmp_path / "f.jpg")
    hp.savefig_hdr(fig, tmp_path / "f.jpg", metadata=False)    # an explicit opt-out still writes the figure
    plt.close(fig)


def test_a_percentile_knee_is_declared_exploratory(tmp_path):
    fig, _ax, _img = figure(knee=None, knee_rule=None)
    p = tmp_path / "f.jpg"; hp.savefig_hdr(fig, p)
    d = read_metadata(p)
    assert d.knee_rule.startswith("percentile; ")
    assert "exploratory" in caption(d)
    plt.close(fig)


def test_two_images_with_different_mappings_are_refused(tmp_path):
    fig, (a, b) = plt.subplots(1, 2)
    d = np.linspace(0, 100, 600).reshape(20, 30)
    hp.imshow(a, d, mode="extend", knee=50.0, stretch=STRETCH, cmap="grey", unit="counts", knee_rule="preset; a")
    hp.imshow(b, d, mode="extend", knee=60.0, stretch=STRETCH, cmap="grey", unit="counts", knee_rule="preset; b")
    with pytest.raises(ValueError, match="more than one"):
        hp.savefig_hdr(fig, tmp_path / "f.jpg")
    plt.close(fig)


def test_max_data_step_linear_grey():
    # y = x / 50 throughout (the Hermite segment is a straight line when its entry slope equals the secant).
    # Above the knee the HDR pass encodes y / 2 in 8-bit sRGB; the widest code is at the top, where the sRGB
    # slope is 1.055 / 2.4: dy = 2 / (255 * 0.4396) = 0.01784, so dx = 0.892.
    assert max_data_step(Mapping.from_stretch(STRETCH, 50.0, 2.0, "grey")) == pytest.approx(0.892, abs=0.02)


def test_max_data_step_follows_the_file_scaling_and_is_in_data_units():
    # The same 2x mapping stored in a file scaled for 4x: y / 4 at y = 2 has sRGB slope 0.4396 * 0.5 ** (1 / 2.4 - 1)
    # = 0.6586, so dy = 4 / (255 * 0.6586) = 0.02382 and dx = 1.191.
    assert max_data_step(Mapping.from_stretch(STRETCH, 50.0, 2.0, "grey"), scaled_by=4.0) == pytest.approx(1.191, abs=0.03)
    scaled = {"kind": "linear", "lo": 0.0, "hi": 1000.0}
    assert max_data_step(Mapping.from_stretch(scaled, 500.0, 2.0, "grey")) == pytest.approx(10 * max_data_step(Mapping.from_stretch(STRETCH, 50.0, 2.0, "grey")), rel=0.02)
    assert max_data_step(Mapping.from_stretch({"kind": "log", "lo": 1.0, "hi": 1e4}, 100.0, 2.0, "fire")) > 0.0


@pytest.mark.parametrize("value, text", [
    (16383.0, "16383"), (2625.5, "2625.5"), (123456.0, "123456"), (1234567.8, "1.23457e+06"), (-160.0, "−160"),
    (189.70123456789, "189.701"), (4.0, "4"), (0.0, "0"), (0.25, "0.25"), (0.000123456789, "0.000123457"),
    (0.0000123456, "1.23456e−05"), (25000000.0, "2.5e+07"), (0.001, "0.001"), (9999999.5, "1e+07")])
def test_caption_numbers_keep_six_significant_figures(value, text):
    from udeep.metadata import _number

    assert _number(value) == text


def test_the_caption_states_the_declared_range_top():
    d = declaration(stretch="asinh", range_low=0.0, range_high=16383.0, knee=2625.5, stretch_width=150.0)
    assert "from 0 to 16383 HU. White is at 2625.5 HU" in caption(d)              # was "16380" and "2626"


def test_caption_states_the_mapping():
    c = caption(declaration())
    for part in ("HU", "linear", "−160", "700", "240", "top of the W400/L40 soft-tissue window", "2 ×", "12 ", "3.5"):
        assert part in c, part
    assert "rendering" in c


def test_png_carries_the_declaration(tmp_path):
    p = tmp_path / "f.png"; d = declaration()
    write_png_pq(np.full((4, 5, 3), 1.5), p, xmp=to_xmp(d))
    assert read_metadata(p) == d
    assert Image.open(p).size == (5, 4)
    q = tmp_path / "g.png"; write_png_pq(np.full((4, 5, 3), 1.5), q)
    assert read_metadata(q) is None


def test_a_document_type_declaration_never_reaches_the_parser(tmp_path):
    """The validator reads files from anyone, and entity tricks start with a DOCTYPE (D24). Reading a file takes
    only the bytes of the packet itself, so a declaration before it is never parsed; handed to the parser directly,
    it is refused."""
    from udeep.metadata import parse

    hostile = b'<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">]>' + to_xmp(declaration())
    with pytest.raises(ValueError, match="document-type"):
        parse(hostile)
    p = tmp_path / "packet.xmp"; p.write_bytes(hostile)
    assert read_metadata(p) == declaration()


def test_a_unit_code_travels_beside_the_unit_text(tmp_path):
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)
    hp.imshow(ax, np.linspace(0, 100, 2400).reshape(40, 60), mode="extend", knee=50.0, stretch=STRETCH, cmap="grey",
              unit="brightness temperature (K)", unit_code="K", knee_rule="preset; half of full scale")
    p = tmp_path / "f.jpg"; hp.savefig_hdr(fig, p)
    d = read_metadata(p)
    assert (d.data_unit, d.data_unit_code) == ("brightness temperature (K)", "K")
    plt.close(fig)


def test_a_derived_mask_is_declared_with_its_rule_and_count(tmp_path):
    d = np.full((40, 60), 20.0); d[10:20, 10:30] = 95.0; d[30, 40] = 95.0; d[33, 45] = 95.0
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)
    hp.imshow(ax, d, mode="highlight", knee=50.0, stretch=STRETCH, cmap="grey", unit="counts",
              knee_rule="physical; half of full scale")
    p = tmp_path / "f.jpg"; hp.savefig_hdr(fig, p, headroom=2.0)
    m = read_metadata(p)
    assert (m.derived_mask, m.derived_masked, m.masked) == ("isolated-3x3", 2, 0)
    assert "2 isolated pixels above it are held at white" in caption(m)
    plt.close(fig)


def test_the_caption_never_shows_a_digit_the_number_does_not_have():
    from udeep.metadata import _number
    assert _number(16383.0) == "16383" and _number(999999.0) == "999999" and _number(0.001234567) == "0.00123457"
    assert _number(1234567.0) == "1.23457e+06"            # in full it would read 1234570: a zero that is not there
    assert _number(-160.0) == "\u2212160" and _number(0.0) == "0"


def test_fewer_than_two_sample_positions_are_taken_as_two():
    from udeep.metadata import data_steps
    m = Mapping.from_stretch(STRETCH, 50.0, 2.0, "grey")
    two = data_steps(m, samples=2)
    for n in (0, 1):
        got = data_steps(m, samples=n)
        assert np.array_equal(got[0], two[0]) and np.array_equal(got[1], two[1])


def test_the_worked_examples_of_profile_8_6_are_what_the_digests_give():
    """The profile's own examples, read from its text: an implementer checks against them first."""
    import re
    from pathlib import Path

    from udeep.metadata import digest_data, digest_pixels
    text = (Path(__file__).resolve().parents[1] / "profile" / "udeep-figure-profile-v0.3.md").read_text(encoding="utf-8")
    data_example = re.search(r"\*\*`DataDigest`, an example\.\*\*.*?`(sha256:[0-9a-f]{64})`", text, re.S).group(1)
    sdr_example = re.search(r"\*\*`SdrDigest`\.\*\*.*?`(sha256:[0-9a-f]{64})`", text, re.S).group(1)
    assert digest_data(np.array([[1.0, -0.0, np.nan]])) == data_example
    assert digest_pixels(np.array([[[255, 255, 255], [0, 0, 0]]], np.uint8)) == sdr_example
