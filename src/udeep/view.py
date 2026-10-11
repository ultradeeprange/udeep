"""The two presets of the UDeep Figure Profile (v0.3, section 13).

    highlight -- a high, meaningful knee; sparse extremes go above SDR white. Isolated pixels above
                 the knee are held at white so noise cannot sparkle: a derived mask (udeep.masks).
    extend    -- the knee at the top of the main range; a second range of real structure goes above
                 white. No area filter: thin real structure (e.g. bone cortex) must lift too.

Both keep the plot below the knee identical to the SDR plot clipped at the same white (the fair
baseline), never lift masked pixels (saturation, blooming, clipping), and take the knee in data
units. A missing knee falls back to a percentile and is flagged as exploratory.

The view holds three arrays the size of the image: the two renderings and the light. The ordinary full-range
SDR plot is not kept (it was a third rendering, and nothing used it): it is `view.mapping.colours(
view.mapping.position(data))` when wanted.

The renderings are float64 unless float32=True is asked for, which keeps them in half the memory (32 bytes a
pixel held for the view, against 56). The mapping is worked out in float64 either way and each colour is rounded
once as it is stored, so a float32 rendering is within one part in 10 million of the float64 one, far inside an
8-bit file's step; but it is not the same number, so the default stays float64.

The mapping is done in NumPy, the reference, unless the Rust core has been turned on (udeep.core.enable()): then
the core does it, on every processor core, to within about one part in 10^12 of the reference.

The mapping itself is a `Mapping` record (udeep.mapping) and is pointwise. This module applies it to an
image in three steps: derive any mask the preset calls for, map every pixel, hold the masked ones.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from udeep import core, masks
from udeep.mapping import Mapping

MODES = ("highlight", "extend")
FALLBACK_PERCENTILE = {"highlight": 99.5, "extend": 95.0}
LIMITER_FRACTION = 0.10          # above-white area beyond which panels dim large bright regions


@dataclass
class HdrView:
    sdr: np.ndarray              # fair SDR plot: clipped at the same white (H x W x 3, 0..1)
    hdr: np.ndarray              # HDR plot, multiples of SDR white (0..headroom)
    y: np.ndarray                # HDR luminance multiple per pixel, before colour
    knee_value: float            # data value rendered at SDR white
    knee_t: float
    above_white: float           # fraction of pixels lifted above white
    masked: int                  # pixels in the validity mask the caller supplied
    derived_mask: str | None = None   # rule name of a mask derived from neighbourhoods (udeep.masks), if any
    derived_masked: int = 0      # pixels that rule holds, beyond the validity mask
    warnings: list[str] = field(default_factory=list)
    colourbar: dict = field(default_factory=dict)
    mapping: Mapping | None = None   # the record that defines every colour here


def hdr_view(data, mode: str, knee: float | None, stretch: dict, headroom: float = 2.0,
             cmap: str = "fire", mask=None, proportional: bool = False, float32: bool = False) -> HdrView:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    d = np.asarray(data, dtype=np.float64)
    warnings: list[str] = []
    if knee is None:
        pct = FALLBACK_PERCENTILE[mode]
        knee = float(np.percentile(d[np.isfinite(d)], pct))
        warnings.append(f"knee from the {pct}th percentile ({knee:.4g}): exploratory, not a meaningful threshold")
    m = Mapping.from_stretch(stretch, knee, headroom, cmap, proportional)
    fast = core.enabled()
    if fast:
        y_sdr, y = core.light(m, d, sdr=True), core.light(m, d)
    else:
        t = m.position(d)
        y_sdr = m.light_at(t, 1.0)
        y = m.light_at(t)
        del t                                # each of these is as large as the image: keep none longer than needed
    # 1. masks: the caller's validity mask, and for Highlight the isolated pixels above the knee
    valid_mask = np.zeros(d.shape, bool) if mask is None else np.asarray(mask, bool)
    # (the rule sees only the pixels that would be lifted: a masked one is held, so it cannot support a block)
    derived = masks.isolated((y > 1.0) & ~valid_mask) if mode == "highlight" else np.zeros(d.shape, bool)
    # 2. the mapping was applied to every pixel alone (above); 3. hold the masked ones at their SDR light
    y = masks.hold(y, y_sdr, valid_mask | derived)

    lifted = float(np.mean(y > 1.0 + 1e-9))
    if mode == "extend" and lifted > LIMITER_FRACTION:
        warnings.append(f"{lifted:.0%} of the plot is above white: large bright areas read as white "
                        "and trigger the display's brightness limiter (keep under ~10 %)")

    ts = np.linspace(0.0, 1.0, 256)
    bar = {"t": ts.tolist(), "value": m.value_at(ts).tolist(), "y": m.light_at(ts).tolist(),
           "sdr_white_value": float(knee)}
    # One table defines every colour in the figure: the SDR renderings use it too (below white a map does not
    # depend on the headroom).
    kept = np.float32 if float32 else np.float64
    colours = (lambda light: core.colours(m, light, kept)) if fast else (lambda light: m.colours(light, kept))
    return HdrView(sdr=colours(y_sdr), hdr=colours(y), y=y, knee_value=float(knee),
                   knee_t=m.knee_position, above_white=lifted, masked=int(valid_mask.sum()), warnings=warnings,
                   colourbar=bar, mapping=m, derived_mask=masks.ISOLATED if mode == "highlight" else None,
                   derived_masked=int(derived.sum()))
