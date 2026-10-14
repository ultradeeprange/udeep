"""Check a file against its own declaration: the conformance points of the profile (v0.3, section 10).

    from udeep.validate import validate
    report = validate("figure.jpg")              # what the file alone can show
    print(report); report.ok

    import udeep.mpl as hp
    report = hp.validate(fig, "figure.jpg")      # also compares the pixels with the figure that made it

    python -m udeep.validate figure.jpg ...    # exit status 1 if any file fails

Two kinds of file are checked: the gain-map JPEG, which is always a rendering, and the 16-bit PQ PNG
(udeep.export.write_png_pq), which may declare itself a data record. For the PNG the check that matters is that
claim: the file's levels are worked out again and set against the stated uncertainty (profile 7.3.1).

A file carries its declared mapping but not its data, so on its own it can show that the declaration is
complete and consistent, that the mapping can be inverted, that nothing exceeds the headroom and that the stated
precision is right. Whether the pixels are that mapping of the data (points 3 to 5) needs the figure as well.

Files come from anyone (design decision D24), so `validate` never raises on one: whatever cannot be read or
checked becomes a failed check that says what it was.
"""
from __future__ import annotations

import importlib
import io
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, JpegImagePlugin

from udeep.color import REF_WHITE_NITS
from udeep.export import read_png_pq, srgb_oetf
from udeep.gainmap import read_gainmap_jpeg, read_iso_metadata
from udeep.metadata import (
    Declaration,
    data_steps,
    data_steps_pq16,
    digest_data,
    digest_pixels,
    missing_properties,
    packet,
    parse,
    rounding_to_noise,
)

cm = importlib.import_module("udeep.cmap")           # the package also exports a function called cmap

MIN_SLOPE = 1e-3                 # a slope below this fraction of the average counts as flat (profile 6.3)
HEADROOM_TOLERANCE = 1.05        # profile section 10: a lossy gain map stays within ~5 % of the headroom
STEP_TOLERANCE = 0.01            # relative agreement required of the stated MaxDataStep
BASE_MEAN_CODES = 0.5            # mean difference allowed between the base and the fair SDR figure put through the
BASE_OUTLIER_CODES, BASE_OUTLIER_FRACTION = 8.0, 1e-3       # file's own JPEG quantisation, in 8-bit codes
HDR_MEDIAN_ERROR = 0.03          # median relative error allowed on the lifted pixels
LEAK_LEVEL, LEAK_FRACTION = 1.10, 1e-4      # pixels that must stay at or below white, and how many may ring above
NEEDS_FIGURE = (("3", "sdr base"), ("4", "hdr rendering"), ("5", "held pixels"))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_HEADROOM_TOLERANCE = 1.001   # a 16-bit PQ PNG is lossless: only its own rounding may pass the headroom


@dataclass(frozen=True)
class Check:
    """One check made on one file: the conformance point it belongs to, its name, how it came out, and what was
    found, in words."""

    point: str                   # the conformance point, as numbered in profile section 10
    name: str
    status: str                  # "pass", "fail" or "skipped"
    detail: str


@dataclass
class Report:
    """What `validate` found for one file: the checks in the order they were made, and the declaration the file
    carries, if one could be read. As text (`str(report)`) it is a line that says whether the file conforms, and
    then a line for each check."""

    path: str
    checks: list[Check] = field(default_factory=list)
    declaration: Declaration | None = None

    @property
    def ok(self) -> bool:
        """True if no check failed. A skipped check is not a failure: it could not be made from what was given."""
        return not any(c.status == "fail" for c in self.checks)

    def status(self, name: str) -> str:
        """How the check called `name` came out: "pass", "fail" or "skipped". StopIteration if no check has
        that name."""
        return next(c.status for c in self.checks if c.name == name)

    def add(self, point: str, name: str, status: str, detail: str) -> None:
        """Record one check's result."""
        self.checks.append(Check(point, name, status, detail))

    def __str__(self) -> str:
        lines = [f"{self.path}: {'conforms' if self.ok else 'DOES NOT CONFORM'}"
                 + ("" if all(c.status != "skipped" for c in self.checks) else " (as far as checked)")]
        lines += [f"  [{c.status:^7}] {c.point} {c.name}: {c.detail}" for c in self.checks]
        return "\n".join(lines)


def _why(err: Exception) -> str:
    return f"{type(err).__name__}: {err}"


