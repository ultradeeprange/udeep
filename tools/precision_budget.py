"""Precision from uncertainty (work item 1.1): how many encoding bits a data mapping needs so that
quantisation adds negligibly to the data's own noise.

For a declared mapping x -> y (multiples of SDR white) -> E = PQ(y * white) quantised to N bits, one
code step dE corresponds to a data step  dx_q(x) = dE / |dE/dx|.  Uniform rounding adds error with
standard deviation dx_q / sqrt(12).  Criterion (adds <= 1 % to the data variance):

    dx_q(x) / sqrt(12) <= K * sigma(x),   K = 0.1        ->   N_req(x) = log2(1 / (sqrt(12) K sigma |dE/dx|))

For integer data (e.g. CT HU) a second, stricter target is *lossless*: dx_q <= 1 data unit, so that every
distinct value gets its own code.  Where sigma is zero (deterministic simulation output) the criterion
needs a declared tolerance instead, or falls back to visibility (udeep.jnd).

All sigma values below are ILLUSTRATIVE inputs, not measurements.
"""
from __future__ import annotations

import numpy as np

from udeep.export import pq_encode
from udeep.tone import tone as tone32

WHITE = 203.0          # BT.2408 reference white, cd/m2
K = 0.1                # added noise <= 10 % of sigma  => <= 1 % of variance


def tone64(t, knee, headroom, exit_slope=1.0):
    """udeep.tone in float64 (the library casts to float32, too coarse for slopes)."""
    t = np.asarray(t, dtype=np.float64)
    width = max(1.0 - knee, 1e-6); secant = (headroom - 1.0) / width
    m0 = min(1.0 / knee, 3.0 * secant)
    u = np.clip((t - knee) / width, 0.0, 1.0)
    h00, h10, h01, h11 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u, -2 * u**3 + 3 * u**2, u**3 - u**2
    shoulder = h00 + h10 * width * m0 + h01 * headroom + h11 * width * exit_slope * secant
    return np.clip(np.where(t <= knee, t / knee, shoulder), 0.0, headroom)


def mapping(x, kind, lo, hi, knee_x, headroom, exit_slope=1.0):
    t = (x - lo) / (hi - lo) if kind == "linear" else np.log(x / lo) / np.log(hi / lo)
    kt = float((knee_x - lo) / (hi - lo) if kind == "linear" else np.log(knee_x / lo) / np.log(hi / lo))
    y = tone64(t, kt, headroom, exit_slope)
    assert np.allclose(y, tone32(t, kt, headroom, exit_slope=exit_slope), rtol=2e-6, atol=2e-6)   # same curve as the library
    return y, kt


def bits_required(x, sigma, **m):
    y, _kt = mapping(x, **m)
    E = pq_encode(y * WHITE)
    dEdx = np.abs(np.gradient(E, x))
    with np.errstate(divide="ignore"):
        need = np.log2(1.0 / (np.sqrt(12.0) * K * sigma * dEdx))
    return y, need, dEdx


def summarise(name, x, sigma, lossless_unit=None, **m):
    y, need, dEdx = bits_required(x, sigma, **m)
    below, above = y <= 1.0, y > 1.0
    near_top = y > m["headroom"] * 0.97               # the last few % of the roll-off, slope -> 0
    shoulder = above & ~near_top
    row = [name]
    for mask in (below, shoulder, near_top):
        row.append(f"{np.nanmedian(need[mask]):5.1f} / {np.nanmax(need[mask]):5.1f}" if mask.any() else "   -")
    if lossless_unit is not None:
        lneed = np.log2(1.0 / (lossless_unit * dEdx))
        row.append(f"{np.nanmax(lneed[below]):5.1f} / {np.nanmax(lneed[shoulder]):5.1f}")
    frac_top = near_top.mean() * 100
    print(" | ".join(row), f"| top-3%-of-headroom region = {frac_top:.1f} % of the data range")


if __name__ == "__main__":
    import sys
    EXIT = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0      # 1: declared mapping (v0.3); 0: the v0.2 roll-off
    print(f"exit_slope = {EXIT}")
    print("bits required, median / max   [below knee | shoulder | last 3 % of headroom]  (+ lossless below|shoulder)")
    x = np.linspace(-160.0, 1850.0, 200001)
    for s in (5.0, 10.0, 20.0):
        for h in (2.0, 4.0):
            summarise(f"CT  sigma={s:>4} HU, headroom {h}", x, np.full_like(x, s), lossless_unit=1.0,
                      kind="linear", lo=-160.0, hi=1850.0, knee_x=240.0, headroom=h, exit_slope=EXIT)
    x = np.geomspace(1.0, 1e5, 200001)
    for h in (2.0, 4.0):
        summarise(f"counts Poisson, knee 1e3, headroom {h}", x, np.sqrt(x),
                  kind="log", lo=1.0, hi=1e5, knee_x=1e3, headroom=h, exit_slope=EXIT)
    x = np.geomspace(1e-3, 1e3, 200001)
    for rel in (1e-2, 1e-3, 1e-4):
        summarise(f"float, declared rel. tol {rel:g}, 6 decades", x, rel * x,
                  kind="log", lo=1e-3, hi=1e3, knee_x=1.0, headroom=4.0, exit_slope=EXIT)
