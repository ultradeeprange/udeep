"""y (multiples of SDR white) -> linear sRGB colour.

A colour map is a TABLE, and the table is its definition (design decision D9): two segments, light 0 to 1 and
1 to the reference headroom, each of NODES evenly spaced nodes holding linear RGB (BT.709 primaries, multiples of
white, float32), with linear interpolation between nodes. `cmap` looks colours up in it.

    cmap(y, name, headroom)    colours, from the table
    table(name, headroom)      the Table: shipped for the registered headrooms (2, 4, 8, 16), built otherwise
    search(y, name, headroom)  the generator that tables are made from (tools/make_tables.py); not for runtime use

A map's definition has a version, and a file names both ("fire/3"). Every version stays registered, so that a file
made with an older one can always be read; new figures use the current one (VERSION). Each function takes
`version=` for an older one.

How the generator builds a map, in Jzazbz: for y <= 1 the colour's lightness (Jz) is that of a grey of luminance
y * top, where `top` is below 1, so SDR data never passes through white. (Its luminance is close to y * top but
not equal to it: at y = 1, 0.85 for fire against a top of 0.8, and 0.78 for ice against 0.7.) For y > 1 luminance rises from `top` to
`headroom` while chroma tapers to 0: extremes become white-hot, brighter than SDR white. Chroma is then reduced
(fixed-step bisection) until the colour fits [0, 1] (y <= 1) or [0, headroom] (y > 1). Below white the map does
not depend on the headroom; above white it does, which is why a table belongs to a (map, headroom) pair.

Version 1 measured chroma from az = bz = 0, which in Jzazbz is close to grey and not grey: with the chroma tapered
to nothing, the top of a map was a faint tint that had then to be clipped to the headroom, so the last step of a
map could fall in lightness. Its ice map also ran along the edge of the gamut near 15 % of white, where lightness
dipped for red-green colour-vision deficiencies. Version 2 measures chroma from the (az, bz) of the grey of the same
lightness, so that no chroma is exactly grey and the top is exactly white; and its ice map asks for less chroma at
its second node (0.13 against 0.16), which keeps it off that edge. In version 2 lightness rises at every node of
every shipped table, for normal vision and for each simulated deficiency (udeep.vision; tested).

Version 3 is version 2 with every value below FLOOR (1e-7 of white) set to zero. Where version 2 holds a channel at
the edge of the gamut it leaves the small remainder of the arithmetic, a few hundred-millionths of white, and that
remainder depends on which processor-specific routines NumPy and OpenBLAS choose: the generator did not give the
same table on every machine (project/performance-scope.md, 4g). With the remainders removed it does, so a version-3
table can be checked by making it again. No colour moves by more than FLOOR.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from udeep.color import (
    REF_WHITE_NITS,
    jzazbz_to_xyz,
    srgb_to_xyz,
    xyz_to_jzazbz,
    xyz_to_srgb,
)
from udeep.scale import as_float

# s = Jz / Jz(top); hue in unwrapped degrees (Jzazbz); chroma targets before gamut limiting
_FIRE = {"top": 0.8, "s": [0.0, 0.45, 0.75, 1.0], "hue": [300.0, 405.0, 445.0, 462.0], "chroma": [0.0, 0.14, 0.16, 0.10]}
_ICE = {"top": 0.7, "s": [0.0, 0.45, 0.75, 1.0], "hue": [290.0, 257.0, 225.0, 204.0], "chroma": [0.0, 0.16, 0.12, 0.07]}
DEFINITIONS = {                              # by version; a version, once shipped, never changes
    1: {"fire": _FIRE, "ice": _ICE},
    2: {"fire": _FIRE, "ice": {**_ICE, "chroma": [0.0, 0.13, 0.12, 0.07]}},
}
DEFINITIONS[3] = DEFINITIONS[2]              # the same colours; see FLOOR
GREY_AXIS = {1: False, 2: True, 3: True}     # is chroma measured from the grey of the same lightness (see above)
FLOOR = {1: 0.0, 2: 0.0, 3: 1e-7}            # a value below this, in multiples of white, is stored as zero
VERSION = 3                                  # what new figures use
MAPS = DEFINITIONS[VERSION]                  # the current definitions; every version has the same names
BISECTION_STEPS = 24
NODES = 4097                                 # per segment, in a shipped table
EMBED_NODES = 257                            # per segment, in a table built for an unregistered headroom and
                                             # carried inside the file (it must fit a JPEG metadata segment)
REGISTERED_HEADROOMS = (2.0, 4.0, 8.0, 16.0)
TABLES = Path(__file__).with_name("tables")
_GREY_XYZ = srgb_to_xyz(np.ones(3))


def _jz_of_grey(lum) -> np.ndarray:
    return xyz_to_jzazbz(np.asarray(lum, dtype=np.float64)[..., None] * _GREY_XYZ * REF_WHITE_NITS)[..., 0]


def _to_rgb(jz, c, h) -> np.ndarray:
    jab = np.stack([jz, c * np.cos(h), c * np.sin(h)], -1)
    return xyz_to_srgb(jzazbz_to_xyz(jab)) / REF_WHITE_NITS


def _from_grey(lum, c, h) -> np.ndarray:
    """The grey of luminance `lum` moved by chroma c at hue h from its own (az, bz): with c = 0, exactly that grey."""
    grey = xyz_to_jzazbz(np.asarray(lum, dtype=np.float64)[..., None] * _GREY_XYZ * REF_WHITE_NITS)
    jab = np.stack([grey[..., 0], grey[..., 1] + c * np.cos(h), grey[..., 2] + c * np.sin(h)], -1)
    return xyz_to_srgb(jzazbz_to_xyz(jab)) / REF_WHITE_NITS


def _check(name: str, version: int) -> None:
    if version not in DEFINITIONS:
        raise ValueError(f"unknown colour-map version {version!r}; have {sorted(DEFINITIONS)}")
    if name not in DEFINITIONS[version]:
        raise ValueError(f"unknown map {name!r}; have {sorted(DEFINITIONS[version])}")


def search(y, name: str = "fire", headroom: float = 4.0, version: int = VERSION) -> np.ndarray:
    """The generator: the exact colour for each y, by a per-value search. Slow; tables are made from it."""
    _check(name, version)
    if not headroom >= 1.0:
        raise ValueError("headroom must be >= 1")
    m = DEFINITIONS[version][name]
    top = m["top"]
    y = np.asarray(y, dtype=np.float64)
    above = (y - 1.0) * (headroom - top) / max(headroom - 1.0, 1e-12)
    lum = np.where(y <= 1.0, y * top, top + above)
    jz = _jz_of_grey(lum)
    jz_top, jz_max = float(_jz_of_grey(top)), float(_jz_of_grey(headroom))
    s = np.minimum(jz / jz_top, 1.0)
    h = np.radians(np.interp(s, m["s"], m["hue"]))
    taper = np.clip((jz - jz_top) / max(jz_max - jz_top, 1e-12), 0.0, 1.0)
    c = np.interp(s, m["s"], m["chroma"]) * (1.0 - taper)
    upper = np.where(y <= 1.0, 1.0, headroom)[..., None]

    # version 1 let a colour stand a millionth outside the gamut and clipped it; version 2 keeps it inside
    colour, slack = ((lambda scale: _from_grey(lum, scale * c, h)), 0.0) if GREY_AXIS[version] else \
        ((lambda scale: _to_rgb(jz, scale * c, h)), 1e-6)
    lo, hi = np.zeros_like(y), np.ones_like(y)
    for _ in range(BISECTION_STEPS):
        mid = 0.5 * (lo + hi)
        rgb = colour(mid)
        fits = np.all((rgb >= -slack) & (rgb <= upper + slack), axis=-1)
        lo, hi = np.where(fits, mid, lo), np.where(fits, hi, mid)
    rgb = np.clip(colour(lo), 0.0, upper)
    rgb = np.where(rgb < FLOOR[version], 0.0, rgb)
    return np.where(np.isnan(y)[..., None], np.nan, rgb).astype(np.float32)


class Table:
    """A colour map as data: `below` covers light 0..1, `above` covers 1..headroom; each is nodes x 3, float32."""

    def __init__(self, name: str, headroom: float, below, above):
        self.name, self.headroom = name, float(headroom)
        self.below = np.ascontiguousarray(below, dtype="<f4"); self.above = np.ascontiguousarray(above, dtype="<f4")
        if self.below.shape != self.above.shape or self.below.ndim != 2 or self.below.shape[1] != 3 or len(self.below) < 2:
            raise ValueError("a table needs two segments of the same shape, nodes x 3")
        self.nodes = len(self.below)
        self._all = np.concatenate([self.below, self.above]).astype(np.float64)

    def lookup(self, y) -> np.ndarray:
        """Linear interpolation between nodes; y outside 0..headroom takes the end colour; NaN stays NaN.
        Float64, on any array library that follows the Array API standard."""
        xp, y = as_float(y)
        nan = xp.isnan(y)
        v = xp.clip(xp.where(nan, 0.0, y), 0.0, max(self.headroom, 1.0))
        high = v > 1.0
        p = xp.where(high, (v - 1.0) / max(self.headroom - 1.0, 1e-12), v) * (self.nodes - 1)
        i = xp.astype(xp.floor(p), xp.int64)
        i = xp.where(i > self.nodes - 2, self.nodes - 2, i)
        f = (p - xp.astype(i, xp.float64))[..., None]
        i = xp.reshape(i + xp.astype(high, xp.int64) * self.nodes, (-1,))
        nodes = xp.asarray(self._all)
        a = xp.reshape(xp.take(nodes, i, axis=0), (*y.shape, 3))
        b = xp.reshape(xp.take(nodes, i + 1, axis=0), (*y.shape, 3))
        return xp.where(nan[..., None], math.nan, a * (1.0 - f) + b * f)

    def to_bytes(self) -> bytes:
        """The table's numbers: `below` then `above`, row by row, little-endian float32."""
        return self.below.tobytes() + self.above.tobytes()

    @property
    def digest(self) -> str:
        """The SHA-256 of `to_bytes`, as "sha256:" and the hexadecimal digits: what the index of the shipped tables
        records for each, and what a shipped table is checked against when it is read."""
        return "sha256:" + hashlib.sha256(self.to_bytes()).hexdigest()

    def to_text(self) -> str:
        """For carrying a table inside a file: "<nodes>;" and the bytes in base64."""
        return f"{self.nodes};" + base64.b64encode(self.to_bytes()).decode("ascii")

    @classmethod
    def from_text(cls, name: str, headroom: float, text: str) -> Table:
        """The table that `to_text` wrote, read back from a file that carries it."""
        nodes, _, body = text.partition(";")
        a = np.frombuffer(base64.b64decode(body), dtype="<f4").reshape(2, int(nodes), 3)
        return cls(name, headroom, a[0], a[1])


