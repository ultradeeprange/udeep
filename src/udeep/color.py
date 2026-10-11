"""Colour spaces for HDR plotting. Element-wise over a trailing axis of 3; float64.

Linear sRGB is *relative*: 1.0 = SDR white = REF_WHITE_NITS (BT.2408 reference white).
Jzazbz takes CIE XYZ in absolute cd/m^2: Safdar et al., Opt. Express 25(13), 15131 (2017).
"""
from __future__ import annotations

import numpy as np

REF_WHITE_NITS = 203.0

_SRGB_TO_XYZ = np.array([[0.4123907992659595, 0.357584339383878, 0.1804807884018343],
                         [0.21263900587151036, 0.715168678767756, 0.07219231536073371],
                         [0.01933081871559185, 0.11919477979462598, 0.9505321522496606]])
_XYZ_TO_SRGB = np.linalg.inv(_SRGB_TO_XYZ)
# linear BT.709/sRGB primaries -> linear BT.2020 primaries (ITU-R BT.2087)
_SRGB_TO_BT2020 = np.array([[0.6274039, 0.3292830, 0.0433131],
                            [0.0690973, 0.9195404, 0.0113623],
                            [0.0163914, 0.0880133, 0.8955953]])

_B, _G = 1.15, 0.66
_C1, _C2, _C3 = 3424 / 4096, 2413 / 128, 2392 / 128
_N, _P = 2610 / 16384, 1.7 * 2523 / 32
_D, _D0 = -0.56, 1.6295499532821566e-11
_XYZ_TO_LMS = np.array([[0.41478972, 0.579999, 0.0146480],
                        [-0.2015100, 1.120649, 0.0531008],
                        [-0.0166008, 0.264800, 0.6684799]])
_LMS_TO_IAB = np.array([[0.5, 0.5, 0.0],
                        [3.524000, -4.066708, 0.542708],
                        [0.199076, 1.096799, -1.295875]])
_LMS_TO_XYZ = np.linalg.inv(_XYZ_TO_LMS)
_IAB_TO_LMS = np.linalg.inv(_LMS_TO_IAB)


def _mm(m: np.ndarray, v) -> np.ndarray:
    return np.einsum("ij,...j->...i", m, np.asarray(v, dtype=np.float64))


def srgb_to_xyz(rgb) -> np.ndarray:
    return _mm(_SRGB_TO_XYZ, rgb)


def xyz_to_srgb(xyz) -> np.ndarray:
    return _mm(_XYZ_TO_SRGB, xyz)


def srgb_to_bt2020(rgb) -> np.ndarray:
    return _mm(_SRGB_TO_BT2020, rgb)


def xyz_to_jzazbz(xyz) -> np.ndarray:
    xyz = np.asarray(xyz, dtype=np.float64)
    x, y, z = xyz[..., 0], xyz[..., 1], xyz[..., 2]
    lms = _mm(_XYZ_TO_LMS, np.stack([_B * x - (_B - 1) * z, _G * y - (_G - 1) * x, z], -1))
    q = (np.maximum(lms, 0.0) / 10000.0) ** _N
    iab = _mm(_LMS_TO_IAB, ((_C1 + _C2 * q) / (1 + _C3 * q)) ** _P)
    iz = iab[..., 0]
    jz = (1 + _D) * iz / (1 + _D * iz) - _D0
    return np.stack([jz, iab[..., 1], iab[..., 2]], -1)


def jzazbz_to_xyz(jab) -> np.ndarray:
    jab = np.asarray(jab, dtype=np.float64)
    jz = jab[..., 0] + _D0
    iz = jz / (1 + _D - _D * jz)
    r = np.maximum(_mm(_IAB_TO_LMS, np.stack([iz, jab[..., 1], jab[..., 2]], -1)), 0.0) ** (1 / _P)
    lms = 10000.0 * (np.maximum(r - _C1, 0.0) / (_C2 - _C3 * r)) ** (1 / _N)
    xp, yp, z = np.moveaxis(_mm(_LMS_TO_XYZ, lms), -1, 0)
    x = (xp + (_B - 1) * z) / _B
    return np.stack([x, (yp + (_G - 1) * x) / _G, z], -1)
