"""The conformance files: small figure files, each made to pass or to break one rule of the profile, with the
verdict a validator must give, so that another implementation can show that it stores, finds and checks a
declaration as the reference does (profile 8.8 and section 10). The data is synthetic.

    python tools/make_conformance.py      # docs/conformance/: the files, expected.json and index.html

Each case names a file, and the data and the SDR figure of record to check it against where it needs them. The
verdicts in CASES are what the rules require; the files are checked against them as they are written, and nothing
is written over the committed set if one does not hold. tests/test_conformance.py checks the committed files the
same way. The files are written by whichever writer the package uses (the Rust core where it is present), so two
machines may write different bytes for the same figure; the verdicts are the same.
"""
from __future__ import annotations

import dataclasses
import html
import json
import re
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from build_profile_page import STYLE
from udeep import write_gainmap_jpeg, write_png_pq, write_png_sdr
from udeep.metadata import (
    Declaration,
    data_digests,
    data_steps,
    data_steps_pq16,
    description,
    digest_sdr,
    packet,
    replace_packet,
    rounding_to_noise,
    to_xmp,
)
from udeep.validate import validate
from udeep.view import hdr_view

OUT = ROOT / "docs" / "conformance"
STRETCH = {"kind": "linear", "lo": 0.0, "hi": 100.0}
NOISE = ("constant", 1.0)
SOURCE = "doi:10.0000/udeep-conformance-example"      # an example identifier: there is no such record

# name: (file, data, sdr, what it tests, {check: status the validator must give}, conforms)
CASES = {
    "valid": ("valid.jpg", "data", "valid_sdr.png", "a complete gain-map JPEG, with its data and its SDR figure of record",
              {"metadata": "pass", "mapping": "pass", "precision": "pass", "data link": "pass", "sdr link": "pass"}, True),
    "valid-other-data": ("valid.jpg", "other_data", None, "the same file against data one value apart (§10 point 7)",
                         {"metadata": "pass", "data link": "fail"}, False),
    "valid-other-sdr": ("valid.jpg", None, "other_sdr.png", "the same file against another SDR figure (§10 point 7)",
                        {"metadata": "pass", "sdr link": "fail"}, False),
    "record": ("record.png", "data", "valid_sdr.png", "a 16-bit PQ PNG data record of the same figure, read back as data",
               {"metadata": "pass", "mapping": "pass", "precision": "pass", "data link": "pass",
                "data read back": "pass", "sdr link": "pass"}, True),
    "missing-unit": ("missing_unit.jpg", None, None, "a required property absent: DataUnit (8.1)",
                     {"metadata": "fail"}, False),
    "not-a-number": ("not_a_number.jpg", None, None, "a number that is not one: MaxDataStep is nan (8.5)",
                     {"metadata": "fail"}, False),
    "sha3-alone": ("sha3_alone.jpg", None, None, "a SHA3-256 digest without its SHA-256 partner (8.5)",
                   {"metadata": "fail"}, False),
    "headroom": ("headroom.jpg", None, None, "the declared headroom is not the gain map's (9.1)",
                 {"metadata": "fail"}, False),
    "two-declarations": ("two_declarations.jpg", None, None, "two declarations in one packet (8.8)",
                         {"metadata": "fail"}, False),
    "elsewhere": ("elsewhere.jpg", None, None, "the declaration outside the primary image's XMP segment (8.8)",
                  {"metadata": "fail"}, False),
    "compressed": ("compressed.png", None, None, "the PNG's XMP chunk compressed (8.8)", {"metadata": "fail"}, False),
    "no-sdr-digest": ("no_sdr_digest.png", None, None, "a PNG with no SDR base that does not name its SDR figure (9.1.2)",
                      {"metadata": "fail"}, False),
}


def synthetic() -> np.ndarray:
    """40 x 60 counts: a ramp from 10 to 40 with a bright spot to 95, noise-free. Synthetic."""
    y, x = np.mgrid[0:40, 0:60].astype(float)
    return 10.0 + 30.0 * x / 59.0 + 60.0 * np.exp(-((x - 40.0) ** 2 + (y - 15.0) ** 2) / 30.0)


def declaration(view, data, steps, record: str) -> Declaration:
    against = rounding_to_noise(view.mapping, NOISE, steps=steps)
    sha256, sha3 = data_digests(data)
    return Declaration(
        **dataclasses.asdict(view.mapping), data_unit="counts", knee_rule="preset; half of full scale",
        noise_model=NOISE[0], noise_parameter=NOISE[1], max_data_step=float(steps[1].max()),
        rounding_to_noise=min(against["worst"], 1e9), record=record, preset="extend", masked=view.masked,
        derived_mask=view.derived_mask, derived_masked=view.derived_masked, data_digest=sha256, data_digest_sha3=sha3,
        data_source=SOURCE, sdr_digest=digest_sdr(view.sdr), sdr_digest_sha3=digest_sdr(view.sdr, "sha3-256"))