def _guarded(report: Report, names: tuple, what: str, check, *args):
    """Run one stage of the validation. If it raises, each of its checks not yet reported fails, saying what
    could not be read or checked: one bad value must not stop the report, or the files after this one."""
    done = len(report.checks)
    try:
        return check(report, *args)
    except Exception as err:  # noqa: BLE001 -- deliberately everything: a traceback is not a report
        reported = {c.name for c in report.checks[done:]}
        for point, name in names:
            if name not in reported:
                report.add(point, name, "fail", f"{what} cannot be read or checked: {_why(err)}")
        return None


def _capacity_max(data: bytes) -> float | None:
    m = re.search(rb'hdrgm:HDRCapacityMax="([^"]*)"', data)
    return None if m is None else float(m.group(1))


def _colour_map(d: Declaration) -> tuple[str, str]:
    """Whether the colours can be known from the file: ("known", ""), or ("unknown", why) when the map is one
    this reader has no table for, or ("fail", why) when the file breaks profile 6.10 or 8.1."""
    where = f"colour map {d.colour_map!r} at headroom {d.reference_headroom:g}"
    if d.colour_map is None:
        return "fail", "no colour map is declared"
    if d.colour_map == "grey":
        return "known", ""
    if d.colour_map_table is not None:
        try:
            cm.Table.from_text(d.colour_map, d.reference_headroom, d.colour_map_table)
        except (ValueError, TypeError) as err:              # not a count, not base64, or not two segments of nodes x 3
            return "fail", f"udeep:ColourMapTable cannot be read: {_why(err)}"
        return "known", ""
    if cm.parse(d.colour_map) is None:
        return "unknown", f"{where} is not registered here and the file carries no table"
    if not cm.is_registered(d.reference_headroom):
        return "fail", (f"udeep:ColourMapTable is absent: {where} is not a registered headroom, so the file must "
                        "carry the table (profile 6.10)")
    return "known", ""


def _metadata(report: Report, path, raw: bytes) -> tuple[Declaration, float] | None:
    """1. the metadata is there, complete and consistent. Returns the declaration and the gain map's headroom, or
    None when there is nothing further to check."""
    add = lambda status, detail: report.add("1", "metadata", status, detail)
    xmp = packet(raw)
    if xmp is None:
        return add("fail", "the file carries no udeep metadata")
    try:
        d, missing = parse(xmp), missing_properties(xmp)
    except (ValueError, TypeError) as err:
        return add("fail", f"the declaration is not valid: {err}")
    report.declaration = d
    try:
        cap = _capacity_max(raw)
        file_headroom = None if cap is None else float(2.0 ** cap)
        if file_headroom is not None and not 1.0 <= file_headroom < float("inf"):
            raise ValueError(f"it gives a headroom of {file_headroom!r} x white")
    except (ValueError, OverflowError) as err:
        return add("fail", f"the gain map's hdrgm:HDRCapacityMax cannot be read: {_why(err)}")
    if file_headroom is None:
        return add("fail", "no gain map found (hdrgm:HDRCapacityMax): the file is neither a gain-map JPEG nor a "
                           "16-bit PQ PNG, the two kinds that can be checked")
    problems = []
    if missing:
        problems.append("the file does not carry " + ", ".join(f"udeep:{name}" for name in missing)
                        + ", which every stored figure must (profile 8.1)")
    try:
        iso = read_iso_metadata(path)
        if iso is not None and abs(iso["alternate_hdr_headroom"] - cap) > 1e-5:
            problems.append(f"the ISO 21496-1 block gives a headroom of {2.0 ** iso['alternate_hdr_headroom']:.4g} x "
                            f"white but the XMP gives {file_headroom:.4g} x")
    except (ValueError, struct.error) as err:
        iso = None
        problems.append(f"the ISO 21496-1 block cannot be read: {err}")
    if abs(file_headroom / d.reference_headroom - 1.0) > 1e-6:
        problems.append(f"the gain map is authored for {file_headroom:.4g} x white but the declaration says "
                        f"{d.reference_headroom:.4g} x (profile 9.1)")
    colours, why = _colour_map(d)
    if colours == "fail" and d.colour_map is not None:             # an absent map is already among the missing
        problems.append(why)
    if problems:
        add("fail", "; ".join(problems))
    else:
        stated = (f"{d.stretch} stretch, knee {d.knee:.4g} {d.data_unit} ({d.knee_rule.split(';')[0]}), headroom "
                  f"{d.reference_headroom:.4g}; gain map described in XMP"
                  + (" and in ISO 21496-1 binary form" if iso is not None else " only"))
        add("pass", f"complete and consistent; {stated}" if colours == "known" else
            f"consistent, but it cannot be told whether anything is absent: {why}, which is required of a map that "
            f"is not registered (profile 6.10); {stated}")
    return d, file_headroom


