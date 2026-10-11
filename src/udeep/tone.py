"""t -> y in multiples of SDR white (t is already log/asinh scaled).

    y = t / knee                          for t <= knee   (SDR: 0 .. 1)
    y = cubic Hermite from 1 to headroom  for t >  knee   (HDR: 1 .. headroom)

The shoulder starts with the slope of the SDR part (C1: no kink, so no Mach band at the knee)
and ends at `exit_slope` times its secant slope. The default, 1, is the **declared mapping** of the
profile (v0.3, 6.3): strictly increasing, so every light level maps back to one data value. With
entry slope a and exit slope 1 (in units of the secant) the slope is (3a-3)u^2 + (4-4a)u + a, never
below min(a, (4-a)/3) of the secant. `exit_slope=0` ends flat at `headroom`, the roll-off of ITU-R
BT.2390's EETF: use it only for display rendering, because it squeezes the top of the data range
into the last few percent of light, where it cannot be recovered.

If 1/knee exceeds three times the secant slope, the entry slope is capped there (Fritsch-Carlson:
entry and exit slopes both within [0, 3] secants keep a cubic Hermite monotone), at the cost of C1
in that corner case.

knee = 1 gives the full-range SDR plot (y = t); headroom = 1 with knee < 1 gives SDR clipped at
the same white -- the fair baseline an HDR rendering must match below white.
"""
from __future__ import annotations

import numpy as np

from udeep.scale import as_float

KNEE_MIN = 1e-6


def tone(t, knee: float, headroom: float, exit_slope: float = 1.0):
    """Float64, on any array library that follows the Array API standard (design decisions D8, D22)."""
    if not 0.0 < knee <= 1.0:
        raise ValueError(f"knee must be in (0, 1], got {knee}")
    if not headroom >= 1.0:
        raise ValueError(f"headroom must be >= 1, got {headroom}")
    if not 0.0 <= exit_slope <= 3.0:
        raise ValueError(f"exit_slope must be in [0, 3] secants to stay monotone, got {exit_slope}")
    xp, t = as_float(t)
    sdr = t / knee
    if knee >= 1.0:
        return xp.clip(sdr, 0.0, headroom)
    width = max(1.0 - knee, KNEE_MIN)
    secant = (headroom - 1.0) / width
    m0 = min(1.0 / knee, 3.0 * secant)           # entry slope (dy/dt), capped for monotonicity
    u = xp.clip((t - knee) / width, 0.0, 1.0)
    h00, h10, h01, h11 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u, -2 * u**3 + 3 * u**2, u**3 - u**2
    shoulder = h00 * 1.0 + h10 * width * m0 + h01 * headroom + h11 * width * exit_slope * secant
    y = xp.where(t <= knee, sdr, shoulder)
    return xp.clip(y, 0.0, headroom)


def knee_at_percentile(t, pct: float) -> float:
    """The t below which `pct` % of the finite values lie; clamped to (0, 1]."""
    v = np.asarray(t, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        raise ValueError("no finite values")
    return float(min(max(np.percentile(v, pct), KNEE_MIN), 1.0))
