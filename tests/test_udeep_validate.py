"""udeep.validate: a file checked against its own declaration (profile section 10)."""
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from udeep import mpl as hp
from udeep import write_gainmap_jpeg
from udeep.metadata import Declaration, declared_light, description
from udeep.tone import tone
from udeep.validate import main, validate

STRETCH = {"kind": "linear", "lo": 0.0, "hi": 100.0}
FILE_ONLY = ("metadata", "mapping", "headroom", "precision")
NEEDS_FIGURE = ("sdr base", "hdr rendering", "held pixels")


def data():
    yy, xx = np.mgrid[:60, :90]
    d = 20.0 + 25.0 * np.sin(xx / 9.0) * np.cos(yy / 7.0)          # below the knee
    d[20:40, 30:70] = np.linspace(55.0, 98.0, 40)[None, :]          # a block above it
    return d


def figure(knee=50.0, cmap="grey", mask=None, noise=("unknown", None)):
    fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
    img = hp.imshow(ax, data(), mode="extend", knee=knee, stretch=STRETCH, cmap=cmap, mask=mask, unit="counts",
                    knee_rule="preset; half of full scale", noise=noise)
    hp.colorbar(img, ax=ax, label="counts")
    ax.set_title("title")
    return fig


@pytest.fixture
def saved(tmp_path):
    fig = figure()
    path = tmp_path / "f.jpg"
    hp.savefig_hdr(fig, path, headroom=2.0)
    yield fig, path
    plt.close(fig)


def tamper(path, name, value):
    raw = path.read_bytes()
    new, n = re.subn(rb'udeep:' + name.encode() + rb'="[^"]*"', b'udeep:' + name.encode() + b'="' + value.encode() + b'"', raw)
    assert n == 1                                            # the edit must land
    path.write_bytes(new)


def test_a_written_file_conforms_as_far_as_the_file_alone_shows(saved):
    _fig, path = saved
    r = validate(path)
    assert r.ok, str(r)
    assert [r.status(n) for n in FILE_ONLY] == ["pass"] * 4, str(r)
    assert [r.status(n) for n in NEEDS_FIGURE] == ["skipped"] * 3
    assert "as far as checked" in str(r)
    assert r.declaration.colour_map == "grey"


def test_with_its_figure_every_point_is_checked(saved):
    fig, path = saved
    r = hp.validate(fig, path)
    assert r.ok and all(c.status == "pass" for c in r.checks), str(r)
    assert "as far as checked" not in str(r)


@pytest.mark.parametrize("cmap", ["fire", "ice"])
def test_colour_maps_conform_too(tmp_path, cmap):
    fig = figure(cmap=cmap); path = tmp_path / "f.jpg"
    hp.savefig_hdr(fig, path, headroom=2.0)
    r = hp.validate(fig, path)
    assert all(c.status == "pass" for c in r.checks), str(r)
    plt.close(fig)


def test_a_file_without_metadata_fails(tmp_path):
    fig = figure(); path = tmp_path / "f.jpg"
    hp.savefig_hdr(fig, path, headroom=2.0, metadata=False)
    r = validate(path)
    assert not r.ok and r.status("metadata") == "fail"
    plt.close(fig)


def test_an_invalid_declaration_fails(saved):
    _fig, path = saved
    tamper(path, "Record", "data-record")                    # with an unknown noise model (profile 7.3.1)
    r = validate(path)
    assert not r.ok and r.status("metadata") == "fail" and "data-record" in str(r)


def test_a_headroom_that_disagrees_with_the_gain_map_fails(saved):
    _fig, path = saved
    tamper(path, "ReferenceHeadroom", "4")
    assert validate(path).status("metadata") == "fail"


def test_an_iso_block_that_disagrees_with_the_xmp_fails(saved):
    import struct

    from udeep.gainmap import ISO_NS
    _fig, path = saved
    assert "ISO 21496-1 binary form" in str(validate(path))
    raw = bytearray(path.read_bytes())
    at = raw.rindex(ISO_NS) + len(ISO_NS) + 5 + 8             # the gain map's block: alternate headroom numerator
    assert struct.unpack(">I", raw[at:at + 4])[0] == 1_000_000     # log2(2) in millionths: the edit is in the right place
    raw[at:at + 4] = struct.pack(">I", 2_000_000)
    path.write_bytes(bytes(raw))
    r = validate(path)
    assert r.status("metadata") == "fail" and "ISO 21496-1" in str(r)


def test_a_flat_roll_off_is_not_a_conforming_mapping(saved):
    _fig, path = saved
    tamper(path, "AboveKnee", "hermite; exit=0")             # the BT.2390-style roll-off: slope zero at the top
    r = validate(path)
    assert r.status("mapping") == "fail" and "6.3" in str(r)
    tamper(path, "AboveKnee", "sigmoid")                     # a form this version does not define: not a valid record
    assert validate(path).status("metadata") == "fail"