def _mapping(report: Report, d: Declaration) -> None:
    """2. the declared mapping rises everywhere (slope > 0, end points included), and meets its end points."""
    add = lambda status, detail: report.add("2", "mapping", status, detail)
    t = np.linspace(0.0, 1.0, 200_001)
    y = d.mapping.light_at(t)
    at_knee = float(d.mapping.light(np.array([d.knee]))[0])
    slope = np.diff(y) / np.diff(t)                           # the stretch is monotone by construction: judge y(t)
    average = float(y[-1] - y[0])                             # the average slope, since t runs from 0 to 1
    floor = float(slope.min()) / average
    if not floor >= MIN_SLOPE:
        where = float(np.mean(slope < MIN_SLOPE * average))
        add("fail", f"not strictly increasing: the slope falls to {max(floor, 0.0):.2g} of the average, over "
                    f"{where:.2%} of the range (profile 6.3)")
    elif abs(y[-1] - d.reference_headroom) > 1e-9 or abs(at_knee - 1.0) > 1e-9:
        add("fail", "the mapping does not reach white at the knee and the headroom at the top of the range")
    else:
        add("pass", f"strictly increasing over the declared range (slope never below {floor:.2f} of the average); "
                    "white at the knee, the headroom at the top")


def _headroom(report: Report, path, d: Declaration, file_headroom: float):
    """4a. nothing exceeds the headroom. Returns the HDR rendering the file gives, for the checks on the pixels."""
    hdr = np.asarray(read_gainmap_jpeg(path, file_headroom), np.float64)
    peak = float(hdr.max())
    report.add("4", "headroom", "pass" if peak <= d.reference_headroom * HEADROOM_TOLERANCE else "fail",
               f"brightest value {peak:.3f} x white, against a headroom of {d.reference_headroom:.4g} "
               f"(up to {HEADROOM_TOLERANCE - 1:.0%} over is allowed for a lossy gain map)")
    return hdr


def _precision(report: Report, d: Declaration, file_headroom: float) -> None:
    """6. the stated precision. The comparisons are written so that a number that is not one (NaN) fails them."""
    add = lambda status, detail: report.add("6", "precision", status, detail)
    if d.record == "data-record":                             # whatever the colour map: this needs no table
        return add("fail", "an 8-bit gain-map JPEG is always a rendering, never a data record (profile 7.3.3)")
    colours, why = _colour_map(d)
    if colours != "known":
        return add("skipped", f"MaxDataStep cannot be recomputed: {why}")
    steps = data_steps(d.mapping, scaled_by=file_headroom)
    step = float(steps[1].max())
    against = rounding_to_noise(d.mapping, (d.noise_model, d.noise_parameter), steps=steps)
    stated, actual = d.rounding_to_noise, None if against is None else min(against["worst"], 1e9)
    if not abs(step - d.max_data_step) <= STEP_TOLERANCE * abs(d.max_data_step):
        return add("fail", f"MaxDataStep is stated as {d.max_data_step:.4g} but is {step:.4g} {d.data_unit}")
    if stated is not None and (actual is None or not abs(stated - actual) <= STEP_TOLERANCE * actual):
        return add("fail", f"RoundingToNoise is stated as {stated:.4g} but is "
                           + ("not defined for unknown noise" if actual is None else f"{actual:.4g}"))
    note = ""
    if against is not None:
        worst = "more than 100 times" if against["worst"] > 100.0 else f"{against['worst']:.2g} of"
        note = (f"; rounding is {worst} the stated uncertainty at its worst, "
                + ("within" if against["meets"] else "over") + f" the limit of {against['limit']:g} for a data record"
                + ("" if against["meets"] else f"; {100.0 * against['failing_fraction']:.2g} % of the range is over it"))
    add("pass", f"MaxDataStep {step:.4g} {d.data_unit} as stated; declared a rendering{note}")


