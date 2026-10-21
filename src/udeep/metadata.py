"""The declared metadata of the UDeep Figure Profile (v0.3, section 8): what a file must say about
how its data became light, so that the mapping can be inverted and checked.

    Declaration      the properties, validated
    to_xmp           an XMP packet (the `udeep` namespace) for a JPEG APP1 segment, a PNG iTXt chunk or SVG metadata
    read_metadata    the Declaration carried by a file, or None
    missing_properties   the properties section 8 requires that a packet does not carry
    float_text       how a number is written: the shortest text that reads back to the same float64
    declared_light   the declared mapping, data -> light, computed from the declaration alone
    caption          a caption sentence generated from the declaration (section 8.2)
    max_data_step    the widest range of data values that one stored code stands for (section 7.2.3)
    rounding_to_noise  the data criterion (section 7.2.1): that rounding against the stated uncertainty
    digest_data, digest_pixels   the digests that tie a file to its data and to its SDR figure of record
    digest_sdr       SdrDigest of a fair SDR rendering given as light, as the writers store it in 8 bits
    values_pq16, read_png_data   a 16-bit PQ PNG's light, and such a file, read back as data values

The namespace URI resolves to a page listing these properties; it is provisional until version 1.0 (profile
section 10, open issue 1).
"""
from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, fields
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from udeep.color import REF_WHITE_NITS
from udeep.export import pq16_codes, read_png_pq, srgb_oetf
from udeep.mapping import ABOVE_KNEE, Mapping

VERSION = "0.3"
NAMESPACE = f"https://ultradeeprange.github.io/udeep/ns/{VERSION}/"
NOISE_MODELS = ("constant", "poisson", "relative", "tolerance", "unknown")
KNEE_RULES = ("physical", "statistical", "preset", "percentile")
RECORDS = ("data-record", "rendering")
DATA_CRITERION_K = 0.1                               # profile 7.2.1
IMAGE_STATE = "data-referred"
EXACT_INTEGER = 2 ** 53                              # every integer up to this size is a float64, and distinct
CAPTION_FIGURES = 6                                  # enough for a range top such as 16383 to be printed as itself
# Properties a file may leave out (profile 8.1). NoiseParameter, DerivedMask and ColourMapTable are required only in
# cases that the declaration's own values decide, and are checked there.
OPTIONAL = ("noise_parameter", "derived_mask", "preset", "colour_map_table", "rounding_to_noise", "data_digest",
            "data_source", "data_unit_code", "sdr_digest")
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