def edited(raw: bytes, edit) -> bytes:
    return replace_packet(raw, edit(packet(raw)))


def drop(name: str):
    def change(xmp: bytes) -> bytes:
        new, n = re.subn(rb' udeep:' + name.encode() + rb'="[^"]*"', b"", xmp)
        assert n == 1, name
        return new
    return change


def setting(name: str, value: str):
    def change(xmp: bytes) -> bytes:
        new, n = re.subn(rb'udeep:' + name.encode() + rb'="[^"]*"', f'udeep:{name}="{value}"'.encode(), xmp)
        assert n == 1, name
        return new
    return change


def write(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    data = synthetic()
    other = data.copy(); other[20, 30] += 0.5
    view = hdr_view(data, "extend", 50.0, STRETCH, 2.0, "fire")
    for name, array in (("data", data), ("other_data", other)):
        np.save(folder / f"{name}.npy", array)
        np.savetxt(folder / f"{name}.csv", array, delimiter=",", fmt="%.17g")
    write_png_sdr(view.sdr, folder / "valid_sdr.png")
    darker = np.asarray(view.sdr, dtype=np.float64).copy(); darker[0, 0] = 0.0
    write_png_sdr(darker, folder / "other_sdr.png")

    jpeg = declaration(view, data, data_steps(view.mapping, scaled_by=2.0), "rendering")
    write_gainmap_jpeg(view.sdr, view.hdr, folder / "valid.jpg", 2.0, description=description(jpeg))
    record = declaration(view, data, data_steps_pq16(view.mapping), "data-record")
    write_png_pq(view.hdr, folder / "record.png", xmp=to_xmp(record))

    raw = (folder / "valid.jpg").read_bytes()
    (folder / "missing_unit.jpg").write_bytes(edited(raw, drop("DataUnit")))
    (folder / "not_a_number.jpg").write_bytes(edited(raw, setting("MaxDataStep", "nan")))
    (folder / "sha3_alone.jpg").write_bytes(edited(raw, drop("DataDigest")))
    (folder / "headroom.jpg").write_bytes(edited(raw, setting("ReferenceHeadroom", "4")))
    one = re.search(rb"<rdf:Description[^>]*xmlns:udeep=[^>]*/>", packet(raw)).group(0)
    (folder / "two_declarations.jpg").write_bytes(edited(raw, lambda xmp: xmp.replace(one, one + one)))
    without = edited(raw, lambda xmp: xmp.replace(one, b""))
    comment = packet(raw)
    (folder / "elsewhere.jpg").write_bytes(without[:2] + b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big")
                                            + comment + without[2:])
    png = (folder / "record.png").read_bytes()
    (folder / "no_sdr_digest.png").write_bytes(edited(edited(png, drop("SdrDigest")), drop("SdrDigestSha3")))
    (folder / "compressed.png").write_bytes(compressed_xmp(png))


def compressed_xmp(png: bytes) -> bytes:
    """The PNG with its XMP iTXt chunk's text deflated, the compression flag set to 1."""
    i = png.index(b"iTXt") - 4
    n = int.from_bytes(png[i:i + 4], "big")
    body = png[i + 8:i + 8 + n]
    key, rest = body.split(b"\x00", 1)
    text = rest[4:]                                      # after the flag, the method, the empty language and keyword
    assert text.startswith(b"<x:xmpmeta")
    new = key + b"\x00\x01\x00\x00\x00" + zlib.compress(text)
    return png[:i] + len(new).to_bytes(4, "big") + b"iTXt" + new + zlib.crc32(b"iTXt" + new).to_bytes(4, "big") + png[i + 12 + n:]


def verdicts(folder: Path) -> dict:
    """What the reference validator says of each case, for the checks the case names."""
    out = {}
    for name, (file, data, sdr, _what, expect, _conforms) in CASES.items():
        arrays = None if data is None else np.load(folder / f"{data}.npy")
        r = validate(folder / file, data=arrays, sdr=None if sdr is None else folder / sdr)
        out[name] = ({check: r.status(check) for check in expect}, r.ok)
    return out


def check(folder: Path) -> list[str]:
    """Where the validator's verdicts differ from the ones the rules require."""
    got = verdicts(folder)
    return [f"{name}: {check} is {got[name][0][check]}, not {status}"
            for name, (_f, _d, _s, _w, expect, _c) in CASES.items() for check, status in expect.items()
            if got[name][0][check] != status] + \
           [f"{name}: conforms is {got[name][1]}, not {CASES[name][5]}" for name in CASES if got[name][1] != CASES[name][5]]


def expected() -> dict:
    return {"about": "UDeep conformance files: each case, the files to check, and the verdict a validator must give "
                     "for the checks named. The data is synthetic.",
            "profile": "UDeep Figure Profile draft v0.3",
            "cases": {name: {"file": f, "data": None if d is None else {"npy": f"{d}.npy", "csv": f"{d}.csv"},
                             "sdr": s, "tests": w, "checks": e, "conforms": c}
                      for name, (f, d, s, w, e, c) in CASES.items()}}


def page() -> str:
    rows = []
    for name, (f, d, s, what, expect, conforms) in CASES.items():
        files = [f'<a href="{html.escape(f)}"><code>{html.escape(f)}</code></a>']
        if d:
            files.append(f'data <a href="{d}.npy"><code>{d}.npy</code></a> / <a href="{d}.csv"><code>.csv</code></a>')
        if s:
            files.append(f'SDR figure <a href="{s}"><code>{s}</code></a>')
        verdict = ", ".join(f"{html.escape(c)} <b>{st}</b>" for c, st in expect.items())
        rows.append(f'      <tr><td>{name}</td><td>{"<br>".join(files)}</td><td>{html.escape(what)}</td>'
                    f'<td class="{"done" if conforms else "no"}">{verdict}; {"conforms" if conforms else "does not conform"}</td></tr>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Conformance files · UDeep</title>
<meta name="description" content="Small UDeep figure files, each made to pass or to break one rule of the profile, with the verdict a validator must give: for checking another implementation against the reference.">
<link rel="stylesheet" href="../fonts/plex.css">
<style>{STYLE}
  .single {{ padding-block: 36px 64px; }}
  pre {{ background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: 14px 16px; overflow-x: auto;
         font: 13px/1.55 var(--mono); color: var(--ink); }}
</style>
<!-- chrome:head -->
<!-- /chrome:head -->
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href="../index.html">U<b>Deep</b></a>
  <nav aria-label="Site"></nav>
</div></header>
<main><div class="wrap single">
  <article>
    <h1>Conformance files</h1>
    <p>Small figure files, each made to pass or to break one rule of the <a href="../profile/index.html">profile</a>,
    with the verdict a validator must give. Another implementation shows that it stores, finds and checks a
    declaration as the reference does by giving the same verdicts. The data is synthetic: a ramp with one bright
    spot, 40 by 60 values, noise-free. <a href="../anatomy/index.html">Anatomy of a UDeep file</a> takes the two
    valid files apart.</p>
    <p>The same cases in machine-readable form: <a href="expected.json"><code>expected.json</code></a>. With the
    reference implementation, a case is checked as</p>
    <pre><code>import numpy, udeep.validate
report = udeep.validate.validate("valid.jpg", data=numpy.load("data.npy"), sdr="valid_sdr.png")
print(report)     # or report.status("data link"), report.ok</code></pre>
    <p>or, for a file on its own, <code>python -m udeep.validate FILE</code>. Points that need the figure itself
    (the pixels, §10 points 3 to 5) are not part of these cases.</p>
    <div class="scroll"><table>
      <thead><tr><th>Case</th><th>Files</th><th>What it tests</th><th>A validator must report</th></tr></thead>
      <tbody>
{chr(10).join(rows)}
      </tbody>
    </table></div>
    <p class="muted">{html.escape(SOURCE)} is an example identifier: there is no such record.</p>
  </article>
</div></main>
<footer><div class="wrap"><p>UDeep Figure Profile, draft v0.3 · files and text under CC BY 4.0.</p></div></footer>
</body>
</html>
"""


def main() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as scratch:
        fresh = Path(scratch)
        write(fresh)
        wrong = check(fresh)
        if wrong:
            raise SystemExit("the files do not give the verdicts the rules require; nothing written:\n  " + "\n  ".join(wrong))
        OUT.mkdir(parents=True, exist_ok=True)
        for p in sorted(fresh.iterdir()):
            (OUT / p.name).write_bytes(p.read_bytes())
    (OUT / "expected.json").write_text(json.dumps(expected(), indent=1) + "\n", encoding="utf-8")
    (OUT / "index.html").write_text(page(), encoding="utf-8")
    print(f"wrote {len(CASES)} cases to {OUT}")


if __name__ == "__main__":
    main()
