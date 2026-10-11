"""The Rust core used from Python (udeep.core): the same results as the NumPy and Pillow code to the stated
tolerances, in use wherever it is found, and that code again when it is turned off or is not there. Skipped when
the library has not been built."""
import os
import subprocess
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from udeep import Mapping, core
from udeep import mpl as hp
from udeep.view import hdr_view

pytestmark = pytest.mark.skipif(not core.available(), reason="the core library is not built")

STRETCHES = [{"kind": "linear", "lo": -160.0, "hi": 700.0}, {"kind": "log", "lo": 0.5, "hi": 5000.0},
             {"kind": "asinh", "lo": 0.0, "hi": 100.0, "width": 5.0, "offset": 2.5}]
TIGHT = {"rtol": 1e-12, "atol": 1e-12}                   # float64 arithmetic: the golden vectors' own tolerance


@pytest.fixture(autouse=True)
def as_found_afterwards():
    assert core.enabled() and core.files()               # in use wherever it is found, and every test leaves it so
    yield
    core.enable(True)


def data(shape=(300, 400), seed=0):
    d = np.random.default_rng(seed).exponential(40.0, shape) - 5.0
    d[3, 7] = np.nan
    return d


@pytest.mark.parametrize("stretch", STRETCHES, ids=lambda s: s["kind"])
@pytest.mark.parametrize("cmap", ["grey", "fire", "ice", "fire/1"])
def test_the_core_gives_the_reference_light_and_colours(stretch, cmap):
    knee = stretch["lo"] + 0.3 * (stretch["hi"] - stretch["lo"]) + stretch.get("offset", 0.0)
    m = Mapping.from_stretch(stretch, knee, 4.0, cmap)
    d = data() * (stretch["hi"] / 100.0) + stretch.get("offset", 0.0)
    for sdr in (False, True):
        want = m.light(d, headroom=1.0) if sdr else m.light(d)
        got = core.light(m, d, sdr=sdr)
        assert got.shape == d.shape and got.dtype == np.float64
        assert np.allclose(got, want, equal_nan=True, **TIGHT) and np.isnan(got[3, 7])
        assert np.array_equal(core.light(m, d, sdr=sdr, threads=1), got, equal_nan=True)      # any number of threads
    light = m.light(d)
    wide, narrow = core.colours(m, light), core.colours(m, light, np.float32)
    assert wide.shape == narrow.shape == (*d.shape, 3) and wide.dtype == np.float64 and narrow.dtype == np.float32
    assert np.allclose(wide, m.colours(light), equal_nan=True, rtol=2e-6, atol=2e-6)
    assert np.array_equal(narrow, wide.astype(np.float32), equal_nan=True)
    assert np.array_equal(core.colours(m, light, threads=1), wide, equal_nan=True)


def test_the_core_takes_any_array_and_keeps_its_shape():
    m = Mapping.from_stretch(STRETCHES[0], 240.0, 4.0, "fire")
    d = data((64, 96))
    for x in (d[::2, ::3], d.T, d.astype(np.float32), d[5], np.float64(300.0), list(d[2, :9])):
        got = core.light(m, x)
        assert got.shape == np.shape(x) and np.allclose(got, m.light(np.asarray(x, dtype=np.float64)), equal_nan=True, **TIGHT)
        assert core.colours(m, got).shape == (*np.shape(x), 3)
    big = np.random.default_rng(1).uniform(-200.0, 800.0, core.SLAB + 1000)        # more than one slab of float32
    assert np.array_equal(core.colours(m, m.light(big), np.float32), core.colours(m, m.light(big)).astype(np.float32))


@pytest.mark.parametrize("mode", ["highlight", "extend"])
@pytest.mark.parametrize("float32", [False, True])
def test_a_view_made_with_the_core_is_the_reference_view(mode, float32):
    d = data(); mask = np.zeros(d.shape, bool); mask[40:44, 50:60] = True
    args = (d, mode, 30.0, STRETCHES[2], 4.0, "ice")
    fast = hdr_view(*args, mask=mask, float32=float32)
    core.enable(False)
    reference = hdr_view(*args, mask=mask, float32=float32)
    core.enable(True)
    assert fast.sdr.dtype == reference.sdr.dtype and fast.mapping == reference.mapping
    assert np.allclose(fast.y, reference.y, equal_nan=True, **TIGHT)
    for a, b in ((fast.sdr, reference.sdr), (fast.hdr, reference.hdr)):
        assert np.allclose(a, b, equal_nan=True, rtol=2e-6, atol=2e-6)
    assert (fast.masked, fast.derived_mask, fast.derived_masked) == (reference.masked, reference.derived_mask, reference.derived_masked)
    assert fast.above_white == reference.above_white and fast.above_white > 0.0


