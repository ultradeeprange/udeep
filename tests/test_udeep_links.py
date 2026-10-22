"""Links from a file to its data and to its SDR figure of record: digests in the declared metadata."""
import dataclasses
import hashlib
from pathlib import Path

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
ROOT = Path(__file__).resolve().parents[1]


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


# ---- the same links with SHA3-256 as well (profile 8.6, added 2026-10-22), so that they do not rest on one hash function

def test_the_profiles_worked_examples_are_what_each_hash_function_gives():
    """8.6's two examples, as the profile prints them, with SHA-256 and with SHA3-256; and the SHA3-256 one is the
    hash function itself over the bytes 8.6 sets out."""
    profile = (ROOT / "profile" / "udeep-figure-profile-v0.3.md").read_text(encoding="utf-8")
    one_row, white_black = [[1.0, -0.0, np.nan]], np.array([[[255, 255, 255], [0, 0, 0]]], np.uint8)
    for got in (digest_data(one_row), digest_data(one_row, "sha3-256"),
                digest_pixels(white_black), digest_pixels(white_black, "sha3-256")):
        assert f"`{got}`" in profile, got
    raw = b"udeep-data-1;1x3;" + bytes.fromhex("000000000000F03F" "0000000000000000" "000000000000F87F")
    assert digest_data(one_row, "sha3-256") == "sha3-256:" + hashlib.sha3_256(raw).hexdigest()
    assert digest_data(one_row, "sha3-256") != "sha3-256:" + hashlib.sha256(raw).hexdigest()


def test_a_digest_is_taken_only_with_a_hash_function_the_profile_names():
    with pytest.raises(ValueError, match="sha3-256"):
        digest_data(data(), "md5")
    with pytest.raises(ValueError, match="sha3-256"):
        digest_pixels(np.zeros((2, 2, 3), np.uint8), "sha3-512")


def test_the_file_declares_both_digests_of_its_data_and_of_its_sdr_figure(saved):
    fig, hdr, sdr = saved
    d = read_metadata(hdr)
    pixels = np.asarray(Image.open(sdr).convert("RGB"))
    assert d.data_digest == digest_data(data()) and d.data_digest_sha3 == digest_data(data(), "sha3-256")
    assert d.sdr_digest == digest_pixels(pixels) and d.sdr_digest_sha3 == digest_pixels(pixels, "sha3-256")
    assert d.data_digest_sha3.startswith("sha3-256:") and len(d.data_digest_sha3) == 9 + 64
    # the caption carries where the data is kept and both its digests (profile 9.4.5)
    assert (f"The data is kept at doi:10.0000/example, with the digests {d.data_digest} and {d.data_digest_sha3}."
            in hp.caption(fig))


def test_a_figure_of_several_images_combines_each_kind_of_digest_on_its_own(tmp_path):
    fig, (a, b) = plt.subplots(1, 2, figsize=(5, 3), dpi=60)
    for ax, d in ((a, data()), (b, data() * 0.5)):
        hp.imshow(ax, d, mode="extend", knee=50.0, stretch=STRETCH, cmap="grey", unit="counts",
                  knee_rule="preset; half of full scale")
    declared = hp.declaration(fig, 2.0)
    for name, field in (("sha256", "data_digest"), ("sha3-256", "data_digest_sha3")):
        joined = "|".join(digest_data(d, name) for d in (data(), data() * 0.5)).encode()
        assert getattr(declared, field) == f"{name}:" + hashlib.new(name.replace("-", "_"), joined).hexdigest()
    plt.close(fig)


def test_a_sha3_digest_that_differs_fails_the_link_though_the_sha256_one_matches(saved, tmp_path):
    """The control first: the file as written passes by both digests. Then the same file with only its SHA3-256
    digests changed, the bytes otherwise the same: each link fails, and says which digest."""
    _fig, hdr, sdr = saved
    r = validate(hdr, data=data(), sdr=sdr)
    assert r.status("data link") == "pass" and r.status("sdr link") == "pass"
    assert "by sha256 and sha3-256" in str(r)
    d = read_metadata(hdr)
    raw = hdr.read_bytes()
    for digest in (d.data_digest_sha3, d.sdr_digest_sha3):
        raw = raw.replace(digest.encode(), ("sha3-256:" + "0" * 64).encode())
    changed = tmp_path / "changed.jpg"
    changed.write_bytes(raw)
    r = validate(changed, data=data(), sdr=sdr)
    assert r.status("data link") == "fail" and r.status("sdr link") == "fail", str(r)
    assert "its sha3-256 digest is" in str(r)


def test_digests_given_as_text_are_each_compared_by_their_own_hash_function(saved):
    _fig, hdr, _sdr = saved
    d = read_metadata(hdr)
    wrong = "sha3-256:" + "0" * 64
    assert validate(hdr, data_digest=d.data_digest_sha3).status("data link") == "pass"
    assert validate(hdr, data_digest=[d.data_digest, d.data_digest_sha3]).status("data link") == "pass"
    assert validate(hdr, data_digest=wrong).status("data link") == "fail"
    assert validate(hdr, data_digest=[d.data_digest, wrong]).status("data link") == "fail"
    r = validate(hdr, data_digest=d.data_digest)                     # only one kind given: the other is not checked
    assert r.status("data link") == "pass" and "not checked: sha3-256" in str(r)
    assert validate(hdr, data_digest="md5:" + "0" * 32).status("data link") == "skipped"


def test_a_sha3_digest_without_its_sha256_partner_is_not_a_valid_declaration(saved):
    """Profile 8.5: each SHA3-256 digest is declared only with the SHA-256 one it goes with."""
    _fig, hdr, _sdr = saved
    d = read_metadata(hdr)
    with pytest.raises(ValueError, match="data_digest_sha3 is declared only with data_digest"):
        dataclasses.replace(d, data_digest=None)
    with pytest.raises(ValueError, match="sdr_digest_sha3 is declared only with sdr_digest"):
        dataclasses.replace(d, sdr_digest=None)
    assert dataclasses.replace(d, data_digest_sha3=None, sdr_digest_sha3=None).data_digest == d.data_digest