def _link(report: Report, name: str, what: str, stated: str | None, given: str | None) -> None:
    """7. one link, to the data or to the SDR figure of record: the digest supplied against the one declared."""
    if stated is None:
        report.add("7", name, "skipped", f"the file declares no digest of {what}")
    elif given is None:
        report.add("7", name, "skipped", f"{what} was not supplied to compare with the declared digest")
    elif given == stated:
        report.add("7", name, "pass", f"{what} supplied is what the file was made from ({stated[:19]}…)")
    else:
        report.add("7", name, "fail", f"{what} supplied is not what the file was made from: its digest is "
                                      f"{given[:19]}…, the file declares {stated[:19]}…")


def _data_link(report: Report, d: Declaration, data, digest: str | None) -> None:
    _link(report, "data link", "the data", d.data_digest, digest if data is None else digest_data(data))


def _sdr_link(report: Report, d: Declaration, sdr, digest: str | None) -> None:
    if sdr is not None:
        digest = digest_pixels(np.asarray(Image.open(sdr).convert("RGB")))
    _link(report, "sdr link", "the SDR figure", d.sdr_digest, digest)


def _pixels(report: Report, path, hdr, expected: tuple) -> None:
    """3, 4b, 5. the pixels, against the figure."""
    want_sdr, want_hdr = (np.asarray(a, np.float64) for a in expected)
    base = np.asarray(read_gainmap_jpeg(path, 1.0), np.float64)
    if base.shape != want_sdr.shape:
        for point, name in NEEDS_FIGURE:
            report.add(point, name, "fail", f"the file is {base.shape[1]} x {base.shape[0]} pixels but the figure "
                                            f"renders {want_sdr.shape[1]} x {want_sdr.shape[0]}")
        return
    # "Within the delivery's quantisation" (point 3): put the fair SDR figure through the file's own JPEG
    # quantisation tables and chroma sampling, then compare decoded pixels.
    stored = Image.open(path)
    buf = io.BytesIO()
    Image.fromarray(np.round(srgb_oetf(want_sdr) * 255.0).astype(np.uint8), "RGB").save(
        buf, "JPEG", qtables=stored.quantization, subsampling=JpegImagePlugin.get_sampling(stored))
    diff = np.abs(np.asarray(stored.convert("RGB"), np.float64)
                  - np.asarray(Image.open(buf).convert("RGB"), np.float64)).max(-1)
    mean, outliers = float(diff.mean()), float(np.mean(diff > BASE_OUTLIER_CODES))
    report.add("3", "sdr base", "pass" if mean <= BASE_MEAN_CODES and outliers <= BASE_OUTLIER_FRACTION else "fail",
               f"differs from the fair SDR figure (at this file's JPEG quality) by {mean:.2f} codes on average; "
               f"{outliers:.3%} of pixels by more than {BASE_OUTLIER_CODES:.0f} codes")
    lifted = want_hdr.max(-1) > 1.0 + 1e-6
    if lifted.any():
        err = np.abs(hdr.max(-1)[lifted] / want_hdr.max(-1)[lifted] - 1.0)
        med = float(np.median(err))
        report.add("4", "hdr rendering", "pass" if med <= HDR_MEDIAN_ERROR else "fail",
                   f"{int(lifted.sum())} lifted pixels; median error {med:.2%} against the declared rendering")
    else:
        report.add("4", "hdr rendering", "fail", "the figure lifts nothing above white, so the file should not be an "
                                                 "HDR file")
    leak = float(np.mean(hdr.max(-1)[~lifted] > LEAK_LEVEL))
    report.add("5", "held pixels", "pass" if leak <= LEAK_FRACTION else "fail",
               f"{leak:.4%} of the pixels that must stay at or below white (masked pixels, axes, text, background) "
               f"are above {LEAK_LEVEL} x white")