def test_a_wrong_max_data_step_fails(saved):
    _fig, path = saved
    tamper(path, "MaxDataStep", "0.1")
    assert validate(path).status("precision") == "fail"


def test_an_unknown_colour_map_cannot_be_checked_for_precision(saved):
    _fig, path = saved
    tamper(path, "ColourMap", "viridis")
    r = validate(path)
    assert r.status("precision") == "skipped" and r.ok
    said = detail(r, "metadata")
    assert "complete" not in said and "'viridis'" in said, said      # a map this reader does not know: it cannot tell


def detail(report, name):
    return next(c.detail for c in report.checks if c.name == name)


def rename(path, name):
    """Take one property out of the file's declaration, by giving it a name the profile does not have."""
    raw = path.read_bytes()
    assert raw.count(b" udeep:" + name.encode() + b"=") == 1
    path.write_bytes(raw.replace(b" udeep:" + name.encode() + b"=", b" udeep:X" + name.encode() + b"="))


def written(tmp_path, headroom=2.0, **kw):
    """A small gain-map JPEG carrying whatever declaration is asked for."""
    sdr = np.full((32, 32, 3), 0.5); hdr = sdr.copy(); hdr[8:24, 8:24] = 1.5
    sdr[8:24, 8:24] = 1.0
    base = {"data_unit": "counts", "stretch": "linear", "range_low": 0.0, "range_high": 100.0, "knee": 50.0,
            "knee_rule": "preset; x", "reference_headroom": headroom, "max_data_step": 1.0, "colour_map": "grey"}
    path = tmp_path / "f.jpg"
    write_gainmap_jpeg(sdr, hdr, path, headroom=headroom, description=description(Declaration(**{**base, **kw})))
    return path


def with_noise(tmp_path):
    fig = figure(noise=("constant", 10.0)); path = tmp_path / "f.jpg"
    hp.savefig_hdr(fig, path, headroom=2.0)
    return fig, path


@pytest.mark.parametrize("name", ["ColourMap", "Version", "ImageState", "NoiseModel", "Record", "Masked", "AboveKnee",
                                  "StretchWidth", "StretchOffset", "DerivedMasked"])
def test_a_required_property_that_is_absent_fails(saved, name):
    """Profile 8.1: every stored figure MUST carry these. A reader's default is not the file's statement."""
    _fig, path = saved
    rename(path, name)
    r = validate(path)
    assert not r.ok and r.status("metadata") == "fail" and f"udeep:{name}" in detail(r, "metadata"), str(r)
    assert "complete" not in detail(r, "metadata")


def test_an_optional_property_may_be_absent(saved):
    _fig, path = saved
    for name in ("Preset", "DataDigest", "SdrDigest"):
        rename(path, name)
    r = validate(path)
    assert r.ok and "complete and consistent" in detail(r, "metadata"), str(r)


@pytest.mark.parametrize("name, value", [("Version", "9.9"), ("ImageState", "display-referred"), ("Masked", "-5"),
                                         ("DerivedMasked", "7"), ("MaxDataStep", "0"), ("MaxDataStep", "nan"),
                                         ("MaxDataStep", "-0.5"), ("MaxDataStep", "inf")])
def test_a_property_with_a_value_the_profile_does_not_allow_fails(saved, name, value):
    _fig, path = saved
    tamper(path, name, value)
    r = validate(path)
    assert not r.ok and r.status("metadata") == "fail", str(r)


def test_a_registered_map_at_an_unregistered_headroom_must_carry_its_table(tmp_path):
    r = validate(written(tmp_path, headroom=3.0, colour_map="fire/1"))            # profile 6.10 and 8.1
    assert r.status("metadata") == "fail" and "ColourMapTable" in detail(r, "metadata"), str(r)


def test_a_carried_table_that_cannot_be_read_fails(tmp_path):
    for table in ("abc;AAAA", "0;", "4;AAAA"):
        r = validate(written(tmp_path, colour_map="mymap/7", colour_map_table=table))
        assert r.status("metadata") == "fail" and "ColourMapTable cannot be read" in detail(r, "metadata"), str(r)


def test_a_gain_map_jpeg_is_never_a_data_record(tmp_path):
    """Profile 7.3.3. The claim needs no colour map to refuse, so an unknown map must not shelter it."""
    fig, path = with_noise(tmp_path)
    plt.close(fig)
    assert validate(path).status("precision") == "pass"
    tamper(path, "Record", "data-record")
    r = validate(path)
    assert not r.ok and r.status("metadata") == "pass" and r.status("precision") == "fail", str(r)
    assert "7.3.3" in detail(r, "precision")
    tamper(path, "ColourMap", "mymap/7")
    r = validate(path)
    assert not r.ok and r.status("precision") == "fail" and "7.3.3" in detail(r, "precision"), str(r)


