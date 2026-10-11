"""udeep.tone: t -> y (multiples of SDR white)."""
import numpy as np
import pytest

from udeep import knee_at_percentile, tone

T = np.linspace(0.0, 1.0, 1001)


def test_sdr_is_identity():
    assert np.allclose(tone(T, knee=1.0, headroom=1.0), T)


def test_anchor_points():
    assert np.allclose(tone([0.0, 0.8, 1.0], knee=0.8, headroom=4.0), [0.0, 1.0, 4.0])


def test_monotone_and_bounded():
    y = tone(T, knee=0.9, headroom=5.0)
    assert np.all(np.diff(y) >= 0.0) and y.max() <= 5.0 + 1e-6


def test_below_knee_stays_sdr():
    y = tone(T, knee=0.9, headroom=5.0)
    assert np.all(y[T <= 0.9] <= 1.0 + 1e-6)


def test_below_knee_identical_to_sdr_clipped_at_the_same_white():
    # The fair SDR baseline (four-lens review, 2026-09-26): HDR may differ from it only above white.
    knee = 0.7
    hdr, sdr_clipped = tone(T, knee, 2.0), tone(T, knee, 1.0)
    assert np.allclose(sdr_clipped, np.minimum(T / knee, 1.0), atol=1e-6)
    assert np.allclose(hdr[T <= knee], sdr_clipped[T <= knee], atol=1e-6)


@pytest.mark.parametrize(("knee", "headroom"), [(0.9, 2.0), (0.8, 4.0), (0.5, 2.0)])
def test_no_kink_at_the_knee(knee, headroom):
    # C1 shoulder (ITU-R BT.2390-style Hermite): slope continuous across the knee, no Mach band.
    # One-sided slopes by Richardson extrapolation (2*D(h) - D(2h)): the shoulder curves hard just
    # past the knee, so a plain forward difference is biased by ~y''h/2 (about 3 % at knee 0.9).
    h = 2e-4
    y = tone(np.array([knee - 2 * h, knee - h, knee, knee + h, knee + 2 * h]), knee, headroom).astype(np.float64)
    left = 2 * (y[2] - y[1]) / h - (y[2] - y[0]) / (2 * h)
    right = 2 * (y[3] - y[2]) / h - (y[4] - y[2]) / (2 * h)
    assert right == pytest.approx(left, rel=0.02)


SHOULDERS = [(0.9, 2.0), (0.8, 4.0), (0.5, 2.0), (0.2, 1.2), (0.199, 2.0), (0.95, 8.0)]


def _alpha_secant(knee, headroom):
    secant = (headroom - 1.0) / (1.0 - knee)
    return min(1.0 / knee, 3.0 * secant) / secant, secant


@pytest.mark.parametrize(("knee", "headroom"), SHOULDERS)
def test_declared_mapping_is_strictly_increasing_above_the_knee(knee, headroom):
    # Profile v0.3, 6.3: the declared mapping must be invertible, so its slope may never reach zero.
    # With entry slope alpha and exit slope 1 (units of the secant) the Hermite slope is
    # (3a-3)u^2 + (4-4a)u + a, whose minimum on [0, 1] is min(a, (4-a)/3) -- never below 1/3 as a <= 3.
    alpha, secant = _alpha_secant(knee, headroom)
    t = np.linspace(knee, 1.0, 4001)       # coarser would average the slope away; finer drowns in float32 rounding
    slope = np.diff(tone(t, knee, headroom).astype(np.float64)) / np.diff(t)
    bound = secant * min(alpha, (4.0 - alpha) / 3.0)
    assert slope.min() > 0.0
    assert 0.97 * bound <= slope.min() <= 1.15 * bound     # interval means sit at or just above the point minimum


@pytest.mark.parametrize(("knee", "headroom"), [(0.8, 4.0), (0.5, 2.0), (0.9, 2.0)])
def test_shoulder_ends_at_the_secant_slope(knee, headroom):
    h = 1e-3
    y = tone(np.array([1.0 - 2 * h, 1.0 - h, 1.0]), knee, headroom).astype(np.float64)
    end = 2 * (y[2] - y[1]) / h - (y[2] - y[0]) / (2 * h)          # Richardson, one-sided
    assert end == pytest.approx((headroom - 1.0) / (1.0 - knee), rel=0.02)


def test_rolloff_form_is_flat_at_the_top():
    # exit_slope=0 is the BT.2390-style roll-off: for display rendering only, not a declared mapping.
    h = 1e-3
    y = tone(np.array([1.0 - h, 1.0]), 0.8, 4.0, exit_slope=0.0).astype(np.float64)
    assert (y[1] - y[0]) / h < 0.05 * (4.0 - 1.0) / (1.0 - 0.8)


def test_rolloff_form_reproduces_the_v02_curve():
    # v0.2 shoulder at (0.8, 4.0), from the golden vectors recorded before the change.
    ts = np.linspace(0.8 - 0.05, 1.0, 26)
    w, m0 = 0.2, min(1 / 0.8, 3 * 3.0 / 0.2)
    u = np.clip((ts - 0.8) / w, 0, 1)
    v02 = np.where(ts <= 0.8, ts / 0.8, (2*u**3 - 3*u**2 + 1) + (u**3 - 2*u**2 + u) * w * m0 + (-2*u**3 + 3*u**2) * 4.0)
    assert np.allclose(tone(ts, 0.8, 4.0, exit_slope=0.0), v02, atol=1e-6)


@pytest.mark.parametrize("exit_slope", [-0.1, 3.1])
def test_rejects_exit_slopes_that_break_monotonicity(exit_slope):
    with pytest.raises(ValueError):
        tone(T, 0.8, 4.0, exit_slope=exit_slope)


def test_sdr_clip_is_unchanged_by_the_exit_slope():
    # headroom 1 is the fair SDR rendering: flat above the knee whatever the exit slope.
    assert np.allclose(tone(T, 0.7, 1.0), np.minimum(T / 0.7, 1.0), atol=1e-6)


def test_steep_entry_is_capped_so_the_curve_never_overshoots():
    # 1/knee far above 3x the secant would make a plain Hermite overshoot headroom.
    y = tone(T, 0.2, 1.2)
    assert np.all(np.diff(y) >= -1e-6) and y.max() <= 1.2 + 1e-6


def test_nan_preserved():
    assert np.isnan(tone([np.nan], knee=0.5, headroom=2.0))[0]


@pytest.mark.parametrize(("knee", "headroom"), [(0.0, 2.0), (1.5, 2.0), (0.5, 0.5)])
def test_rejects_bad_arguments(knee, headroom):
    with pytest.raises(ValueError):
        tone(T, knee=knee, headroom=headroom)


def test_knee_at_percentile():
    assert knee_at_percentile(T, 90.0) == pytest.approx(0.9)


def test_knee_ignores_nan_and_is_positive():
    assert knee_at_percentile(np.array([np.nan, 0.0, 0.0]), 50.0) > 0.0
    with pytest.raises(ValueError):
        knee_at_percentile(np.array([np.nan]), 50.0)
