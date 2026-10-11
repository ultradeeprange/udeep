"""Links from a file to its data and to its SDR figure of record: digests in the declared metadata."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest
from PIL import Image

from udeep import mpl as hp
from udeep.metadata import digest_data, digest_pixels, read_metadata
from udeep.validate import main, validate

STRETCH = {"kind": "linear", "lo": 0.0, "hi": 100.0}


def data():
    d = np.full((40, 60), 20.0); d[10:20, 10:30] = 95.0
    return d


def figure(d=None, **kw):
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)
    hp.imshow(ax, data() if d is None else d, mode="extend", knee=50.0, stretch=STRETCH, cmap="grey", unit="counts",
              knee_rule="preset; half of full scale", **kw)
    return fig


@pytest.fixture
def saved(tmp_path):
    fig = figure(source="doi:10.0000/example")
    hdr, sdr = tmp_path / "f.jpg", tmp_path / "f.png"
    fig.savefig(sdr, bbox_inches="tight", pad_inches=0.1)            # the ordinary figure: the figure of record
    hp.savefig_hdr(fig, hdr, headroom=2.0)
    yield fig, hdr, sdr
    plt.close(fig)


def test_data_digest_depends_on_values_and_shape_not_on_storage_type():
    d = data()
    assert digest_data(d) == digest_data(d.astype(np.float32)) == digest_data(d.tolist())    # 20 and 95 are exact in float32
    assert digest_data(d).startswith("sha256:") and len(digest_data(d)) == 7 + 64
    changed = d.copy(); changed[0, 0] += 1e-9
    assert digest_data(changed) != digest_data(d)
    assert digest_data(d.reshape(60, 40)) != digest_data(d)          # the same numbers in another shape
    nan = d.copy(); nan[0, 0] = np.nan
    other_nan = d.copy(); other_nan[0, 0] = -np.nan                  # every NaN counts as the same value
    assert digest_data(nan) == digest_data(other_nan) != digest_data(d)


def test_pixels_must_be_8_bit_before_they_are_digested():
    black = np.zeros((4, 4, 3), np.uint8)
    for other in (np.random.default_rng(0).random((4, 4, 3)), np.full((4, 4, 3), 256, np.uint16),
                  np.zeros((4, 4, 3), np.int8), np.zeros((4, 4, 3), bool), black.tolist()):
        with pytest.raises(ValueError, match="8-bit"):           # each used to share a digest with a black image
            digest_pixels(other)
    with pytest.raises(ValueError, match="H x W x 3"):
        digest_pixels(np.zeros((4, 4), np.uint8))
    assert digest_pixels(black) != digest_pixels(np.zeros((2, 8, 3), np.uint8))     # height x width, in that order
    assert digest_pixels(np.asfortranarray(black)) == digest_pixels(black)         # however the memory is laid out


def test_data_that_float64_cannot_hold_is_refused_not_cast():
    with pytest.raises(ValueError, match="real"):
        digest_data(np.array([1 + 2j, 3 + 4j]))                  # used to share a digest with [1.0, 3.0]
    with pytest.raises(ValueError, match="integers"):
        digest_data(np.array([2 ** 53, 2 ** 53 + 1]))            # used to share one with [2 ** 53, 2 ** 53]
    with pytest.raises(ValueError, match="integers"):
        digest_data(np.array([2 ** 64 - 1], np.uint64))
    with pytest.raises(ValueError, match="integers"):
        digest_data(np.array([-2 ** 63], np.int64))
    for other in (np.array(["1.0", "2.0"]), np.array([1.0, None], object), np.array([True, False]),
                  np.array(["2026-10-08"], "datetime64[D]")):
        with pytest.raises(ValueError, match="real numbers"):
            digest_data(other)
    whole = np.array([-2 ** 53, 0, 7, 2 ** 53])                  # every integer up to 2 ** 53 is exact in float64
    assert digest_data(whole) == digest_data(whole.astype(np.float64)) == digest_data(whole.tolist())
    assert digest_data(np.array([1, 2], np.uint8)) == digest_data([1.0, 2.0])


def test_the_validator_reports_data_it_cannot_digest(saved):
    _fig, hdr, _sdr = saved
    r = validate(hdr, data=data().astype(complex))
    assert r.status("data link") == "fail" and "real" in str(r)
    r = validate(hdr, sdr=hdr.with_name("missing.png"))
    assert r.status("sdr link") == "fail" and "missing.png" in str(r)
    assert r.status("data link") == "skipped"


def test_the_file_declares_its_data_and_its_sdr_figure(saved):
    _fig, hdr, sdr = saved
    d = read_metadata(hdr)
    assert d.data_digest == digest_data(data())
    assert d.data_source == "doi:10.0000/example"
    assert d.sdr_digest == digest_pixels(np.asarray(Image.open(sdr).convert("RGB")))


def test_the_validator_confirms_the_right_data_and_sdr_figure(saved):
    _fig, hdr, sdr = saved
    r = validate(hdr, data=data(), sdr=sdr)
    assert r.status("data link") == "pass" and r.status("sdr link") == "pass", str(r)
    assert validate(hdr).status("data link") == "skipped"            # nothing supplied to compare


def test_the_validator_rejects_other_data_and_another_figure(saved, tmp_path):
    _fig, hdr, _sdr = saved
    other = data(); other[5, 5] = 21.0
    assert validate(hdr, data=other).status("data link") == "fail"
    fig2 = figure(other); wrong = tmp_path / "other.png"
    fig2.savefig(wrong, bbox_inches="tight", pad_inches=0.1)
    r = validate(hdr, sdr=wrong)
    assert r.status("sdr link") == "fail" and not r.ok
    plt.close(fig2)


def test_validating_against_the_figure_checks_the_data_link_too(saved):
    fig, hdr, sdr = saved
    r = hp.validate(fig, hdr, sdr=sdr)
    assert all(c.status == "pass" for c in r.checks), str(r)
    fig2 = figure(data() + 1.0)
    assert hp.validate(fig2, hdr).status("data link") == "fail"
    plt.close(fig2)


def test_command_line_takes_the_sdr_figure(saved, capsys):
    _fig, hdr, sdr = saved
    assert main([str(hdr), "--sdr", str(sdr)]) == 0
    assert "sdr link" in capsys.readouterr().out
    assert main([str(hdr), "--sdr", str(hdr)]) == 1                  # the HDR file's own base is JPEG-coded: not the record