@dataclass(frozen=True)
class Declaration:
    """What a file declares about how its data became light (profile section 8), checked as it is made.

    The fields are the `udeep:` metadata properties under Python names: `range_low` is written `udeep:RangeLow`.
    Those that define the colours are the fields of a Mapping, which the `mapping` property gives. The rest say
    what the data is (`data_unit`, `data_unit_code`), why the knee is where it is (`knee_rule`: "physical",
    "statistical", "preset" or "percentile", then "; " and a description), how uncertain the data is
    (`noise_model`, `noise_parameter`), how finely the file stores it (`max_data_step`, `rounding_to_noise`),
    which pixels were held (`masked`, `derived_mask`, `derived_masked`), which preset made the figure (`preset`),
    whether the file is a data record or a rendering (`record`), and what ties it to its data and to its SDR
    figure (`data_digest`, `data_source`, `sdr_digest`). `version` and `image_state` are fixed by the profile.

    ValueError, with the reason, for a declaration the profile does not allow."""

    data_unit: str
    stretch: str
    range_low: float
    range_high: float
    knee: float
    knee_rule: str
    reference_headroom: float
    max_data_step: float
    noise_model: str = "unknown"
    noise_parameter: float | None = None
    stretch_width: float = 1.0
    stretch_offset: float = 0.0
    above_knee: str = ABOVE_KNEE
    masked: int = 0                      # pixels in the validity mask (saturated, clipped, invalid)
    derived_mask: str | None = None      # rule name of a mask derived from neighbourhoods, such as isolated-3x3
    derived_masked: int = 0              # pixels that rule holds at white, beyond the validity mask
    record: str = "rendering"
    preset: str | None = None
    colour_map: str | None = None        # the map from light to colour: "grey", or name/version such as "fire/1"
    colour_map_table: str | None = None  # the map's table itself (Table.to_text), when it is not a registered one
    rounding_to_noise: float | None = None   # worst rounding error over the stated uncertainty (rounding_to_noise)
    data_digest: str | None = None       # digest_data of the array that was mapped: ties the file to its data
    data_source: str | None = None       # where that data is kept: a persistent identifier (doi:..., or a URL)
    data_unit_code: str | None = None    # the unit in machine-readable form: a UCUM code, such as "K" or "s-1"
    sdr_digest: str | None = None        # digest_pixels of the fair SDR figure, before any lossy coding
    version: str = VERSION
    image_state: str = "data-referred"

    def __post_init__(self):
        if not self.data_unit:
            raise ValueError('data_unit must be stated (use "1" for a dimensionless quantity)')
        if self.noise_model not in NOISE_MODELS:
            raise ValueError(f"noise_model must be one of {NOISE_MODELS}, got {self.noise_model!r}")
        if (self.noise_model == "unknown") != (self.noise_parameter is None):
            raise ValueError("noise_parameter is needed for every noise model except 'unknown', and only then")
        self._check_fixed_and_counted()
        _ = self.mapping                                  # the mapping's own checks: stretch, range, knee
        if self.knee_rule.split(";")[0].strip() not in KNEE_RULES:
            raise ValueError(f"knee_rule must start with one of {KNEE_RULES}, then '; ' and a description")
        if not self.reference_headroom > 1.0:
            raise ValueError("reference_headroom must be > 1")
        if self.record not in RECORDS:
            raise ValueError(f"record must be one of {RECORDS}")
        if self.record == "data-record" and self.noise_model == "unknown":
            raise ValueError("record: a file with an unknown noise model cannot be a data-record (profile 7.3.1)")

    def _check_fixed_and_counted(self):
        """What the profile fixes (section 8), and numbers that would mean nothing: a step or an uncertainty that
        is not positive, a count below zero, pixels held by a rule that is not named (6.2a)."""
        if self.version != VERSION:
            raise ValueError(f"version must be {VERSION!r}, got {self.version!r}")
        if self.image_state != IMAGE_STATE:
            raise ValueError(f"image_state must be {IMAGE_STATE!r}, got {self.image_state!r}")
        if not (math.isfinite(self.max_data_step) and self.max_data_step > 0.0):
            raise ValueError(f"max_data_step must be a finite number > 0, got {self.max_data_step!r}")
        if self.noise_parameter is not None and not (math.isfinite(self.noise_parameter) and self.noise_parameter > 0.0):
            raise ValueError(f"noise_parameter must be a finite number > 0, got {self.noise_parameter!r}")
        if self.rounding_to_noise is not None and not (math.isfinite(self.rounding_to_noise)
                                                       and self.rounding_to_noise >= 0.0):
            raise ValueError(f"rounding_to_noise must be a finite number >= 0, got {self.rounding_to_noise!r}")
        for name in ("masked", "derived_masked"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} is a count of pixels and cannot be negative, got {getattr(self, name)!r}")
        if self.derived_masked > 0 and not self.derived_mask:
            raise ValueError(f"derived_mask must name the rule that holds {self.derived_masked} pixels at white")

    @property
    def mapping(self) -> Mapping:
        """The part of the declaration that defines the colours: the record every computation takes."""
        kw = {f.name: getattr(self, f.name) for f in fields(Mapping)}
        kw["colour_map"] = self.colour_map or "undeclared"      # no map declared: the colours cannot be known
        return Mapping(**kw)


def _property(field_name: str) -> str:
    return "".join(part.capitalize() for part in field_name.split("_"))