@pytest.mark.parametrize("stated, field", [("MaxDataStep", "max_data_step"), ("RoundingToNoise", "rounding_to_noise")])
def test_a_stated_precision_that_is_not_a_number_fails(tmp_path, stated, field):
    fig, path = with_noise(tmp_path)
    plt.close(fig)
    tamper(path, stated, "nan")
    r = validate(path)                                               # used to pass, with the words "as stated"
    assert not r.ok and r.status("metadata") == "fail" and field in detail(r, "metadata"), str(r)


@pytest.mark.parametrize("field, value", [("max_data_step", float("nan")), ("rounding_to_noise", float("nan")),
                                          ("rounding_to_noise", float("inf")), ("max_data_step", 0.0)])
def test_point_6_fails_on_a_number_that_cannot_be_compared(tmp_path, monkeypatch, field, value):
    """The Declaration refuses these first; the check itself must not pass them if they ever reach it."""
    import udeep.validate as module
    from udeep.metadata import parse

    fig, path = with_noise(tmp_path)
    plt.close(fig)

    def forged(xmp):
        d = parse(xmp)
        object.__setattr__(d, field, value)
        return d

    monkeypatch.setattr(module, "parse", forged)
    r = validate(path)
    assert not r.ok and r.status("precision") == "fail", str(r)


def replace_bytes(path, old, new):
    raw = path.read_bytes()
    assert raw.count(old) == 1
    path.write_bytes(raw.replace(old, new))


@pytest.mark.parametrize("old, new, check, said", [
    (b'hdrgm:HDRCapacityMax="1"', b'hdrgm:HDRCapacityMax="."', "metadata", "HDRCapacityMax"),
    (b'hdrgm:HDRCapacityMax="1"', b'hdrgm:HDRCapacityMax="9e99"', "metadata", "HDRCapacityMax"),
    (b'Item:Semantic="GainMap"', b'Item:Semantic="GainMaq"', "headroom", "gain map"),
    (b'hdrgm:GainMapMax="', b'hdrgm:GainMapMaq="', "headroom", "gain map"),
    (b'udeep:Masked="0"', b'udeep:Masked="x"', "metadata", "not valid"),
    (b'udeep:AboveKnee="hermite; exit=1"', b'udeep:AboveKnee="hermite; exit=5"', "metadata", "not valid"),
    (b'udeep:AboveKnee="hermite; exit=1"', b'udeep:AboveKnee="hermite; exit=1.2.3"', "metadata", "not valid"),
    (b'udeep:StretchWidth="1"', b'udeep:StretchWidth="inf"', "metadata", "not valid"),
    (b'udeep:NoiseParameter="10"', b'udeep:NoiseParameter="inf"', "metadata", "not valid")])
def test_the_validator_reports_a_file_it_cannot_read_and_never_raises(tmp_path, old, new, check, said):
    """Files come from anyone (D24): whatever cannot be read is a failed check that says what, not a traceback."""
    fig, path = with_noise(tmp_path)
    replace_bytes(path, old, new)
    r = validate(path)
    assert not r.ok and r.status(check) == "fail" and said in detail(r, check), str(r)
    if said != "not valid":                                          # hp.validate reads the declaration first, and that raises
        r = hp.validate(fig, path)                                   # with the figure: the points that need pixels fail too
        assert not r.ok and r.status(check) == "fail", str(r)
        assert check == "metadata" or [r.status(n) for n in NEEDS_FIGURE] == ["fail"] * 3, str(r)
    plt.close(fig)


def test_a_file_that_is_missing_or_is_not_an_image_is_reported(saved, tmp_path, capsys):
    _fig, path = saved
    r = validate(tmp_path / "absent.jpg")
    assert not r.ok and r.status("metadata") == "fail" and "cannot be read" in detail(r, "metadata")
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(path.read_bytes()[:20_000])                   # the declaration is there; the gain map is not
    r = validate(broken)
    assert not r.ok and r.declaration is not None, str(r)
    assert main([str(tmp_path / "absent.jpg"), str(broken), str(path)]) == 1     # one bad file does not stop the run
    out = capsys.readouterr().out
    assert "absent.jpg: DOES NOT CONFORM" in out and "f.jpg: conforms" in out