def identifier(name: str, version: int = VERSION) -> str:
    """How a file names a map: the name and the version of its definition, such as "fire/3"."""
    return f"{name}/{version}"


def parse(text: str) -> tuple[str, int] | None:
    """The (name, version) a file's map name stands for, or None if it is not a registered map."""
    name, _, version = text.partition("/")
    if not version.isdigit() or int(version) not in DEFINITIONS or name not in DEFINITIONS[int(version)]:
        return None
    return name, int(version)


def generate(name: str, headroom: float, nodes: int = NODES, version: int = VERSION) -> Table:
    """Build a table from the generator. For headroom 1 there is nothing above white: that segment is flat."""
    below = search(np.linspace(0.0, 1.0, nodes), name, max(headroom, 1.0), version)
    above = search(np.linspace(1.0, headroom, nodes), name, headroom, version) if headroom > 1.0 else \
        np.repeat(below[-1:], nodes, 0)
    return Table(name, headroom, below, above)


def _file(name: str, headroom: float, version: int = VERSION) -> str:
    return f"{name}-{version}-h{headroom:g}.f32"


@lru_cache(maxsize=64)
def table(name: str, headroom: float, version: int = VERSION) -> Table:
    """The table for a map at a reference headroom: the shipped one for a registered headroom, otherwise one built
    now at EMBED_NODES nodes, which the file must then carry. Headroom 1 (an SDR figure) uses the lower segment
    of a shipped table."""
    _check(name, version)
    if not headroom >= 1.0:
        raise ValueError("headroom must be >= 1")
    shipped = headroom if headroom in REGISTERED_HEADROOMS else REGISTERED_HEADROOMS[0] if headroom == 1.0 else None
    if shipped is None:
        return generate(name, headroom, EMBED_NODES, version)
    index = json.loads((TABLES / "index.json").read_text(encoding="utf-8"))
    entry = index["tables"][f"{identifier(name, version)}@{shipped:g}"]
    a = np.frombuffer((TABLES / entry["file"]).read_bytes(), dtype="<f4").reshape(2, index["nodes"], 3)
    t = Table(name, shipped, a[0], a[1])
    if t.digest != entry["sha256"]:
        raise ValueError(f"colour-map table {entry['file']} does not match its recorded digest")
    return t if headroom > 1.0 else Table(name, 1.0, t.below, np.repeat(t.below[-1:], t.nodes, 0))


def is_registered(headroom: float) -> bool:
    """Whether a table is shipped for this reference headroom: 2, 4, 8 or 16, and 1 (an SDR figure, which uses the
    lower segment of a shipped table). At any other headroom a file must carry its table."""
    return headroom == 1.0 or headroom in REGISTERED_HEADROOMS


def cmap(y, name: str = "fire", headroom: float = 4.0, version: int = VERSION) -> np.ndarray:
    """Colours for light y, from the map's table: linear RGB in multiples of SDR white, float64."""
    return table(name, float(headroom), version).lookup(y)
