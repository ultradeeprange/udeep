"""udeep.scale: data -> t in [0, 1]."""
import numpy as np
import pytest

from udeep import scale


def test_linear_endpoints_and_midpoint():
    assert np.allclose(scale([0.0, 5.0, 10.0], "linear", 0.0, 10.0), [0.0, 0.5, 1.0])


def test_clips_outside_limits():
    assert np.allclose(scale([-5.0, 15.0], "linear", 0.0, 10.0), [0.0, 1.0])


def test_log_decades():
    assert np.allclose(scale([1.0, 10.0, 100.0], "log", 1.0, 100.0), [0.0, 0.5, 1.0])


def test_log_nonpositive_is_zero():
    assert np.allclose(scale([0.0, -3.0], "log", 1.0, 100.0), [0.0, 0.0])


def test_asinh_is_linear_near_zero_and_log_far_out():
    t = scale([0.0, 1e-3, 1e3], "asinh", 0.0, 1e3, width=1.0)
    assert t[0] == 0.0 and t[2] == 1.0
    assert 0.0 < t[1] < 1e-3


def test_nan_preserved_and_float32():
    t = scale([np.nan, 1.0], "linear", 0.0, 2.0)
    assert np.isnan(t[0]) and t[1] == pytest.approx(0.5)
    assert t.dtype == np.float64


@pytest.mark.parametrize(("kind", "lo", "hi"), [("linear", 1.0, 1.0), ("log", 0.0, 10.0), ("cube", 0.0, 1.0)])
def test_rejects_bad_arguments(kind, lo, hi):
    with pytest.raises(ValueError):
        scale([1.0], kind, lo, hi)
