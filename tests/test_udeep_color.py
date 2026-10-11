"""udeep.color: matrices and Jzazbz."""
import numpy as np

from udeep.color import (
    REF_WHITE_NITS,
    jzazbz_to_xyz,
    srgb_to_bt2020,
    srgb_to_xyz,
    xyz_to_jzazbz,
    xyz_to_srgb,
)

RNG = np.random.default_rng(0)


def test_srgb_xyz_inverse():
    rgb = RNG.uniform(0, 1, (100, 3))
    assert np.allclose(xyz_to_srgb(srgb_to_xyz(rgb)), rgb)


def test_white_luminance_is_one():
    assert abs(srgb_to_xyz(np.ones(3))[1] - 1.0) < 1e-6


def test_bt2020_keeps_white():
    assert np.allclose(srgb_to_bt2020(np.ones(3)), np.ones(3), atol=1e-6)


def test_jzazbz_round_trip_in_gamut():
    xyz = srgb_to_xyz(RNG.uniform(1e-3, 1, (2000, 3))) * RNG.uniform(0.1, 4000, (2000, 1))
    back = jzazbz_to_xyz(xyz_to_jzazbz(xyz))
    assert np.max(np.abs(back - xyz) / xyz) < 1e-9


def test_neutral_axis_is_nearly_achromatic_and_lightness_grows():
    nits = np.array([1.0, 10.0, 100.0, REF_WHITE_NITS, 1000.0, 4000.0])
    jab = xyz_to_jzazbz(srgb_to_xyz(np.ones(3)) * nits[:, None])
    assert np.all(np.abs(jab[:, 1:]) < 1e-3)
    assert np.all(np.diff(jab[:, 0]) > 0)
