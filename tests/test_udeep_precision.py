"""The data criterion (profile 7.2.1): rounding to the file's levels against the data's stated uncertainty."""
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from udeep import Mapping
from udeep import mpl as hp
from udeep.metadata import caption, data_steps, max_data_step, read_metadata, rounding_to_noise
from udeep.validate import validate

STRETCH = {"kind": "linear", "lo": 0.0, "hi": 100.0}
ARGS = (Mapping.from_stretch(STRETCH, 50.0, 2.0, "grey"),)


def figure(noise):
    d = np.full((40, 60), 20.0); d[10:20, 10:30] = 95.0
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)
    hp.imshow(ax, d, mode="extend", knee=50.0, stretch=STRETCH, cmap="grey", unit="counts",
              knee_rule="preset; half of full scale", noise=noise)
    return fig


def test_data_steps_tile_the_declared_range():
    x, width = data_steps(*ARGS)
    assert x[0] == 0.0 and np.isclose(x[-1] + width[-1], 100.0)
    assert np.allclose(x[1:], x[:-1] + width[:-1]) and np.all(width > 0.0)
    assert width.max() == max_data_step(*ARGS)


def test_constant_noise():
    # the widest level is 0.888 counts (see the metadata tests): 0.888 / sqrt(12) = 0.256 counts of rounding error
    r = rounding_to_noise(*ARGS, noise=("constant", 10.0))
    assert r["worst"] == pytest.approx(0.0256, abs=0.001) and r["limit"] == 0.1 and r["meets"]
    assert r["failing_fraction"] == 0.0
    r = rounding_to_noise(*ARGS, noise=("constant", 1.0))
    assert r["worst"] == pytest.approx(0.256, abs=0.01) and not r["meets"]
    assert 90.0 < r["at"] <= 100.0                              # the widest levels are at the top of the range


def test_relative_noise_fails_only_near_zero():
    # sigma = 0.1 x vanishes at zero, where the level width does not: the criterion must fail there, and only there
    r = rounding_to_noise(*ARGS, noise=("relative", 0.1))
    assert not r["meets"] and r["at"] == 0.0 and r["worst"] == np.inf
    assert 0.0 < r["failing_fraction"] < 0.03


def test_a_poisson_range_that_starts_at_zero_cannot_meet_the_criterion():
    # profile 7.2.1: sigma = sqrt(x) reaches zero at the bottom of the range, so no finite level is fine enough there
    r = rounding_to_noise(*ARGS, noise=("poisson", 1.0))
    assert not r["meets"] and r["worst"] == np.inf and r["at"] == 0.0
    _start, width = data_steps(*ARGS)
    assert r["failing_fraction"] == pytest.approx(width[0] / 100.0)          # the first level, and only that one


def test_noise_is_taken_where_it_is_smallest_in_each_level():
    """Poisson and relative noise grow away from zero, so each level is judged at its end nearer zero."""
    above = Mapping.from_stretch({"kind": "linear", "lo": 10.0, "hi": 100.0}, 50.0, 2.0, "grey")
    start, width = data_steps(above)
    one, four = (rounding_to_noise(above, ("poisson", p)) for p in (1.0, 4.0))
    ratio = width / np.sqrt(12.0) / np.sqrt(4.0 * start)                      # sigma = sqrt(parameter x value)
    assert four["worst"] == pytest.approx(ratio.max(), rel=1e-12) and four["at"] == start[np.argmax(ratio)]
    assert four["worst"] == pytest.approx(one["worst"] / 2.0, rel=1e-12) and four["meets"]
    r = rounding_to_noise(above, ("relative", 0.05))
    ratio = width / np.sqrt(12.0) / (0.05 * start)                            # sigma = parameter x |value|
    assert r["worst"] == pytest.approx(ratio.max(), rel=1e-12) and r["at"] == start[np.argmax(ratio)]
    assert r["failing_fraction"] == pytest.approx(width[ratio > 0.1].sum() / 90.0)

    below = Mapping.from_stretch({"kind": "linear", "lo": -100.0, "hi": -10.0}, -50.0, 2.0, "grey")
    start, width = data_steps(below)
    r = rounding_to_noise(below, ("relative", 0.05))                          # nearer zero is now the top of a level
    ratio = width / np.sqrt(12.0) / (0.05 * np.abs(start + width))
    assert r["worst"] == pytest.approx(ratio.max(), rel=1e-12) and r["at"] == (start + width)[np.argmax(ratio)]
    assert rounding_to_noise(below, ("poisson", 1.0))["failing_fraction"] == 1.0     # no counts below zero: sigma is 0

    across = Mapping.from_stretch({"kind": "linear", "lo": -5.0, "hi": 10.0}, 5.0, 2.0, "grey")
    r = rounding_to_noise(across, ("relative", 0.1))                          # zero lies inside one level
    assert r["worst"] == np.inf and r["at"] == 0.0 and not r["meets"]