def _png_metadata(report: Report, raw: bytes) -> Declaration | None:
    """1, for a PNG: the declaration is there, complete and consistent."""
    add = lambda status, detail: report.add("1", "metadata", status, detail)
    xmp = packet(raw)
    if xmp is None:
        return add("fail", "the file carries no udeep metadata")
    try:
        d, missing = parse(xmp), missing_properties(xmp)
    except (ValueError, TypeError) as err:
        return add("fail", f"the declaration is not valid: {err}")
    report.declaration = d
    problems = []
    if missing:
        problems.append("the file does not carry " + ", ".join(f"udeep:{name}" for name in missing)
                        + ", which every stored figure must (profile 8.1)")
    colours, why = _colour_map(d)
    if colours == "fail" and d.colour_map is not None:
        problems.append(why)
    if problems:
        add("fail", "; ".join(problems))
    else:
        stated = (f"{d.stretch} stretch, knee {d.knee:.4g} {d.data_unit} ({d.knee_rule.split(';')[0]}), headroom "
                  f"{d.reference_headroom:.4g}; a 16-bit PQ PNG")
        add("pass", f"complete and consistent; {stated}" if colours == "known" else
            f"consistent, but it cannot be told whether anything is absent: {why}; {stated}")
    return d


def _png_light(report: Report, path, d: Declaration):
    """4a, for a PNG: nothing exceeds the headroom. Returns the light the file holds."""
    light = read_png_pq(path)                                  # ValueError if it is a PNG of another kind
    peak = float(light.max())
    report.add("4", "headroom", "pass" if peak <= d.reference_headroom * PNG_HEADROOM_TOLERANCE else "fail",
               f"brightest value {peak:.4f} x white, against a headroom of {d.reference_headroom:.4g}; white is taken "
               f"as {REF_WHITE_NITS:g} cd/m2, which the file does not state")
    return light


def _png_precision(report: Report, d: Declaration) -> None:
    """6, for a PNG: the stated precision, and above all the claim to be a data record (profile 7.3.1)."""
    add = lambda status, detail: report.add("6", "precision", status, detail)
    colours, why = _colour_map(d)
    if colours != "known":
        return add("skipped", f"the levels cannot be worked out: {why}")
    steps = data_steps_pq16(d.mapping)
    step = float(steps[1].max())
    against = rounding_to_noise(d.mapping, (d.noise_model, d.noise_parameter), steps=steps)
    stated, actual = d.rounding_to_noise, None if against is None else min(against["worst"], 1e9)
    if not abs(step - d.max_data_step) <= STEP_TOLERANCE * abs(d.max_data_step):
        return add("fail", f"MaxDataStep is stated as {d.max_data_step:.4g} but is {step:.4g} {d.data_unit}")
    if stated is not None and (actual is None or not abs(stated - actual) <= STEP_TOLERANCE * actual):
        return add("fail", f"RoundingToNoise is stated as {stated:.4g} but is "
                           + ("not defined for unknown noise" if actual is None else f"{actual:.4g}"))
    levels = f"{len(steps[1]):,} levels, MaxDataStep {step:.4g} {d.data_unit} as stated"
    if against is None:
        return add("pass", f"{levels}; declared a rendering, and with the noise unknown it could be nothing else")
    worst = "more than 100 times" if against["worst"] > 100.0 else f"{against['worst']:.2g} of"
    measure = f"rounding is {worst} the stated uncertainty at its worst, against a limit of {against['limit']:g}"
    if d.record == "data-record" and not against["meets"]:
        return add("fail", f"{levels}; declared a data record, but {measure}: "
                           f"{100.0 * against['failing_fraction']:.2g} % of the range is over it (profile 7.3.1)")
    if d.record == "data-record":
        return add("pass", f"{levels}; a data record, as declared: {measure}")
    add("pass", f"{levels}; declared a rendering: {measure}"
                + (", so it could have been declared a data record" if against["meets"] else ""))


def _png_data(report: Report, d: Declaration, light, data) -> None:
    """7, for a PNG that says it is a data record: the data comes back out of it."""
    add = lambda status, detail: report.add("7", "data read back", status, detail)
    if data is None:
        return add("skipped", "the data was not supplied to compare with what the file holds")
    if d.colour_map != "grey":
        return add("skipped", f"the colour map is {d.colour_map}: turning a colour back into a value is not done here")
    values = np.asarray(data, dtype=np.float64)
    back = d.mapping.value(np.asarray(light)[..., 1])          # grey: any channel is the light
    if back.shape != values.shape:
        return add("fail", f"the file is {back.shape[1]} x {back.shape[0]} and the data {values.shape}")
    inside = np.isfinite(values) & (values >= d.range_low) & (values <= d.range_high)
    worst = float(np.abs(back - values)[inside].max()) if inside.any() else 0.0
    limit = 0.5 * d.max_data_step * (1.0 + STEP_TOLERANCE)
    add("pass" if worst <= limit else "fail",
        f"every value inside the declared range is read back to within {worst:.3g} {d.data_unit} "
        f"(half the widest step is {0.5 * d.max_data_step:.3g})"
        + (f"; {int((~inside).sum()):,} values lie outside the range and are not held" if (~inside).any() else ""))


