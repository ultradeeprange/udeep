"""The mapping from data to light and colour, as one record of parameters (design decision D3).

    value x  ->  position t in [0, 1]  ->  light y, in multiples of SDR white  ->  linear RGB, same unit

A Mapping holds exactly what a file declares about that chain, under the same names as the `udeep:` metadata
properties. Everything that computes with the mapping takes this record, so a figure can be rebuilt from its own
metadata, and nothing can depend on a parameter the file does not record. The same record is meant to become the
C struct of the native library and the uniform block of the shader.

Each method is a pure function of single values: no pixel looks at a neighbour (D1, D2). All are float64 and
written against the Array API standard, so they run on any array library that follows it (D8, D22).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from udeep.scale import as_float, in_chunks, scale, unscale
from udeep.tone import KNEE_MIN, tone

STRETCHES = ("linear", "log", "asinh", "proportional")
ABOVE_KNEE = "hermite; exit=1"                       # the reference form of profile 6.7, as udeep.tone draws it
INVERSE_STEPS = 60                                   # bisection steps: the curve is strictly increasing
KNEE_ROUNDING = 1e-12                                # a knee placed exactly at the limit may land this far inside
                                                     # it once its position is worked out: allow for that
NUMBERS = ("range_low", "range_high", "knee", "reference_headroom", "stretch_width", "stretch_offset")
EXIT_SLOPE_MAX = 3.0                                 # what udeep.tone accepts: beyond it the curve can turn back
PROPORTIONAL_RTOL = 1e-9                             # how closely range_high must equal knee x reference_headroom


def _exit_slope(above_knee: str) -> float | None:
    """The number in "hermite; exit=<number>" if the text is that form and the curve can use it, else None."""
    found = re.fullmatch(r"hermite; exit=([0-9.]+)", above_knee)
    try:
        slope = float(found.group(1)) if found else math.nan
    except ValueError:                               # digits and dots that are not a number, such as "1.2.3"
        return None
    return slope if 0.0 <= slope <= EXIT_SLOPE_MAX else None


def _cmap_module():
    import importlib

    return importlib.import_module("udeep.cmap")


@dataclass(frozen=True)
class Mapping:
    stretch: str                         # linear, log, asinh or proportional
    range_low: float                     # the declared range, in data units, after the offset is subtracted
    range_high: float
    knee: float                          # the data value shown at SDR white
    reference_headroom: float            # light at the top of the range, in multiples of SDR white
    stretch_width: float = 1.0           # asinh: the width of the linear part
    stretch_offset: float = 0.0          # subtracted from the data first
    above_knee: str = ABOVE_KNEE
    colour_map: str = "grey"             # "grey", or a registered map as name/version, such as "fire/3"
    colour_map_table: str | None = None  # the map's table itself (Table.to_text), when it is not a registered one

    def __post_init__(self):
        if self.stretch not in STRETCHES:
            raise ValueError(f"stretch must be one of {STRETCHES}, got {self.stretch!r}")
        for name in NUMBERS:                         # the comparisons below let an infinity through
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be a finite number, got {getattr(self, name)!r}")
        if not self.range_high > self.range_low:
            raise ValueError("the declared range needs range_high > range_low")
        if self.kind == "log" and not self.range_low > 0.0:
            raise ValueError("a log stretch needs range_low > 0")
        if self.stretch == "proportional" and (self.range_low != 0.0 or self.stretch_offset != 0.0):
            raise ValueError("a proportional mapping is a linear stretch from zero, with no offset")
        if not self.stretch_width > 0.0:
            raise ValueError("stretch_width must be > 0")
        if not self.range_low < self.knee - self.stretch_offset <= self.range_high:
            raise ValueError("the knee must lie inside the declared range")
        if not self.reference_headroom >= 1.0:
            raise ValueError("reference_headroom must be >= 1")
        if _exit_slope(self.above_knee) is None:
            raise ValueError(f"unknown form above the knee: {self.above_knee!r} (need 'hermite; exit=<number>' with "
                             f"the number in 0..{EXIT_SLOPE_MAX:g})")
        self._check_knee_position()
        if self.stretch == "proportional":
            self._check_proportional()

    def _check_knee_position(self):
        """The curve cannot place a knee within KNEE_MIN of either end of the range (udeep.tone), and would draw
        another one without saying so. A knee exactly at the top is the full-range SDR plot and is allowed."""
        t = self.knee_position
        if t < KNEE_MIN - KNEE_ROUNDING or 1.0 - KNEE_MIN + KNEE_ROUNDING < t < 1.0:
            raise ValueError(f"the knee {self.knee!r} is at position {t!r} of the declared range: it must be at "
                             f"least {KNEE_MIN:g} from the bottom, and either at the top or at least {KNEE_MIN:g} "
                             "below it")

    def _check_proportional(self):
        """Profile 6.6: light = value / knee over the whole range. The part above the knee is that straight line
        only when it ends at knee x headroom and leaves with the slope of its secant."""
        top = self.knee * self.reference_headroom
        if not math.isclose(self.range_high, top, rel_tol=PROPORTIONAL_RTOL, abs_tol=0.0):
            raise ValueError("a proportional mapping needs range_high == knee x reference_headroom "
                             f"({self.knee!r} x {self.reference_headroom!r} = {top!r}), got {self.range_high!r}: "
                             "otherwise light is not value / knee above the knee")
        if self.exit_slope != 1.0:
            raise ValueError(f"a proportional mapping needs above_knee {ABOVE_KNEE!r}, got {self.above_knee!r}: "
                             "any other exit slope bends the line above the knee")

    # ---- building one

    @classmethod
    def from_stretch(cls, stretch: dict, knee: float, headroom: float, cmap: str = "grey",
                     proportional: bool = False) -> Mapping:
        """From the authoring form: stretch = {"kind", "lo", "hi", optional "width", "offset"} and a map's name.
        The name alone ("fire") is the map's current version; with a version ("fire/1") it is that one, for
        reproducing an older figure. A map at a headroom it is not registered for gets its table attached, so the
        record stays complete."""
        cm = _cmap_module()
        table = None
        if cmap != "grey":
            known = cm.parse(cmap) if "/" in cmap else (cmap, cm.VERSION)
            if known is None or known[0] not in cm.DEFINITIONS[known[1]]:
                raise ValueError(f"unknown colour map {cmap!r}; have grey, {', '.join(sorted(cm.MAPS))}, and each "
                                 f"with a version from {sorted(cm.DEFINITIONS)}, such as 'fire/1'")
            cmap, version = known
            if not cm.is_registered(float(headroom)):
                table = cm.table(cmap, float(headroom), version).to_text()
        return cls(stretch="proportional" if proportional else stretch["kind"], range_low=float(stretch["lo"]),
                   range_high=float(stretch["hi"]), knee=float(knee), reference_headroom=float(headroom),
                   stretch_width=float(stretch.get("width", 1.0)), stretch_offset=float(stretch.get("offset", 0.0)),
                   colour_map="grey" if cmap == "grey" else cm.identifier(cmap, version), colour_map_table=table)

    # ---- derived numbers

    @property
    def kind(self) -> str:
        return "linear" if self.stretch == "proportional" else self.stretch

    @property
    def exit_slope(self) -> float:
        return _exit_slope(self.above_knee)

    @property
    def knee_position(self) -> float:
        """Where the knee is drawn. A record is refused unless the curve can draw it there, so nothing is moved."""
        return float(scale(np.array([self.knee - self.stretch_offset]), self.kind, self.range_low, self.range_high,
                           self.stretch_width)[0])

    # ---- the chain, forwards

    def position(self, x):
        """Data values -> position t in [0, 1]; values outside the declared range are clipped; NaN stays NaN."""
        def one(part):
            _xp, v = as_float(part)
            return scale(v - self.stretch_offset, self.kind, self.range_low, self.range_high, self.stretch_width)

        return in_chunks(one, x)

    def light_at(self, t, headroom: float | None = None):
        """Position -> light. headroom=1 gives the fair SDR rendering: the same below the knee, white above it."""
        knee = self.knee_position
        top, exit_slope = (self.reference_headroom, self.exit_slope) if headroom is None else (headroom, 1.0)
        return in_chunks(lambda part: tone(part, knee, top, exit_slope), t)

    def light(self, x, headroom: float | None = None):
        """Data values -> light, in multiples of SDR white: the declared mapping (profile section 6)."""
        return self.light_at(self.position(x), headroom)

    def colours(self, y, dtype=np.float64):
        """Light -> linear RGB (BT.709 primaries, multiples of SDR white), shape (..., 3). The colours are worked
        out in float64; dtype=np.float32 keeps them in half the memory, each rounded once (NumPy arrays only)."""
        table = self.table
        if table is None:
            xp, y = as_float(y)
            if dtype is np.float64 or xp is not np:
                return xp.stack([y, y, y], axis=-1)
            return in_chunks(lambda part: np.stack([part, part, part], axis=-1), y, tail=(3,), dtype=dtype)
        return in_chunks(table.lookup, y, tail=(3,), dtype=dtype)

    @property
    def table(self):
        """The colour map's table: None for grey, the carried table if there is one, else the registered one.
        Raises ValueError when the map cannot be known from this record."""
        cm = _cmap_module()
        if self.colour_map == "grey":
            return None
        if self.colour_map_table is not None:
            return cm.Table.from_text(self.colour_map, self.reference_headroom, self.colour_map_table)
        known = cm.parse(self.colour_map)
        if known is None or not cm.is_registered(self.reference_headroom):
            raise ValueError(f"colour map {self.colour_map!r} at headroom {self.reference_headroom:g} is not "
                             "registered here and no table is carried")
        return cm.table(known[0], self.reference_headroom, known[1])

    # ---- and backwards: the mapping is invertible (profile 6.3)

    def value_at(self, t):
        """Position -> data value: the inverse of `position` inside the declared range."""
        return unscale(t, self.kind, self.range_low, self.range_high, self.stretch_width) + self.stretch_offset

    def value(self, y):
        """Light -> data value: the inverse of `light`. Light outside 0..headroom is clipped to it."""
        xp, y = as_float(y)
        y = xp.clip(y, 0.0, self.reference_headroom)
        lo, hi = xp.zeros_like(y), xp.ones_like(y)
        for _ in range(INVERSE_STEPS):
            mid = 0.5 * (lo + hi)
            below = self.light_at(mid) < y
            lo, hi = xp.where(below, mid, lo), xp.where(below, hi, mid)
        return xp.where(xp.isnan(y), math.nan, self.value_at(0.5 * (lo + hi)))