def test_the_core_is_used_wherever_it_is_found_and_not_once_turned_off(monkeypatch):
    calls = []
    real = core.light
    monkeypatch.setattr(core, "light", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    d = data((40, 50))
    hdr_view(d, "extend", 30.0, STRETCHES[2], 4.0, "fire")
    assert len(calls) == 2                                # the SDR light and the HDR light, with nothing asked for
    core.enable(False)
    hdr_view(d, "extend", 30.0, STRETCHES[2], 4.0, "fire")
    assert len(calls) == 2 and not core.enabled() and not core.files()
    core.enable(True)
    hdr_view(d, "extend", 30.0, STRETCHES[2], 4.0, "fire")
    assert len(calls) == 4


def test_without_the_core_the_package_works_and_asking_for_it_is_refused(monkeypatch):
    monkeypatch.setitem(core._state, "library", None)
    monkeypatch.setitem(core._state, "looked", True)
    monkeypatch.setitem(core._state, "on", None)          # as when the package is first used
    monkeypatch.setitem(core._state, "files", False)
    assert not core.available() and not core.enabled() and not core.files()
    view = hdr_view(data((40, 50)), "extend", 30.0, STRETCHES[2], 4.0, "fire")       # NumPy does the work
    assert view.above_white > 0.0
    with pytest.raises(RuntimeError, match="core library was not found"):
        core.enable()
    assert not core.enabled()
    core.enable(False)                                    # turning it off never needs it
    with pytest.raises(RuntimeError, match="not available"):
        core.light(Mapping.from_stretch(STRETCHES[0], 240.0, 4.0), np.zeros(3))


def test_a_figure_made_with_the_core_conforms_and_declares_the_same(tmp_path):
    d = 20.0 + 25.0 * np.random.default_rng(4).random((120, 160)); d[30:80, 40:110] = np.linspace(55.0, 99.0, 70)[None, :]
    declared = {}
    for fast in (True, False):
        core.enable(fast)
        fig, ax = plt.subplots(figsize=(4.2, 3.2), dpi=100)
        img = hp.imshow(ax, d, "extend", 50.0, {"kind": "linear", "lo": 0.0, "hi": 100.0}, 4.0, "fire", unit="K",
                        knee_rule="preset; half of the range", noise=("constant", 2.5))
        hp.colorbar(img, ax=ax)
        path = tmp_path / f"core{int(fast)}.jpg"
        hp.savefig_hdr(fig, path)
        report = hp.validate(fig, path)
        assert report.ok, str(report)
        declared[fast] = {k: v for k, v in vars(hp.declaration(fig)).items() if k != "sdr_digest"}
        plt.close(fig)
    core.enable(True)
    assert declared[True] == declared[False]


def test_a_file_reads_within_its_headroom_on_the_decoder_that_wrote_it(tmp_path):
    """The reason to write and read with one implementation: each writer holds its file exactly on its own JPEG
    decoder, and the other decoder reads the same file a few per cent differently."""
    from udeep.gainmap import read_gainmap_jpeg, write_gainmap_jpeg
    d = 20.0 + 25.0 * np.random.default_rng(3).random((72, 99)); d[20:50, 30:70] = 100.0; d[60:, 80:] = 100.0
    for cmap, headroom in (("fire", 4.0), ("ice", 8.0)):
        v = hdr_view(d, "extend", 50.0, {"kind": "linear", "lo": 0.0, "hi": 100.0}, headroom, cmap)
        peak = float(v.hdr.max())
        shown = {}
        for writer in (True, False):
            core.enable(writer)
            path = tmp_path / f"{cmap}{int(writer)}.jpg"
            write_gainmap_jpeg(v.sdr, v.hdr, path, headroom)
            for reader in (True, False):
                core.enable(reader)
                shown[writer, reader] = read_gainmap_jpeg(path, headroom)
                assert read_gainmap_jpeg(path, 1.0).max() <= 1.0 + 1e-6
        core.enable(True)
        assert shown[True, True].max() <= peak * (1 + 1e-6) and shown[False, False].max() <= peak * (1 + 1e-6)
        assert shown[True, True].dtype == np.float32 and shown[True, True].shape == v.hdr.shape
        assert max(shown[True, False].max(), shown[False, True].max()) <= headroom * 1.05      # the validator's limit
        block = shown[True, True][24:46, 34:66].max(-1) / v.hdr[24:46, 34:66].max(-1)
        assert np.median(block) > 0.96                    # held under the top, not thrown away


def test_the_cores_reader_leaves_other_files_to_the_python_one(tmp_path):
    from PIL import Image

    from udeep.gainmap import read_gainmap_jpeg
    plain = tmp_path / "plain.jpg"
    Image.fromarray(np.full((16, 16, 3), 128, np.uint8)).save(plain)
    assert core.read_gainmap(plain.read_bytes(), 4.0) is None and core.read_gainmap(b"not a jpeg", 4.0) is None
    with pytest.raises(ValueError, match="no gain-map container"):
        read_gainmap_jpeg(plain, 4.0)                     # the Python reader's own refusal, unchanged


def test_the_core_writer_refuses_what_the_python_one_refuses(tmp_path):
    from udeep.gainmap import write_gainmap_jpeg
    good = np.full((8, 8, 3), 0.5)
    path = tmp_path / "bad.jpg"
    cases = ((good, good[:4], 4.0, "H x W x 3"), (good + 1.0, good, 4.0, "must lie in"),
             (good, good, 1.0, "headroom must be > 1"), (good, np.full((8, 8, 3), np.inf), 4.0, "too large"))
    for sdr, hdr, headroom, message in cases:
        with pytest.raises(ValueError, match=message):
            write_gainmap_jpeg(sdr, hdr, path, headroom)
        assert not path.exists()


def test_the_environment_can_turn_the_core_off():
    code = "import udeep.core as c; print(c.enabled(), c.available())"
    env = {k: v for k, v in os.environ.items() if k != "UDEEP_USE_CORE"}
    assert subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True).stdout.split() == ["True", "True"]
    env["UDEEP_USE_CORE"] = "0"
    assert subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True).stdout.split() == ["False", "True"]