def test_constant_noise_and_a_tolerance_are_reported_at_the_middle_of_the_worst_level():
    start, width = data_steps(*ARGS)
    for model, ratio in (("constant", width / np.sqrt(12.0) / 10.0), ("tolerance", 0.5 * width / 10.0)):
        r = rounding_to_noise(*ARGS, noise=(model, 10.0))
        worst = int(np.argmax(ratio))
        assert r["at"] == start[worst] + 0.5 * width[worst] and r["worst"] == pytest.approx(ratio[worst], rel=1e-12)


def test_a_tolerance_is_compared_with_the_largest_rounding_error():
    assert rounding_to_noise(*ARGS, noise=("tolerance", 0.5))["worst"] == pytest.approx(0.888, abs=0.02)   # 0.444 / 0.5
    assert rounding_to_noise(*ARGS, noise=("tolerance", 0.5))["limit"] == 1.0
    assert not rounding_to_noise(*ARGS, noise=("tolerance", 0.1))["meets"]


def test_unknown_noise_has_no_criterion():
    assert rounding_to_noise(*ARGS, noise=("unknown", None)) is None


def test_the_writer_records_it_and_warns_when_the_file_is_too_coarse(tmp_path):
    fig = figure(("constant", 1.0)); path = tmp_path / "f.jpg"
    with pytest.warns(hp.PrecisionWarning, match=r"0\.26 of the stated uncertainty"):
        hp.savefig_hdr(fig, path, headroom=2.0)
    d = read_metadata(path)
    assert d.rounding_to_noise == pytest.approx(0.256, abs=0.01) and d.record == "rendering"
    assert "0.26 of the stated uncertainty" in caption(d)
    r = validate(path)
    assert r.status("precision") == "pass" and "over the limit" in str(r)
    plt.close(fig)


def test_the_writer_is_quiet_when_the_criterion_is_met_or_does_not_apply(tmp_path):
    for noise in (("constant", 10.0), ("unknown", None)):
        fig = figure(noise)
        with warnings.catch_warnings():
            warnings.simplefilter("error", hp.PrecisionWarning)
            hp.savefig_hdr(fig, tmp_path / "f.jpg", headroom=2.0)
        d = read_metadata(tmp_path / "f.jpg")
        assert (d.rounding_to_noise is None) == (noise[0] == "unknown")
        plt.close(fig)


def test_the_validator_catches_a_wrong_figure(tmp_path):
    import re
    fig = figure(("constant", 10.0)); path = tmp_path / "f.jpg"
    hp.savefig_hdr(fig, path, headroom=2.0)
    raw = path.read_bytes()
    new, n = re.subn(rb'udeep:RoundingToNoise="[^"]*"', b'udeep:RoundingToNoise="0.001"', raw)
    assert n == 1
    path.write_bytes(new)
    assert validate(path).status("precision") == "fail"
    plt.close(fig)


def test_a_figure_of_counts_from_zero_is_recorded_as_far_over_the_limit(tmp_path):
    fig = figure(("poisson", 1.0)); path = tmp_path / "f.jpg"
    with pytest.warns(hp.PrecisionWarning, match=r"1e\+09 of the stated uncertainty"):
        hp.savefig_hdr(fig, path, headroom=2.0)
    d = read_metadata(path)
    assert d.rounding_to_noise == 1e9                               # unbounded; the file records a large finite number
    assert "more than 100 times the stated uncertainty" in caption(d)
    r = validate(path)
    assert r.status("precision") == "pass" and "over the limit" in str(r), str(r)
    plt.close(fig)
