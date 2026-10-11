"""Working memory: a figure must not need many times its data (project/performance-scope.md)."""
import tracemalloc

import numpy as np
import pytest

from udeep import Mapping
from udeep.scale import CHUNK, in_chunks
from udeep.view import hdr_view

STRETCH = {"kind": "asinh", "lo": 0.0, "hi": 100.0, "width": 5.0}


def peak_and_held(function):
    tracemalloc.start()
    kept = function()
    held, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak, held, kept


@pytest.mark.parametrize("cmap", ["grey", "fire"])
def test_a_view_needs_a_bounded_multiple_of_its_data(cmap):
    """Measured 2026-10-09 on a megapixel: about 80 bytes a pixel at the peak and 56 held (two float64 RGB
    renderings and the light). Before the mapping was worked through in pieces, the peak was 228."""
    d = np.random.default_rng(0).exponential(10.0, (1000, 1000))
    peak, held, _view = peak_and_held(lambda: hdr_view(d, "highlight", 30.0, STRETCH, 4.0, cmap))
    per_pixel = peak / d.size, held / d.size
    assert per_pixel[0] < 100.0 and per_pixel[1] < 60.0, per_pixel


def test_working_in_pieces_changes_no_value():
    """The whole point of in_chunks is that nothing changes: compare with the same functions applied whole."""
    rng = np.random.default_rng(1)
    x = rng.uniform(-10.0, 120.0, (3, CHUNK + 1234)); x[1, ::997] = np.nan      # larger than a chunk, with gaps
    for cmap in ("grey", "ice"):
        m = Mapping.from_stretch(STRETCH, 30.0, 4.0, cmap)
        position, light = m.position(x), m.light(x)
        pieces = [x[:, :50_000], x[:, 50_000:]]                                  # each small enough to go whole
        assert all(p.size <= CHUNK for row in pieces for p in row)
        def whole(f, pieces=pieces):
            return np.stack([np.concatenate([f(p[r]) for p in pieces]) for r in range(3)])

        assert np.array_equal(position, whole(m.position), equal_nan=True)
        assert np.array_equal(light, whole(m.light), equal_nan=True)
        assert np.array_equal(m.colours(light), whole(lambda part, m=m: m.colours(m.light(part))), equal_nan=True)
        assert m.colours(light).shape == (3, CHUNK + 1234, 3)


def test_in_chunks_leaves_small_and_foreign_arrays_alone():
    import array_api_strict as strict
    calls = []
    double = lambda a: (calls.append(1), a * 2.0)[1]
    assert np.array_equal(in_chunks(double, np.arange(10.0)), np.arange(10.0) * 2.0) and len(calls) == 1
    big = np.arange(float(2 * CHUNK + 5))
    calls.clear()
    assert np.array_equal(in_chunks(double, big), big * 2.0) and len(calls) == 3
    calls.clear()
    foreign = strict.asarray(big)
    assert type(in_chunks(double, foreign)) is type(foreign) and len(calls) == 1
    assert in_chunks(lambda a: [v * 2 for v in a], [1.0, 2.0]) == [2.0, 4.0]      # not an array at all


# ---- float32 renderings: an option, off unless asked for

@pytest.mark.parametrize("cmap", ["grey", "fire"])
def test_float32_renderings_halve_what_a_view_holds(cmap):
    """Measured 2026-10-10 on a megapixel: 32 bytes a pixel held (two float32 RGB renderings and the float64 light)
    against 56, and a lower peak."""
    d = np.random.default_rng(0).exponential(10.0, (1000, 1000))
    peak, held, view = peak_and_held(lambda: hdr_view(d, "highlight", 30.0, STRETCH, 4.0, cmap, float32=True))
    wide_peak, wide_held, wide = peak_and_held(lambda: hdr_view(d, "highlight", 30.0, STRETCH, 4.0, cmap))
    assert view.sdr.dtype == view.hdr.dtype == np.float32 and view.y.dtype == np.float64
    assert wide.sdr.dtype == wide.hdr.dtype == np.float64                          # the default is unchanged
    assert held / d.size < 34.0 < 54.0 < wide_held / d.size, (held / d.size, wide_held / d.size)
    assert peak < 0.8 * wide_peak, (peak / d.size, wide_peak / d.size)


def test_a_float32_rendering_is_the_float64_one_rounded_once():
    rng = np.random.default_rng(2)
    d = rng.exponential(10.0, (300, 400)); d[5, 5] = np.nan
    for cmap in ("grey", "fire", "ice"):
        narrow = hdr_view(d, "extend", 30.0, STRETCH, 4.0, cmap, float32=True)
        wide = hdr_view(d, "extend", 30.0, STRETCH, 4.0, cmap)
        for a, b in ((narrow.sdr, wide.sdr), (narrow.hdr, wide.hdr)):
            assert np.array_equal(a, b.astype(np.float32), equal_nan=True)         # not another calculation
            assert np.nanmax(np.abs(a.astype(np.float64) - b) / np.maximum(b, 1e-3)) < 1.2e-7
        assert np.array_equal(narrow.y, wide.y, equal_nan=True) and narrow.mapping == wide.mapping
        assert (narrow.above_white, narrow.masked, narrow.derived_masked) == (wide.above_white, wide.masked, wide.derived_masked)


def test_a_figure_made_with_float32_renderings_is_the_same_file_bar_rounding(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from udeep import mpl as hp
    from udeep.metadata import read_metadata
    d = 20.0 + 25.0 * np.random.default_rng(4).random((120, 160)); d[30:80, 40:110] = np.linspace(55.0, 99.0, 70)[None, :]
    files, bases = {}, {}
    for float32 in (False, True):
        fig, ax = plt.subplots(figsize=(4.2, 3.2), dpi=100)
        img = hp.imshow(ax, d, "extend", 50.0, {"kind": "linear", "lo": 0.0, "hi": 100.0}, 4.0, "fire", unit="K",
                        knee_rule="preset; half of the range", float32=float32)
        hp.colorbar(img, ax=ax)
        path = tmp_path / f"f{int(float32)}.jpg"
        hp.savefig_hdr(fig, path)
        report = hp.validate(fig, path)
        assert report.ok, str(report)
        files[float32] = read_metadata(path)
        bases[float32] = hp._compose(fig, 4.0, True, 0.1)
        plt.close(fig)
    # the declaration is the same but for the SDR figure's digest, which may differ where an 8-bit code rounds the
    # other way: that is why float32 is an option and not the default
    same = lambda m: {k: v for k, v in vars(m).items() if k != "sdr_digest"}
    assert same(files[True]) == same(files[False])
    for a, b in zip(bases[True], bases[False]):
        codes = np.abs(a - b) > 0
        assert codes.mean() < 0.01 and np.abs(a - b).max() < 0.05          # at most a pixel in a hundred, by one code