def test_an_error_in_a_check_itself_is_a_failed_check(saved, monkeypatch):
    import udeep.validate as module

    def broken(*_a, **_k):
        raise ZeroDivisionError("float division by zero")

    _fig, path = saved
    monkeypatch.setattr(module, "data_steps", broken)
    r = validate(path)
    assert r.status("precision") == "fail" and "ZeroDivisionError" in detail(r, "precision"), str(r)
    assert [r.status(n) for n in ("metadata", "mapping", "headroom")] == ["pass"] * 3     # the others still ran


def test_values_over_the_headroom_fail(tmp_path):
    sdr = np.full((32, 32, 3), 0.5); hdr = sdr.copy(); hdr[8:24, 8:24] = 3.0
    sdr[8:24, 8:24] = 1.0
    d = Declaration(data_unit="counts", stretch="linear", range_low=0.0, range_high=100.0, knee=50.0,
                    knee_rule="preset; x", reference_headroom=2.0, max_data_step=1.0, colour_map="grey")
    path = tmp_path / "f.jpg"
    write_gainmap_jpeg(sdr, hdr, path, headroom=2.0, description=description(d))
    r = validate(path)
    assert r.status("headroom") == "fail", str(r)


def test_a_file_from_a_different_mapping_fails_against_the_figure(saved):
    _fig, path = saved
    other = figure(knee=70.0)                                # the same data with white somewhere else
    r = hp.validate(other, path)
    assert r.status("sdr base") == "fail", str(r)
    plt.close(other)


def test_lifted_pixels_that_follow_another_curve_fail_the_hdr_rendering(saved):
    """Point 4: where the figure lifts, the file must show the declared light, not just some light above white."""
    _fig, path = saved
    other = figure(knee=70.0)                                # between 70 and 100 both lift, by different amounts
    r = hp.validate(other, path)
    assert r.status("hdr rendering") == "fail" and "median error" in detail(r, "hdr rendering"), str(r)
    plt.close(other)


def test_a_lifted_pixel_that_should_be_held_fails(saved):
    _fig, path = saved                                       # saved without a mask
    mask = np.zeros(data().shape, bool); mask[20:40, 30:70] = True
    masked = figure(mask=mask)                               # the figure says: this block is invalid, hold it at white
    r = hp.validate(masked, path)
    assert r.status("held pixels") == "fail", str(r)
    assert r.status("hdr rendering") == "pass"               # what the figure does lift is still drawn correctly
    plt.close(masked)


def test_the_data_criterion_is_reported_for_constant_noise(tmp_path):
    fig = figure(noise=("constant", 10.0)); path = tmp_path / "f.jpg"
    hp.savefig_hdr(fig, path, headroom=2.0)
    r = validate(path)
    detail = next(c.detail for c in r.checks if c.name == "precision")
    assert "0.026 of the stated uncertainty at its worst, within" in detail, detail      # 0.888 / sqrt(12) / 10
    plt.close(fig)


def test_declared_light_is_the_curve_the_library_draws():
    d = Declaration(data_unit="1", stretch="asinh", range_low=0.0, range_high=20.0, stretch_width=0.1, knee=1.2,
                    knee_rule="physical; x", reference_headroom=4.0, max_data_step=1.0)
    x = np.linspace(0.0, 20.0, 2001)
    t = np.arcsinh(x / 0.1) / np.arcsinh(20.0 / 0.1)
    knee_t = float(np.arcsinh(1.2 / 0.1) / np.arcsinh(20.0 / 0.1))
    assert np.allclose(declared_light(d, x), tone(t, knee_t, 4.0), rtol=2e-6, atol=2e-6)


def test_command_line_exit_status(saved, capsys):
    _fig, path = saved
    assert main([str(path)]) == 0
    assert "conforms" in capsys.readouterr().out
    tamper(path, "MaxDataStep", "0.1")
    assert main([str(path)]) == 1
    assert main([]) == 2


def test_a_figure_at_an_unregistered_headroom_carries_its_colour_map(tmp_path):
    """Registered maps ship tables for headrooms 2, 4, 8 and 16; any other headroom puts the table in the file."""
    from udeep.cmap import Table
    from udeep.cmap import table as shipped
    from udeep.metadata import read_metadata

    d = data()
    for headroom, carried in ((4.0, False), (3.0, True)):
        fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
        hp.imshow(ax, d, mode="extend", knee=50.0, stretch=STRETCH, cmap="fire", headroom=headroom, unit="counts",
                  knee_rule="preset; half of full scale")
        path = tmp_path / f"h{headroom:g}.jpg"
        hp.savefig_hdr(fig, path)
        m = read_metadata(path)
        assert m.colour_map == "fire/2" and (m.colour_map_table is not None) == carried
        if carried:
            assert Table.from_text("fire", 3.0, m.colour_map_table).digest == shipped("fire", 3.0).digest
        r = hp.validate(fig, path)
        assert all(c.status == "pass" for c in r.checks), str(r)
        plt.close(fig)