def float_text(value: float) -> str:
    """A number as the metadata writes it: the shortest text that reads back to the same float64.

    It is Python's repr of the float, without the ".0" that ends a whole number: 4.0 is "4", 0.1 is "0.1",
    1700000002.3456 is "1700000002.3456", 1e16 is "1e+16" and 0.00001 is "1e-05". Other languages reproduce it
    from the shortest digits that read back: with the decimal point after digit number p, the text is plain
    digits when -4 < p <= 16; otherwise it is the first digit, a point and the rest if there are more, then "e",
    the sign of the exponent p - 1 and at least two digits of it."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"only a finite number can be written, got {value!r}")
    return repr(value).removesuffix(".0")


def _text(value) -> str:
    return float_text(value) if isinstance(value, float) else str(value)


def _packet(element: bytes) -> bytes:
    return (b'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="udeep"><rdf:RDF xmlns:rdf="' + _RDF.encode() + b'">'
            + element + b"</rdf:RDF></x:xmpmeta>")


def to_xmp(d: Declaration) -> bytes:
    """A complete XMP packet holding the declaration as `udeep:` attributes."""
    return _packet(description(d))


def digest_data(a) -> str:
    """A digest of an array's values and shape, whatever type it is stored as: "sha256:" and 64 hex digits.

    The array is taken as float64, little-endian, in row order; every NaN counts as the same value and -0 as 0.
    The shape goes in first, so the same numbers in another shape give another digest.

    Only real numbers that float64 holds exactly are accepted: floats, and integers no larger than 2 ** 53.
    Anything else (complex numbers, text, booleans, dates, wider floats) raises ValueError, because the cast would
    give two different arrays one digest. A masked array is digested as its stored values, hidden ones included;
    the mask is not part of the digest."""
    given = np.asarray(a)
    kind = given.dtype.kind
    if kind not in "iuf" or (kind == "f" and given.dtype.itemsize > 8):
        raise ValueError(f"only an array of real numbers has a data digest, got {given.dtype}"
                         + (": a complex array is not real, so digest its parts" if kind == "c" else ""))
    if kind in "iu" and given.dtype.itemsize >= 8 and bool(np.any((given < -EXACT_INTEGER) | (given > EXACT_INTEGER))):
        raise ValueError("integers beyond 2 ** 53 are not exact in float64, which the data digest is taken in")
    v = np.ascontiguousarray(given.astype("<f8")) + 0.0
    v = np.where(np.isnan(v), np.nan, v)
    h = hashlib.sha256(("udeep-data-1;" + "x".join(str(n) for n in v.shape) + ";").encode())
    h.update(np.ascontiguousarray(v, dtype="<f8").tobytes())
    return "sha256:" + h.hexdigest()


def digest_pixels(rgb8) -> str:
    """A digest of an 8-bit RGB image's pixels (H x W x 3), independent of how the file stores them.

    It is taken over the text "udeep-rgb8-1;<height>x<width>;" and then the bytes, row by row. The array must
    already be unsigned 8-bit: any other type raises ValueError, since casting it would wrap or truncate."""
    v = np.asarray(rgb8)
    if v.dtype != np.uint8:
        raise ValueError(f"pixels must be an unsigned 8-bit integer array, got {v.dtype}")
    if v.ndim != 3 or v.shape[-1] != 3:
        raise ValueError("pixels must be H x W x 3")
    h = hashlib.sha256(f"udeep-rgb8-1;{v.shape[0]}x{v.shape[1]};".encode())
    h.update(np.ascontiguousarray(v).tobytes())
    return "sha256:" + h.hexdigest()


def digest_sdr(sdr) -> str:
    """The SdrDigest (profile 8.6) of a fair SDR rendering given as light: height x width x 3, linear, 0 to 1, as
    HdrView.sdr holds it. It is taken over the 8-bit sRGB codes the writers store for it (the base of a gain-map
    JPEG before compression, and udeep.write_png_sdr's file), with a NaN stored as 0.

    A file with no SDR base of its own, such as a 16-bit PQ PNG, must declare it (profile 9.1.2); the gain-map JPEG
    of the same figure then declares the same value."""
    light = np.nan_to_num(np.asarray(sdr, dtype=np.float64), nan=0.0)
    return digest_pixels(np.round(srgb_oetf(light) * 255.0).astype(np.uint8))


def description(d: Declaration) -> bytes:
    """The declaration as one rdf:Description element, to sit beside others in an existing packet.

    The element is read back before it is returned, and ValueError is raised if that gives another declaration:
    a file must not state a mapping other than the one that made its pixels (profile 6.4)."""
    attrs = "".join(f" udeep:{_property(f.name)}={quoteattr(_text(getattr(d, f.name)))}"
                    for f in fields(d) if getattr(d, f.name) is not None)
    element = f'<rdf:Description rdf:about="" xmlns:udeep="{NAMESPACE}"{attrs}/>'.encode()
    _check_reads_back(d, element)
    return element


def _check_reads_back(d: Declaration, element: bytes) -> None:
    try:
        back = parse(_packet(element))
    except (ValueError, TypeError) as err:
        raise ValueError(f"the declaration does not read back from its own text: {err}") from err
    changed = [f"udeep:{_property(f.name)} {getattr(d, f.name)!r} is read as {getattr(back, f.name)!r}"
               for f in fields(d) if getattr(back, f.name) != getattr(d, f.name)]
    if changed:
        raise ValueError("the declaration does not read back as it was given: " + "; ".join(changed))


def _declared(xmp: bytes) -> dict | None:
    """The udeep properties of the first rdf:Description that has any, as text; None if there is none."""
    if b"<!DOCTYPE" in xmp or b"<!ENTITY" in xmp:
        raise ValueError("the XMP packet carries a document-type declaration, which this reader refuses")
    try:
        root = ET.fromstring(xmp)
    except ET.ParseError as err:
        raise ValueError(f"the XMP packet is not well-formed XML: {err}") from err
    for node in root.iter(f"{{{_RDF}}}Description"):
        found = {k.split("}")[1]: v for k, v in node.attrib.items() if k.startswith(f"{{{NAMESPACE}}}")}
        if found:
            return found
    return None


def parse(xmp: bytes) -> Declaration | None:
    """The declaration in an XMP packet, or None if it holds none. An invalid declaration raises ValueError: a
    value out of range or not a number, or a property the record cannot be made without.

    A property the packet does not carry takes the record's default, so the result does not show what the file
    itself states: `missing_properties` does."""
    found = _declared(xmp)
    if found is None:
        return None
    kw = {}
    for f in fields(Declaration):
        raw = found.get(_property(f.name))
        if raw is None:
            continue
        kind = str(f.type)
        kw[f.name] = float(raw) if "float" in kind else int(raw) if kind == "int" else raw
    try:
        return Declaration(**kw)
    except TypeError as err:                                   # a field with no default was not in the packet
        raise ValueError(f"the declaration is incomplete: {err}") from err


def missing_properties(xmp: bytes) -> list[str] | None:
    """The properties profile 8.1 requires of every file that this packet does not carry, by name and in the
    record's order; None if the packet holds no declaration at all."""
    found = _declared(xmp)
    if found is None:
        return None
    return [_property(f.name) for f in fields(Declaration) if f.name not in OPTIONAL and _property(f.name) not in found]


def packet(data: bytes) -> bytes | None:
    """The XMP packet in a file's bytes that uses the udeep namespace, or None."""
    for m in re.finditer(rb"<x:xmpmeta.*?</x:xmpmeta>", data, re.DOTALL):
        if NAMESPACE.encode() in m.group(0):
            return m.group(0)
    return None


def read_metadata(path) -> Declaration | None:
    """The declaration a file carries (gain-map JPEG, PNG, SVG or a bare packet), or None if it has none.
    ValueError if it carries one that cannot be read."""
    xmp = packet(Path(path).read_bytes())
    return None if xmp is None else parse(xmp)


def declared_light(d: Declaration, x) -> np.ndarray:
    """The declared mapping itself: data values -> light in multiples of SDR white, computed from the declaration
    alone (profile 6.4). Values outside the declared range are clipped to it."""
    return d.mapping.light(x)


def _number(v: float) -> str:
    """Six significant figures, written out in full between a thousandth and a million: there every digit shown
    is a digit of the number. Larger and smaller numbers take an exponent, which does not pretend to more."""
    r = float(f"{v:.{CAPTION_FIGURES}g}")
    text = np.format_float_positional(r, trim="-") if r == 0.0 or 1e-3 <= abs(r) < 1e6 else f"{r:.{CAPTION_FIGURES}g}"
    return text.replace("-", "−")


def caption(d: Declaration) -> str:
    """One caption paragraph generated from the declaration (profile 8.2 and 9.4.2)."""
    unit = "" if d.data_unit == "1" else f" {d.data_unit}"
    rule, _, why = d.knee_rule.partition(";")
    rule, why = rule.strip(), why.strip()
    scale_name = "in proportion to the value" if d.stretch == "proportional" else f"on {'an' if d.stretch == 'asinh' else 'a'} {d.stretch} scale"
    parts = [(f"Colour shows the data {scale_name} from {_number(d.range_low + d.stretch_offset)} to "
             f"{_number(d.range_high + d.stretch_offset)}{unit}."),
             f"White is at {_number(d.knee)}{unit}" + (f" ({why})" if why else f" (a {rule} threshold)") + ".",
             (f"In the HDR version, values above it are brighter, up to {_number(d.reference_headroom)} × white at the "
             "top of the range; in the ordinary version they are white.")]
    if rule == "percentile":
        parts.append("The white level was set from a percentile of this image, so the figure is exploratory.")
    if d.masked:
        parts.append(f"{d.masked} invalid or saturated pixels are held at or below white.")
    if d.derived_mask == "isolated-3x3":
        held = (f"{d.derived_masked} isolated pixels above it are held at white" if d.derived_masked
                else "no pixel was held by that rule")
        parts.append(f"Values above white are shown brighter only where they fill a 3 × 3 block of pixels; {held}.")
    elif d.derived_mask:
        parts.append(f"{d.derived_masked} further pixels are held at white by the rule {d.derived_mask}.")
    if d.noise_model != "unknown":
        parts.append(f"Stated uncertainty: {d.noise_model}, parameter {_number(d.noise_parameter)}.")
    if d.rounding_to_noise is not None:
        limit = 1.0 if d.noise_model == "tolerance" else DATA_CRITERION_K
        worst = "more than 100 times" if d.rounding_to_noise > 100.0 else f"up to {d.rounding_to_noise:.2g} of"
        parts.append(f"Rounding to this file's levels is {worst} the stated uncertainty (a data record needs {limit:g} or less).")
    kind = ("a data record" if d.record == "data-record" else "a rendering, not a data record")
    parts.append(f"This file is {kind}: one stored level spans up to {_number(d.max_data_step)}{unit}.")
    return " ".join(parts)


def data_steps(mapping: Mapping, scaled_by: float | None = None,
               samples: int = 1 << 16) -> tuple[np.ndarray, np.ndarray]:
    """The stored levels across the declared range: where each run of data values sharing one code starts, and
    how wide it is, both in data units.

    It follows the 8-bit path of udeep.mpl.savefig_hdr: below the knee the code is the 8-bit sRGB colour of
    the SDR figure; above it, the 8-bit sRGB colour of the HDR figure scaled by `scaled_by` (default: the
    headroom). JPEG compression adds error on top of this and is not included. The range is sampled at
    `samples` points, so the result is good to about two parts in `samples` of the declared range."""
    t = np.linspace(0.0, 1.0, max(int(samples), 2))          # fewer than two positions are taken as two
    y = mapping.light_at(t)
    base = np.round(srgb_oetf(mapping.colours(mapping.light_at(t, 1.0))) * 255.0)
    scale_down = float(scaled_by or mapping.reference_headroom)
    lifted = np.round(srgb_oetf(np.clip(mapping.colours(y) / scale_down, 0.0, 1.0)) * 255.0)
    above = y > 1.0
    code = np.concatenate([np.where(above[:, None], lifted, base), above[:, None]], axis=1)
    edges = np.flatnonzero(np.any(code[1:] != code[:-1], axis=1)) + 1          # first sample of each new code
    x = mapping.value_at(np.concatenate([[0.0], t[edges], [1.0]]))
    return x[:-1], np.diff(x)


def data_steps_pq16(mapping: Mapping, sdr_white_nits: float = REF_WHITE_NITS,
                    samples: int = 1 << 20) -> tuple[np.ndarray, np.ndarray]:
    """The stored levels of a 16-bit PQ PNG (udeep.export.write_png_pq) across the declared range: where each run
    of data values sharing one code starts, and how wide it is, both in data units.

    Unlike the 8-bit path of data_steps there is one code for each colour and no split at the knee. Such a file
    has tens of thousands of levels, so the range is sampled at about a million points by default. Give the
    result to rounding_to_noise as `steps` to see whether the file may be a data record (profile 7.3.1)."""
    t = np.linspace(0.0, 1.0, max(int(samples), 2))
    code = pq16_codes(mapping.colours(mapping.light_at(t)), sdr_white_nits)
    edges = np.flatnonzero(np.any(code[1:] != code[:-1], axis=1)) + 1
    x = mapping.value_at(np.concatenate([[0.0], t[edges], [1.0]]))
    return x[:-1], np.diff(x)


REFINE_STEPS = 64          # how often values_pq16 may halve a gap between samples that skips a code


def _skips(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Whether two colours' codes are more than one step apart in some channel, so that codes lie between them."""
    return np.abs(a.astype(np.int64) - b.astype(np.int64)).max(axis=-1) > 1


def _key(code: np.ndarray) -> np.ndarray:
    """Three 16-bit codes as one number, for looking them up."""
    code = code.astype(np.int64)
    return (code[..., 0] << 32) | (code[..., 1] << 16) | code[..., 2]


def values_pq16(mapping: Mapping, light, sdr_white_nits: float = REF_WHITE_NITS,
                samples: int = 1 << 20) -> np.ndarray:
    """The data values that a 16-bit PQ PNG's light stands for (`light` as udeep.read_png_pq gives it): the
    declared mapping turned back, through the file's own codes, whatever the colour map.

    Each stored level is a run of data values that share one code in all three channels (data_steps_pq16). A
    pixel is given the middle of the run whose code it holds, and so comes back to within half that run's width,
    which is at most half of MaxDataStep. Near black PQ codes change faster than any even sampling can follow:
    there, where two neighbouring samples are more than one code apart in some channel, the gap between them is
    halved, and halved again, until no piece of it skips a code (at most REFINE_STEPS times). The ends of the runs
    are then found to within a millionth of the spacing around them. A writer that rounds a sum of three products in a different order can leave a rare code one step
    from the reference's (udeep.core); a pixel whose code is in no run takes the nearest run that is a step away
    in each channel at most, and a pixel with none so near is not a colour of this mapping and is NaN.

    ValueError when two separate runs share one code: that code would not say which data value it stands for. It
    has not happened with any map tried (grey, fire and ice, linear over 0 to 4095 at a 4x headroom)."""
    light = np.asarray(light, dtype=np.float64)
    if light.ndim < 1 or light.shape[-1] != 3:
        raise ValueError("light must end in an axis of 3 channels")
    code_at = lambda s: pq16_codes(mapping.colours(mapping.light_at(s)), sdr_white_nits)
    t = np.linspace(0.0, 1.0, max(int(samples), 2))
    code = code_at(t)
    gap = np.flatnonzero(_skips(code[:-1], code[1:]))
    lo, hi, low, high = t[gap], t[gap + 1], code[gap], code[gap + 1]
    extra_t, extra_code = [], []
    for _ in range(REFINE_STEPS):                         # breadth first: every piece that still skips, halved
        mid = 0.5 * (lo + hi)
        inside = (mid > lo) & (mid < hi)                  # a piece too small to halve in float64 stays as it is
        lo, hi, low, high, mid = lo[inside], hi[inside], low[inside], high[inside], mid[inside]
        if not mid.size:
            break
        between = code_at(mid)
        extra_t.append(mid); extra_code.append(between)
        left, right = _skips(low, between), _skips(between, high)
        lo, hi = np.concatenate([lo[left], mid[right]]), np.concatenate([mid[left], hi[right]])
        low, high = np.concatenate([low[left], between[right]]), np.concatenate([between[left], high[right]])
    if extra_t:
        more_t, more_code = np.concatenate(extra_t), np.concatenate(extra_code)
        order = np.argsort(more_t)
        at = np.searchsorted(t, more_t[order])
        t, code = np.insert(t, at, more_t[order]), np.insert(code, at, more_code[order], axis=0)
    edges = np.flatnonzero(np.any(code[1:] != code[:-1], axis=1)) + 1
    lo, hi, before = t[edges - 1], t[edges], code[edges - 1]
    for _ in range(20):                                   # each change of code, to a millionth of a sample's spacing
        mid = 0.5 * (lo + hi)
        same = np.all(code_at(mid) == before, axis=1)
        lo, hi = np.where(same, mid, lo), np.where(same, hi, mid)
    x = mapping.value_at(np.concatenate([[0.0], hi, [1.0]]))
    middle = 0.5 * (x[:-1] + x[1:])
    keys = _key(code[np.concatenate([[0], edges])])
    order = np.argsort(keys, kind="stable")
    ordered = keys[order]
    if np.any(ordered[1:] == ordered[:-1]):
        raise ValueError("two separate runs of data values share one code, so the code does not say which value "
                         "it stands for: this mapping's colours cannot be read back as data")

    def find(k):
        at = np.minimum(np.searchsorted(ordered, k), len(ordered) - 1)
        return np.where(ordered[at] == k, order[at], -1)

    pixel = pq16_codes(light, sdr_white_nits).reshape(-1, 3).astype(np.int64)  # the file's codes, exactly
    run = find(_key(pixel))
    missing = np.flatnonzero(run < 0)
    if missing.size:                                      # the nearest run a step away, by the fewest channels moved
        near, moved = np.full(missing.size, -1), np.full(missing.size, 4)
        for step in np.array(np.meshgrid(*[[-1, 0, 1]] * 3, indexing="ij")).reshape(3, -1).T:
            if not step.any():
                continue
            found = find(_key(np.clip(pixel[missing] + step, 0, 65535)))
            better = (found >= 0) & (np.abs(step).sum() < moved)
            near, moved = np.where(better, found, near), np.where(better, np.abs(step).sum(), moved)
        run[missing] = near
    values = np.where(run >= 0, middle[np.maximum(run, 0)], np.nan)
    return values.reshape(light.shape[:-1])


def read_png_data(path, samples: int = 1 << 20) -> tuple[np.ndarray, Declaration]:
    """A 16-bit PQ PNG read back as data: the data values its pixels stand for under the declaration it carries
    (values_pq16), height x width, and that declaration. A value outside the declared range was stored at the
    range's end, and a masked pixel holds its fair SDR light instead of its value (profile 6.2a), so neither comes
    back as it was. The file need not declare itself a data record; if it does not, the values are good only to
    within its levels, which may be wider than the data's uncertainty.

    ValueError if the file is not a 16-bit PQ PNG, carries no declaration, or its colour map is one this reader
    has no table for."""
    d = read_metadata(path)
    if d is None:
        raise ValueError(f"{path}: the file carries no udeep declaration, so its light cannot be turned into data")
    return values_pq16(d.mapping, read_png_pq(path), samples=samples), d


def max_data_step(mapping: Mapping, scaled_by: float | None = None, samples: int = 1 << 16) -> float:
    """The widest run of data values that share one stored code, in data units (profile 7.2.3: MaxDataStep)."""
    return float(data_steps(mapping, scaled_by, samples)[1].max())


def rounding_to_noise(mapping: Mapping, noise: tuple, scaled_by: float | None = None,
                      steps: tuple | None = None) -> dict | None:
    """The data criterion (profile 7.2.1) for a file's stored levels, or None when the noise is unknown.

    For a noise model, each level's rounding error (its width / sqrt(12)) is divided by the data's standard
    deviation at that level; the limit is K = 0.1. For a declared tolerance, the largest rounding error (half the
    width) is divided by the tolerance; the limit is 1.

    The noise is taken at the point of each level where it is smallest, so a level meets the criterion only if
    all of it does. Poisson and relative noise grow away from zero, so that point is the one nearest zero,
    min(max(0, start), start + width): the end of the level nearer zero, or zero itself when the level contains
    it. At zero the noise is zero and the ratio infinite, so a range that reaches zero cannot meet the criterion
    (7.2.1). Constant noise and a tolerance are the same everywhere, and are reported at the middle of the level.

    Returns the worst ratio (the first, if several levels share it), the data value where it was taken, the
    limit, whether the whole declared range meets it, and the fraction of the range that does not."""
    model, parameter = noise
    if model == "unknown":
        return None
    start, width = steps if steps is not None else data_steps(mapping, scaled_by)
    at = start + 0.5 * width
    nearest_zero = np.minimum(np.maximum(0.0, start), start + width)
    if model == "tolerance":
        limit, error, sigma = 1.0, 0.5 * width, np.full_like(width, float(parameter))
    else:
        limit, error = DATA_CRITERION_K, width / np.sqrt(12.0)
        if model == "constant":
            sigma = np.full_like(width, float(parameter))
        elif model == "poisson":
            at, sigma = nearest_zero, np.sqrt(np.maximum(nearest_zero, 0.0) * float(parameter))
        elif model == "relative":
            at, sigma = nearest_zero, float(parameter) * np.abs(nearest_zero)
        else:
            raise ValueError(f"unknown noise model {model!r}")
    with np.errstate(divide="ignore"):
        ratio = np.where(sigma > 0.0, error / np.where(sigma > 0.0, sigma, 1.0), np.inf)
    worst = int(np.argmax(ratio))
    failing = ratio > limit
    return {"worst": float(ratio[worst]), "at": float(at[worst]), "limit": limit, "meets": not bool(failing.any()),
            "failing_fraction": float(width[failing].sum() / width.sum())}