def themed_figure():
    d = 20.0 + 25.0 * np.random.default_rng(4).random((120, 160)); d[30:80, 40:110] = np.linspace(55.0, 99.0, 70)[None, :]
    fig, ax = plt.subplots(figsize=(4.2, 3.2), dpi=100)
    img = hp.imshow(ax, d, "extend", 50.0, {"kind": "linear", "lo": 0.0, "hi": 100.0}, 4.0, "fire", unit="K",
                    knee_rule="preset; half of the range", noise=("constant", 2.5))
    hp.colorbar(img, ax=ax)
    ax.set_title("renders"); ax.text(60, 50, "label", color="white")
    return fig


def test_the_core_makes_the_same_file_from_the_four_renders_as_from_the_two_figures(tmp_path):
    """Going straight from the renders is a shorter road to the same bytes: the combining rule of udeep.mpl is the
    one the core applies."""
    from udeep.gainmap import write_gainmap_jpeg
    fig = themed_figure()
    renders = hp._renders(fig, 4.0, True, 0.1)
    base, hdr = hp._combined(*renders, 4.0)
    data, lifted = core.write_from_renders(*renders, 4.0, description=b"")
    path = tmp_path / "two.jpg"
    write_gainmap_jpeg(base, hdr, path, 4.0)
    assert data == path.read_bytes()
    assert lifted == int(np.any(hdr > base, axis=-1).sum()) > 500
    # and a save is that file with its declaration, whichever way it was made
    hp.savefig_hdr(fig, tmp_path / "core.jpg")
    core.enable(False)
    hp.savefig_hdr(fig, tmp_path / "python.jpg")
    core.enable(True)
    from udeep.metadata import read_metadata
    assert read_metadata(tmp_path / "core.jpg") == read_metadata(tmp_path / "python.jpg")
    assert hp.validate(fig, tmp_path / "core.jpg").ok
    plt.close(fig)


def test_renders_that_do_not_match_are_refused():
    r = np.zeros((8, 8, 3), np.uint8)
    with pytest.raises(ValueError, match="H x W x 3"):
        core.write_from_renders(r, r[:4], r, r, 4.0)
    with pytest.raises(ValueError, match="headroom must be > 1"):
        core.write_from_renders(r, r, r, r, 1.0)
    data, lifted = core.write_from_renders(r + 200, r + 200, r + 200, r + 200, 4.0)      # nothing is an image alone
    assert lifted == 0 and data[:2] == b"\xff\xd8"