def _validate_png(report: Report, path, raw: bytes, data, data_digest: str | None) -> Report:
    d = _guarded(report, (("1", "metadata"),), "the metadata", _png_metadata, raw)
    if d is None:
        return report
    _guarded(report, (("2", "mapping"),), "the declared mapping", _mapping, d)
    light = _guarded(report, (("4", "headroom"),), "the image", _png_light, path, d)
    _guarded(report, (("6", "precision"),), "the stated precision", _png_precision, d)
    _guarded(report, (("7", "data link"),), "the data supplied", _data_link, d, data, data_digest)
    if light is not None:
        _guarded(report, (("7", "data read back"),), "the data supplied", _png_data, d, light, data)
    for point, name in (("7", "sdr link"), *NEEDS_FIGURE):
        report.add(point, name, "skipped", "applies to a gain-map JPEG: this file holds one image and no SDR base")
    return report


def validate(path, expected: tuple | None = None, data=None, sdr=None, data_digest: str | None = None,
             sdr_digest: str | None = None) -> Report:
    """Check the gain-map JPEG, or the 16-bit PQ PNG, at `path`. `expected` is (sdr, hdr): the fair SDR figure and the HDR figure as
    linear RGB arrays in multiples of SDR white, for the checks that need the pixels (udeep.mpl.validate
    supplies them from a figure). `data` is the array the figure was made from and `sdr` the path of its SDR figure
    of record: each is compared with the digest the file declares (or pass the digests themselves).

    It does not raise on a file, whatever the file holds: see the report's checks."""
    report = Report(str(path))
    try:
        raw = Path(path).read_bytes()
    except OSError as err:
        report.add("1", "metadata", "fail", f"the file cannot be read: {_why(err)}")
        return report
    if raw[:8] == PNG_SIGNATURE:
        return _validate_png(report, path, raw, data, data_digest)
    found = _guarded(report, (("1", "metadata"),), "the metadata", _metadata, path, raw)
    if found is None:
        return report
    d, file_headroom = found
    _guarded(report, (("2", "mapping"),), "the declared mapping", _mapping, d)
    hdr = _guarded(report, (("4", "headroom"),), "the gain map", _headroom, path, d, file_headroom)
    _guarded(report, (("6", "precision"),), "the stated precision", _precision, d, file_headroom)
    _guarded(report, (("7", "data link"),), "the data supplied", _data_link, d, data, data_digest)
    _guarded(report, (("7", "sdr link"),), f"the SDR figure supplied ({sdr})", _sdr_link, d, sdr, sdr_digest)
    for point, name in NEEDS_FIGURE:
        if expected is None:
            report.add(point, name, "skipped", "needs the figure that made the file: use udeep.mpl.validate(fig, path)")
        elif hdr is None:
            report.add(point, name, "fail", "the gain map cannot be read, so the pixels cannot be compared")
    if expected is not None and hdr is not None:
        _guarded(report, NEEDS_FIGURE, "the pixels", _pixels, path, hdr, expected)
    return report


def main(argv: list[str] | None = None) -> int:
    """The command line, `python -m udeep.validate FILE [FILE ...]`: prints each file's report. With one file,
    `--sdr FIGURE.png` also checks the SDR figure of record. Returns the exit status: 0 if every file conforms,
    1 if one does not, 2 for a command line it cannot use."""
    paths = list(sys.argv[1:] if argv is None else argv)
    sdr = None
    if "--sdr" in paths:
        at = paths.index("--sdr")
        sdr = paths[at + 1] if at + 1 < len(paths) else None
        del paths[at:at + 2]
    if not paths or (sdr is not None and len(paths) != 1):
        print("usage: python -m udeep.validate FILE [FILE ...]            (gain-map JPEGs and 16-bit PQ PNGs)\n"
              "       python -m udeep.validate FILE.jpg --sdr FIGURE.png    (also check the SDR figure of record)")
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    reports = [validate(p, sdr=sdr) for p in paths]
    print("\n".join(str(r) for r in reports))
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
