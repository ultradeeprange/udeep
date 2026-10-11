"""How visible one code-value step is: PQ steps at N bits in units of the DICOM PS3.14 GSDF JND.

The GSDF (Barten model, standard target) gives luminance at JND index j = 1..1023, over 0.05-3993 cd/m2.
A PQ step of 1 means one code value changes luminance by one just-noticeable difference for that
target. The GSDF target is not the most sensitive pattern: Miller et al. (2013) designed PQ against
the minimum of Barten's threshold over all frequencies, which is lower, so these ratios are a lower
bound on visibility, not an upper one.
"""
from __future__ import annotations

import numpy as np

from udeep.export import pq_encode

_A, _B, _C, _D, _E, _F, _G, _H, _K, _M = (
    -1.3011877, -2.5840191e-2, 8.0242636e-2, -1.0320229e-1, 1.3646699e-1,
    2.8745620e-2, -2.5468404e-2, -3.1978977e-3, 1.2992634e-4, 1.3635334e-3)
_M1, _M2, _C1, _C2, _C3 = 0.1593017578125, 78.84375, 0.8359375, 18.8515625, 18.6875
GSDF_RANGE = (0.05, 3993.0)                   # cd/m2 covered by the GSDF


def gsdf(j):
    """Luminance (cd/m2) at JND index j (DICOM PS3.14, eq. for j = 1..1023)."""
    x = np.log(np.asarray(j, dtype=np.float64))
    num = _A + _C * x + _E * x**2 + _G * x**3 + _M * x**4
    den = 1 + _B * x + _D * x**2 + _F * x**3 + _H * x**4 + _K * x**5
    return 10.0 ** (num / den)


_J = np.linspace(1.0, 1023.0, 200001)
_L = gsdf(_J)
_REL = np.gradient(_L, _J) / _L               # relative luminance change of one JND


def jnd_relative(nits):
    """Relative luminance step (dL/L) of one GSDF JND at `nits`."""
    return np.interp(np.log(nits), np.log(_L), _REL)


def pq_decode(v):
    p = np.asarray(v, dtype=np.float64) ** (1.0 / _M2)
    return 10000.0 * (np.maximum(p - _C1, 0.0) / (_C2 - _C3 * p)) ** (1.0 / _M1)


def pq_step_in_jnd(nits, bits: int):
    """Size of one PQ code-value step at `nits`, in GSDF JNDs, for a `bits`-bit signal."""
    n = 2**bits - 1
    code = np.floor(np.asarray(pq_encode(nits), dtype=np.float64) * n)
    step = pq_decode((code + 1) / n) / pq_decode(code / n) - 1.0
    return step / jnd_relative(nits)
