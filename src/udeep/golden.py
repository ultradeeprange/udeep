"""Golden vectors: the contract every implementation must reproduce (design decision D14).

    python -m udeep.golden        # rewrite golden.json after a deliberate change

golden.json is language-neutral. Each case has a `kind` (what to compute), `args`, the expected `out`, and how
closely a result must agree: `tol` = {"rel", "abs"} for numbers (|got - want| <= abs + rel * |want|), or
`exact` for text, digests and bytes. NaN is written as null. The tolerances are the written ones of decision D8:
tight where an implementation in float64 must agree with the reference, looser where float32 tables or GPU
arithmetic are involved.

Kinds: scale, tone, pq_encode, srgb_oetf, cmap (a table lookup), table (a shipped table's digest), mapping (a
parameter record: position, light, fair SDR light, the inverse, colours), levels (stored levels and the data
criterion, for the 8-bit gain-map path), levels_pq16 (the same for a 16-bit PQ PNG), mask_isolated, digest_data, digest_pixels, digest_data_sha3, digest_pixels_sha3 (the same with SHA3-256), xmp (a
declaration's serialised form), caption, iso21496
(the gain-map metadata block's bytes).

A levels_pq16 case is a levels case for a 16-bit PQ PNG with white at `white_nits` (203 cd/m2, profile 9.1.1): one
code for each colour, no split at the knee. Its mappings are the grey forms of the mapping records. Sixteen-bit codes
are fine enough that a float32 colour table, which ports may interpolate in other arithmetic, would move codes that
sit near a rounding tie; with grey the case pins the PQ path itself, and the colours are pinned by the mapping cases.
Each case is checked, when it is made, to keep every code under a relative change of 1e-12 in the light.

A levels case gives the noise as [model, parameter]. Its `rounding_to_noise` is the worst ratio as a file states
it, no larger than 1e9: the ratio is unbounded where the noise reaches zero, and JSON has no infinity. `at`, the
data value where the worst ratio was taken, is given for poisson and relative noise, and only where one level is
clearly the worst. Elsewhere (constant noise, a tolerance, relative noise on a log stretch) many levels share the
worst ratio to within rounding, so which of them is found is not part of the contract and `at` is left out.
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import math
from pathlib import Path

import numpy as np

from udeep import Mapping, masks, pq_encode, scale, tone
from udeep.export import srgb_oetf
from udeep.gainmap import _iso_metadata
from udeep.metadata import (
    Declaration,
    caption,
    data_steps,
    data_steps_pq16,
    description,
    digest_data,
    digest_pixels,
    rounding_to_noise,
)

cm = importlib.import_module("udeep.cmap")           # the package also exports a function called cmap

PATH = Path(__file__).with_name("golden.json")
FORMAT = 2
TIGHT = {"rel": 1e-12, "abs": 1e-12}          # float64 arithmetic against the float64 reference
TABLE = {"rel": 2e-6, "abs": 2e-6}            # through a float32 table, or float32 arithmetic (D8)
LEVELS = {"rel": 1e-9, "abs": 1e-9}           # counts of stored levels and their widths
UNBOUNDED = 1e9                               # what a file states for a ratio with no bound (udeep.mpl.declaration)
WHITE_NITS = 203.0                            # where a PQ file has its white (profile 9.1.1)
PQ16_SAMPLES = 262145                         # finer than the levels, so the count is the levels, not the samples
NOISE = {"poisson": 0.004, "relative": 0.05, "tolerance": 0.003}    # the models beside constant; see _noise_cases


def _plain(a):
    """Nested lists of Python floats; NaN becomes None (JSON has no NaN)."""
    if isinstance(a, dict):
        return {k: _plain(v) for k, v in a.items()}
    if isinstance(a, (str, bool, int)) or a is None:
        return a
    if isinstance(a, float):
        return None if math.isnan(a) else a
    return [_plain(v) for v in np.asarray(a, dtype=np.float64).tolist()]


def _mapping_cases() -> dict:
    records = {
        "linear_grey": Mapping.from_stretch({"kind": "linear", "lo": -160.0, "hi": 700.0}, 240.0, 4.0, "grey"),
        "offset_fire": Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 90.0, "offset": 10.0}, 55.0, 2.0, "fire"),
        "log_ice": Mapping.from_stretch({"kind": "log", "lo": 1.0, "hi": 1e4}, 100.0, 8.0, "ice"),
        "asinh_ice": Mapping.from_stretch({"kind": "asinh", "lo": 0.0, "hi": 20.0, "width": 0.1}, 1.2, 4.0, "ice"),
        "proportional_fire": Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 12.0}, 3.0, 4.0, "fire",
                                                  proportional=True),
        "capped_grey": Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 1.0}, 0.2, 2.0, "grey"),
    }
    # exit slopes other than the reference 1: the record carries them in above_knee, and every port must read them
    records["exit_half_grey"] = dataclasses.replace(records["linear_grey"], above_knee="hermite; exit=0.5")
    records["exit_two_ice"] = dataclasses.replace(records["log_ice"], above_knee="hermite; exit=2")
    cases = {}
    for name, m in records.items():
        inside = m.value_at(np.linspace(0.0, 1.0, 21))
        span = m.range_high - m.range_low
        x = np.concatenate([inside, [m.knee, inside[0] - 0.1 * span, inside[-1] + 0.1 * span, math.nan]])
        light = m.light(x)
        cases[f"mapping_{name}"] = {
            "kind": "mapping", "args": {"mapping": dataclasses.asdict(m), "x": _plain(x)},
            "out": {"knee_position": m.knee_position, "position": _plain(m.position(x)), "light": _plain(light),
                    "sdr_light": _plain(m.light(x, headroom=1.0)), "value_of_light": _plain(m.value(light)),
                    "colours": _plain(m.colours(light))},
            "tol": TIGHT if m.colour_map == "grey" else TABLE,
            "tables": [] if m.colour_map == "grey" else [cm._file(m.table.name, m.reference_headroom, cm.parse(m.colour_map)[1])]}
        steps = data_steps(m, samples=4097)
        for suffix, noise in [("", ("constant", 0.01 * span)), *_noise_cases(m)]:
            cases[f"levels_{name}{suffix}"] = {
                "kind": "levels", "args": {"mapping": dataclasses.asdict(m), "samples": 4097, "noise": list(noise)},
                "out": _levels(m, steps, noise), "tol": LEVELS, "tables": cases[f"mapping_{name}"]["tables"]}
        grey = dataclasses.replace(m, colour_map="grey", colour_map_table=None)
        if not _pq16_settled(grey):
            raise ValueError(f"levels_pq16_{name}: a code sits on a rounding tie; change PQ16_SAMPLES")
        noise = ("constant", 0.01 * span)
        cases[f"levels_pq16_{name}"] = {
            "kind": "levels_pq16", "args": {"mapping": dataclasses.asdict(grey), "samples": PQ16_SAMPLES,
                                             "white_nits": WHITE_NITS, "noise": list(noise)},
            "out": _levels(grey, data_steps_pq16(grey, WHITE_NITS, samples=PQ16_SAMPLES), noise), "tol": LEVELS,
            "tables": []}
    return cases


def _pq16_settled(m: Mapping) -> bool:
    """Whether every sampled code stays the same when the light moves by a relative 1e-12, far more than two
    float64 implementations differ by: then another port finds the same codes, and the same levels."""
    from udeep.export import pq16_codes
    light = m.colours(m.light_at(np.linspace(0.0, 1.0, PQ16_SAMPLES)))
    codes = pq16_codes(light, WHITE_NITS)
    return all(np.array_equal(pq16_codes(light * f, WHITE_NITS), codes) for f in (1.0 + 1e-12, 1.0 - 1e-12))


def _noise_cases(m: Mapping) -> list[tuple[str, tuple]]:
    """The other noise models, each with a parameter in proportion to the top of the range, so that part of the
    range meets the criterion and part does not. None is 1: a parameter of 1 hides multiplying for dividing."""
    top = max(abs(m.range_high + m.stretch_offset), abs(m.range_low + m.stretch_offset))
    return [(f"_{model}", (model, factor if model == "relative" else factor * top)) for model, factor in NOISE.items()]


def _levels(m: Mapping, steps: tuple, noise: tuple) -> dict:
    found = rounding_to_noise(m, noise, steps=steps)
    out = {"count": len(steps[0]), "max_data_step": float(steps[1].max()), "first_start": float(steps[0][0]),
           "rounding_to_noise": min(found["worst"], UNBOUNDED), "failing_fraction": found["failing_fraction"],
           "limit": found["limit"], "meets": found["meets"]}
    if not _settled(m, noise, steps, out):
        raise ValueError(f"the levels case for {noise} hangs on the last digits of a level's width: change the parameter")
    if noise[0] in ("poisson", "relative") and _settled(m, noise, steps, {**out, "at": found["at"]}):
        out["at"] = found["at"]
    return out


def _settled(m: Mapping, noise: tuple, steps: tuple, out: dict) -> bool:
    """Whether another implementation, whose level widths differ in the last digits, would find the same result:
    no level sits on the limit, and (where `at` is given) no second level ties with the worst."""
    rng = np.random.default_rng(1)
    for _ in range(8):
        width = steps[1] * (1.0 + 1e-9 * rng.uniform(-1.0, 1.0, len(steps[1])))
        other = rounding_to_noise(m, noise, steps=(steps[0], width))
        if abs(other["failing_fraction"] - out["failing_fraction"]) > 1e-7 or other["meets"] != out["meets"]:
            return False
        if "at" in out and abs(other["at"] - out["at"]) > 1e-7 * (m.range_high - m.range_low):
            return False
    return True


def _declaration() -> Declaration:
    m = Mapping.from_stretch({"kind": "asinh", "lo": 0.0, "hi": 16383.0, "width": 150.0}, 2625.5, 4.0, "fire")
    return Declaration(**dataclasses.asdict(m), data_unit='DN per 4 × 4 binned pixel & "quoted" <text>', data_unit_code="1",
                       knee_rule="percentile; the 99th percentile of the unmasked pixels", noise_model="poisson",
                       noise_parameter=0.25, masked=198, derived_mask=masks.ISOLATED, derived_masked=1797,
                       max_data_step=189.70123456789, rounding_to_noise=1.5e-3, record="rendering", preset="highlight",
                       data_digest="sha256:" + "ab" * 32, data_source="doi:10.0000/example; https://example.org/a.fits",
                       sdr_digest="sha256:" + "cd" * 32, data_digest_sha3="sha3-256:" + "ef" * 32,
                       sdr_digest_sha3="sha3-256:" + "01" * 32)


XMP_NOTE = ("one rdf:Description; properties in the order of the reference record; text XML-escaped; each float as "
            "the shortest text that reads back to the same float64 (udeep.metadata.float_text): plain digits "
            "when the decimal point falls after digit -3 to 16 of the shortest digits, with no '.0' on a whole "
            "number ('4', '16383', '0.25', '0.0001'); otherwise an exponent with a sign and at least two digits "
            "('1e+16', '1e-05', '1.5e-07')")


def _awkward_numbers() -> Declaration:
    """Numbers that ten-digit text could not carry: a narrow range on a large pedestal, and both exponent forms."""
    return Declaration(data_unit="s", stretch="linear", range_low=1700000000.0, range_high=1700000002.3456,
                       knee=1700000001.23456, knee_rule="physical; the start of the event", reference_headroom=4.0,
                       stretch_width=1.5e-07, stretch_offset=-1 / 3, max_data_step=1e-05, noise_model="constant",
                       noise_parameter=1e16, rounding_to_noise=0.1, colour_map="grey")


def make() -> dict:
    """Every golden case, worked out now by this package: the dictionary that golden.json is written from."""
    x = [-1.0, 0.0, 0.5, 1.0, 3.0, 10.0, 100.0, 1000.0, math.nan]
    t = np.linspace(0.0, 1.0, 11)
    y = np.concatenate([np.linspace(0.0, 4.0, 17), np.linspace(0.013, 3.987, 23), [-0.5, 4.5]])   # on nodes, between, outside
    cases = {}
    for kind, lo, hi in [("linear", 0.0, 10.0), ("log", 1.0, 1000.0), ("asinh", 0.0, 1000.0)]:
        cases[f"scale_{kind}"] = {"kind": "scale", "args": {"x": _plain(x), "kind": kind, "lo": lo, "hi": hi, "width": 1.0},
                                  "out": _plain(scale(x, kind, lo, hi, 1.0)), "tol": TIGHT}
    for knee, head in [(1.0, 1.0), (0.8, 4.0), (0.95, 8.0)]:
        cases[f"tone_{knee}_{head}"] = {"kind": "tone", "args": {"t": _plain(t), "knee": knee, "headroom": head},
                                        "out": _plain(tone(t, knee, head)), "tol": TIGHT}
    for knee, head in [(0.8, 2.0), (0.8, 4.0), (0.2, 1.2)]:   # (0.2, 1.2): capped entry slope
        ts = np.linspace(knee - 0.05, 1.0, 26)                 # dense from just below the knee
        cases[f"shoulder_{knee}_{head}"] = {"kind": "tone", "args": {"t": _plain(ts), "knee": knee, "headroom": head},
                                            "out": _plain(tone(ts, knee, head)), "tol": TIGHT}
        # the flat-ended roll-off (display rendering only): exit_slope 0, the v0.2 shoulder
        cases[f"rolloff_{knee}_{head}"] = {"kind": "tone",
                                           "args": {"t": _plain(ts), "knee": knee, "headroom": head, "exit_slope": 0.0},
                                           "out": _plain(tone(ts, knee, head, exit_slope=0.0)), "tol": TIGHT}
    knee, head = 0.8, 4.0
    ts = np.linspace(knee - 0.05, 1.0, 26)
    for slope in (0.5, 2.0):                                   # between the roll-off and the reference, and beyond it
        cases[f"shoulder_{knee}_{head}_exit_{slope}"] = {
            "kind": "tone", "args": {"t": _plain(ts), "knee": knee, "headroom": head, "exit_slope": slope},
            "out": _plain(tone(ts, knee, head, exit_slope=slope)), "tol": TIGHT}
    for name in ("fire", "ice"):
        cases[f"cmap_{name}_4"] = {"kind": "cmap", "args": {"y": _plain(y), "name": name, "headroom": 4.0,
                                                            "table": cm._file(name, 4.0)},
                                   "out": _plain(cm.cmap(y, name, 4.0)), "tol": TABLE}
    index = json.loads((cm.TABLES / "index.json").read_text(encoding="utf-8"))
    for key, entry in index["tables"].items():
        cases[f"table_{key}"] = {"kind": "table", "args": {"file": entry["file"], "headroom": entry["headroom"]},
                                 "out": {"sha256": entry["sha256"], "nodes": index["nodes"], "bytes": 2 * index["nodes"] * 3 * 4},
                                 "exact": True}
    nits = [0.0, 1.0, 100.0, 203.0, 1000.0, 4000.0, 10000.0]
    cases["pq_encode"] = {"kind": "pq_encode", "args": {"nits": nits}, "out": _plain(pq_encode(nits)), "tol": TIGHT}
    v = [0.0, 0.001, 0.0031308, 0.01, 0.18, 0.5, 1.0, 1.5, -0.2]
    cases["srgb_oetf"] = {"kind": "srgb_oetf", "args": {"v": v}, "out": _plain(srgb_oetf(v)), "tol": TIGHT}
    cases.update(_mapping_cases())

    grid = ["000000000000", "001000000111", "000000000111", "000000000000", "000000000000", "011111111000",
            "000000000000", "000000000000", "000000001110", "000000001110", "000000001110", "000000000000"]
    above = np.array([[c == "1" for c in row] for row in grid])
    cases["mask_isolated"] = {"kind": "mask_isolated", "args": {"above": grid},
                              "out": ["".join("1" if c else "0" for c in row) for row in masks.isolated(above)],
                              "exact": True}

    values = [[0.0, -0.0, 1.5], [math.nan, -2.25, 1e300]]
    cases["digest_data"] = {"kind": "digest_data", "args": {"shape": [2, 3], "values": _plain(values)},
                            "out": digest_data(values), "exact": True,
                            "note": "float64 little-endian, row order, after the text 'udeep-data-1;2x3;'; "
                                    "-0 counts as 0 and every NaN as the same NaN"}
    pixels = [[[0, 1, 2], [253, 254, 255]], [[10, 20, 30], [40, 50, 60]]]
    cases["digest_pixels"] = {"kind": "digest_pixels", "args": {"shape": [2, 2, 3], "values": pixels},
                              "out": digest_pixels(np.array(pixels, np.uint8)), "exact": True,
                              "note": "8-bit RGB, row order, after the text 'udeep-rgb8-1;2x2;'"}
    wide = [[[0, 1, 2], [253, 254, 255], [7, 8, 9]], [[10, 20, 30], [40, 50, 60], [70, 80, 90]]]
    cases["digest_pixels_2x3"] = {"kind": "digest_pixels", "args": {"shape": [2, 3, 3], "values": wide},
                                  "out": digest_pixels(np.array(wide, np.uint8)), "exact": True,
                                  "note": "2 rows of 3 pixels: the text is 'udeep-rgb8-1;2x3;', height then width"}
    # the same bytes with SHA3-256 (profile 8.6: DataDigestSha3 and SdrDigestSha3)
    cases["digest_data_sha3"] = {"kind": "digest_data_sha3", "args": cases["digest_data"]["args"],
                                 "out": digest_data(values, "sha3-256"), "exact": True,
                                 "note": "the bytes of digest_data, digested with SHA3-256 (FIPS 202)"}
    for name, array in (("digest_pixels_sha3", pixels), ("digest_pixels_2x3_sha3", wide)):
        cases[name] = {"kind": "digest_pixels_sha3", "args": {"shape": list(np.shape(array)), "values": array},
                       "out": digest_pixels(np.array(array, np.uint8), "sha3-256"), "exact": True,
                       "note": "the bytes of digest_pixels, digested with SHA3-256 (FIPS 202)"}

    d = _declaration()
    fields = {k: v for k, v in dataclasses.asdict(d).items() if v is not None}
    cases["xmp_description"] = {"kind": "xmp", "args": {"declaration": fields}, "out": description(d).decode("utf-8"),
                                "exact": True, "note": XMP_NOTE}
    awkward = _awkward_numbers()
    cases["xmp_description_floats"] = {
        "kind": "xmp", "args": {"declaration": {k: v for k, v in dataclasses.asdict(awkward).items() if v is not None}},
        "out": description(awkward).decode("utf-8"), "exact": True, "note": XMP_NOTE}
    cases["caption"] = {"kind": "caption", "args": {"declaration": fields}, "out": caption(d), "exact": True,
                        "note": "numbers to 6 significant figures (the rounding ratio to 2), written out in full "
                                "between 0.001 and 10000000, with U+2212 for a minus sign"}
    cases["iso21496_block"] = {"kind": "iso21496", "args": {"gain_map_min": -0.0125, "gain_map_max": 2.0, "hdr_capacity_max": 2.0},
                               "out": _iso_metadata(-0.0125, 2.0, 2.0).hex(), "exact": True,
                               "note": "hex of the gain map's ISO 21496-1 metadata; every rational has denominator 1000000"}
    return {"format": FORMAT, "profile": "0.3",
            "about": "Reference vectors for the UDeep Figure Profile. See udeep/golden.py for the kinds.",
            "cases": cases}


def agree(got, want, case: dict, where: str = "") -> list[str]:
    """Differences between a result and the expected output, under the case's own tolerance; empty if none."""
    if isinstance(want, dict):
        if not isinstance(got, dict) or got.keys() != want.keys():
            return [f"{where}: keys differ"]
        return [m for k in want for m in agree(got[k], want[k], case, f"{where}.{k}")]
    if isinstance(want, list):
        if not isinstance(got, list) or len(got) != len(want):
            return [f"{where}: length differs"]
        return [m for i, (g, w) in enumerate(zip(got, want, strict=True)) for m in agree(g, w, case, f"{where}[{i}]")]
    if want is None or isinstance(want, (str, bool)) or case.get("exact"):
        return [] if got == want else [f"{where}: {got!r} != {want!r}"]
    if got is None:
        return [f"{where}: NaN, expected {want!r}"]
    tol = case["tol"]
    return [] if abs(got - want) <= tol["abs"] + tol["rel"] * abs(want) else [f"{where}: {got!r} != {want!r}"]


def main() -> None:
    """The command line, `python -m udeep.golden`: writes golden.json from `make`."""
    # allow_nan=False: an infinity or a NaN that reaches here is not JSON, and another language could not read it
    PATH.write_text(json.dumps(make(), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(f"wrote {PATH}")


if __name__ == "__main__":
    main()
