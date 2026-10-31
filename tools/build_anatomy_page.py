"""Build docs/anatomy/index.html: a UDeep file taken apart, segment by segment and property by property, from the two
valid conformance files (docs/conformance/valid.jpg and record.png) as they are, and the meanings from section 8.1
of the profile, so that the page cannot drift from the files or the definition.

    python tools/build_anatomy_page.py
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from build_namespace_page import SRC, section_8_1
from build_profile_page import STYLE
from udeep.metadata import NAMESPACE, packet

FILES = ROOT / "docs" / "conformance"
OUT = ROOT / "docs" / "anatomy" / "index.html"
XMP_ID = b"http://ns.adobe.com/xap/1.0/\x00"
ISO_ID = b"urn:iso:std:iso:ts:21496:-1"
NAMES = {0xD8: "SOI", 0xD9: "EOI", 0xDA: "SOS", 0xDB: "DQT", 0xC0: "SOF0", 0xC2: "SOF2", 0xC4: "DHT", 0xDD: "DRI",
         0xFE: "COM"}


def jpeg_parts(data: bytes, start: int, end: int, primary: bool) -> list[tuple[int, int, str, str]]:
    """(offset, size, marker, what it holds) for one image of a gain-map JPEG, from its SOI to its EOI."""
    out, i = [(start, 2, "SOI", "start of the " + ("primary image" if primary else "gain-map image"))], start + 2
    while i < end:
        marker = data[i + 1]
        if marker == 0xDA:
            n = int.from_bytes(data[i + 2:i + 4], "big")
            what = ("the picture itself: the fair SDR figure, the one every screen and print shows" if primary else
                    "the gain map: how much brighter each pixel may be on an HDR screen")
            out.append((i, end - 2 - i, "SOS", f"start of scan, then the coded image data: {what}"))
            out.append((end - 2, 2, "EOI", "end of the " + ("primary image" if primary else "gain-map image")))
            return out
        n = int.from_bytes(data[i + 2:i + 4], "big")
        body = data[i + 4:i + 2 + n]
        name = f"APP{marker - 0xE0}" if 0xE0 <= marker <= 0xEF else NAMES.get(marker, f"0x{marker:02X}")
        if marker == 0xE1 and body.startswith(XMP_ID):
            what = ("XMP: the gain map's container description (<code>hdrgm</code>, <code>Container</code>) and "
                    "<b>the UDeep declaration</b> (profile 8.8)" if NAMESPACE.encode() in body else
                    "XMP: the gain map's own parameters (<code>hdrgm</code>)")
        elif marker == 0xE2 and body.startswith(ISO_ID):
            what = ("ISO 21496-1 gain-map metadata, binary: its version" if primary else
                    "ISO 21496-1 gain-map metadata, binary: the gain map's parameters")
        elif marker == 0xE2 and body.startswith(b"MPF"):
            what = "Multi-Picture Format index: where the gain-map image starts, and its size"
        elif marker == 0xE0:
            what = "JFIF header"
        elif marker == 0xDB:
            what = "quantisation table"
        elif marker in (0xC0, 0xC2):
            h, w = int.from_bytes(body[1:3], "big"), int.from_bytes(body[3:5], "big")
            what = f"frame: {w} × {h} pixels, {body[5]} components, {body[0]}-bit"
        elif marker == 0xC4:
            what = "Huffman table"
        else:
            what = ""
        out.append((i, n + 2, name, what))
        i += 2 + n
    raise ValueError("no scan found")


def png_parts(data: bytes) -> list[tuple[int, int, str, str]]:
    out, i = [(0, 8, "signature", "the eight bytes every PNG starts with")], 8
    idat = []
    while i < len(data):
        n = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8].decode("ascii")
        body = data[i + 8:i + 8 + n]
        if kind == "IHDR":
            w, h = int.from_bytes(body[0:4], "big"), int.from_bytes(body[4:8], "big")
            what = f"{w} × {h} pixels, {body[8]}-bit, RGB"
        elif kind == "cICP":
            what = (f"colour signalling {body[0]}, {body[1]}, {body[2]}, {body[3]}: BT.2020 primaries, the PQ transfer "
                    "function, RGB, full range (profile 9.1)")
        elif kind == "iTXt" and body.startswith(b"XML:com.adobe.xmp\x00"):
            what = ("<code>XML:com.adobe.xmp</code>, not compressed: <b>the UDeep declaration</b> (profile 8.8), "
                    "naming its SDR figure (9.1.2)")
        elif kind == "IDAT":
            idat.append(12 + n)
            i += 12 + n
            continue
        elif kind == "IEND":
            what = "the end"
        else:
            what = ""
        if idat:
            out.append((i - sum(idat), sum(idat), f"IDAT × {len(idat)}", "the image: PQ codes, 16 bits a channel, deflated"))
            idat = []
        out.append((i, 12 + n, kind, what))
        i += 12 + n
    return out


def table(parts) -> str:
    rows = "\n".join(f"      <tr><td><code>{html.escape(m)}</code></td><td>{o:,}</td><td>{s:,}</td><td>{w}</td></tr>"
                     for o, s, m, w in parts)
    return ('<div class="scroll"><table>\n      <thead><tr><th>Part</th><th>At byte</th><th>Bytes</th>'
            f"<th>What it holds</th></tr></thead>\n      <tbody>\n{rows}\n      </tbody>\n    </table></div>")


def bar(parts, total: int) -> tuple[str, set[int]]:
    """The file's bytes in proportion: the declaration and other metadata against the image data. With the sizes
    the segments are drawn at, each a class (`w<bytes>`, see main), as the site's pages carry no style= attribute."""
    spans = []
    for _o, s, m, w in parts:
        kind = ("decl" if "UDeep declaration" in w else "image" if m in ("SOS",) or m.startswith("IDAT") else "meta")
        if spans and spans[-1][0] == kind:
            spans[-1][1] += s
        else:
            spans.append([kind, s])
    cells = "".join(f'<span class="{k} w{s}"></span>' for k, s in spans)
    return f'<div class="bar" role="img" aria-label="{total:,} bytes in all">{cells}</div>', {s for _k, s in spans}


def meanings() -> dict[str, str]:
    """Each property's meaning, from the table of profile 8.1, to its first full stop or colon."""
    rows, out = section_8_1(SRC.read_text(encoding="utf-8"))[0].split("\n")[2:], {}
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        text = re.sub(r"\*(Added|Defined)[^*]*\*", "", cells[2]).strip()
        text = re.split(r"(?<=[a-z0-9)`])\. ", text)[0].rstrip(".")
        names = re.findall(r"`udeep:(\w+)`", cells[0])
        parts = text.split("; ")
        shared = len(parts) != len(names)                # "the asinh width; the value subtracted ...": one each
        for i, name in enumerate(names):
            out[name] = _inline(text if shared else parts[i])
    return out


def _inline(text: str) -> str:
    """A cell of the profile's table as HTML: code, italics, and bold dropped."""
    text = html.escape(text).replace("**", "")
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return re.sub(r"\*([^*]+)\*", r"<i>\1</i>", text)


def shortened(value: str) -> str:
    if len(value) <= 80:                                 # a digest is shown whole: it is the point
        return html.escape(value)
    return f"{html.escape(value[:44])}…{html.escape(value[-12:])} <span class=\"muted\">({len(value):,} characters)</span>"


def declaration_rows(xmp: bytes) -> str:
    element = re.search(rb"<rdf:Description[^>]*xmlns:udeep=[^>]*/>", xmp).group(0).decode("utf-8")
    props = re.findall(r'udeep:(\w+)="([^"]*)"', element)
    meaning = meanings()
    return "\n".join(f"      <tr><td><code>udeep:{name}</code></td><td><code>{shortened(html.unescape(value))}</code></td>"
                     f"<td>{meaning.get(name, '')}</td></tr>" for name, value in props)


def pretty(xmp: bytes) -> str:
    """The packet one attribute to a line, as it is in the file but for the line breaks."""
    text = xmp.decode("utf-8")
    text = re.sub(r'\s+(\w[\w:]*=")', r"\n    \1", text)
    text = re.sub(r"(<rdf:Description|<x:xmpmeta|<rdf:RDF|</rdf:RDF>|</x:xmpmeta>)", r"\n\1", text)
    lines = [line if len(line) <= 120 else line[:100] + f'…" ({len(line):,} characters)' for line in text.strip().split("\n")]
    return html.escape("\n".join(lines))


def main() -> None:
    jpg, png = (FILES / "valid.jpg").read_bytes(), (FILES / "record.png").read_bytes()
    gain_at = jpg.index(b"\xff\xd9\xff\xd8") + 2
    primary, gain = jpeg_parts(jpg, 0, gain_at, True), jpeg_parts(jpg, gain_at, len(jpg), False)
    chunks = png_parts(png)
    xmp = packet(jpg)
    (jpg_bar, jpg_sizes), (png_bar, png_sizes) = bar(primary + gain, len(jpg)), bar(chunks, len(png))
    widths = "".join(f"  .bar .w{s} {{ flex: {s} 0 0; }}\n" for s in sorted(jpg_sizes | png_sizes))
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Anatomy of a UDeep file · UDeep</title>
<meta name="description" content="A UDeep gain-map JPEG and a 16-bit PQ PNG taken apart, segment by segment, and the declaration they carry, property by property.">
<link rel="stylesheet" href="../fonts/plex.css">
<style>{STYLE}
  .single {{ padding-block: 36px 64px; }}
  article {{ max-width: none; }}
  article > p, article > ul {{ max-width: 78ch; }}
  .muted {{ color: var(--muted); }}
  pre {{ background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: 14px 16px; overflow-x: auto;
         font: 12.5px/1.55 var(--mono); color: var(--ink); }}
  .bar {{ display: flex; height: 22px; border-radius: 4px; overflow: hidden; border: 1px solid var(--line); margin: 6px 0 4px; }}
  .bar span {{ min-width: 3px; }}
  .bar .image {{ background: var(--line); }} .bar .meta {{ background: var(--muted); }} .bar .decl {{ background: var(--warm); }}
{widths}  .key {{ display: flex; gap: 18px; flex-wrap: wrap; font-size: 13px; color: var(--muted); }}
  .key i {{ display: inline-block; width: 12px; height: 12px; border-radius: 2px; vertical-align: -1px; margin-right: 6px; }}
  .key i.decl {{ background: var(--warm); }} .key i.meta {{ background: var(--muted); }} .key i.image {{ background: var(--line); }}
  details {{ margin: 10px 0; }} summary {{ cursor: pointer; color: var(--tag); }}
  table.declared td:nth-child(2) {{ width: 34%; }} table.declared td:nth-child(2) code {{ overflow-wrap: anywhere; }}
  table.declared td:nth-child(3) {{ width: 46%; }}
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
    <h1>Anatomy of a UDeep file</h1>
    <p>A UDeep figure is an ordinary image file with one thing added: a declaration of how its data became the
    picture, stored where the image format keeps metadata (<a href="../profile/index.html#8-declared-metadata">profile
    8.8</a>). Below, the two valid <a href="../conformance/index.html">conformance files</a> are taken apart as they
    are. Both show the same synthetic data, a ramp with one bright spot, 40 by 60 values; the page is made from the
    files, so it shows what they hold.</p>

    <h2>A gain-map JPEG</h2>
    <p><a href="../conformance/valid.jpg"><code>valid.jpg</code></a>, {len(jpg):,} bytes: two JPEG images in one file.
    The first is the ordinary figure, which any program shows. The second, after it, is the gain map, which a screen
    that can show HDR uses to make the bright parts brighter than white. The declaration is in the first image's XMP
    segment, beside the gain map's own description.</p>
    {jpg_bar}
    <p class="key"><span><i class="decl"></i>the declaration's segment</span><span><i class="meta"></i>other metadata</span><span><i class="image"></i>image data</span></p>
    <p class="muted">In a figure this small (60 by 40 pixels) the metadata is a large share of the file. In the
    18 HDR figures of this site's gallery, the declaration is 1.5 to 1.7 KiB of files from 184 KiB to 1 MiB, under
    1 % of each.</p>
    <h3>The primary image: the ordinary figure</h3>
    {table(primary)}
    <h3>The gain-map image</h3>
    {table(gain)}

    <h2>A 16-bit PQ PNG</h2>
    <p><a href="../conformance/record.png"><code>record.png</code></a>, {len(png):,} bytes: the same figure as a data
    record, fine enough that it reads back as the data. It has no ordinary SDR picture of its own, so its
    declaration names the one that goes with it, by digest.</p>
    {png_bar}
    {table(chunks)}

    <h2>The declaration</h2>
    <p>The properties the JPEG declares, in the order the reference writes them, each with its meaning from
    <a href="../ns/0.3/index.html">the namespace page</a>. The PNG's declaration is the same but for what depends on
    the encoding: the widest step, the rounding against the noise, and that it is a data record.</p>
    <div class="scroll"><table class="declared">
      <thead><tr><th>Property</th><th>Value in <code>valid.jpg</code></th><th>Meaning</th></tr></thead>
      <tbody>
{declaration_rows(xmp)}
      </tbody>
    </table></div>
    <details><summary>The whole XMP packet, one attribute to a line</summary>
    <pre><code>{pretty(xmp)}</code></pre></details>

    <h2>Where it must be, and what a reader does</h2>
    <ul>
      <li>One declaration a file, in one XMP packet. A reader takes it only from the place below, and refuses a file
        that holds two.</li>
      <li>In a gain-map JPEG, the primary image's XMP segment (APP1), never the gain-map image's. One segment holds at
        most 65,533 bytes; this declaration is {len(xmp):,} bytes with its packet.</li>
      <li>In a PNG, an <code>iTXt</code> chunk named <code>XML:com.adobe.xmp</code>, not compressed, before the image
        data. In an SVG, the <code>&lt;metadata&gt;</code> element.</li>
      <li>Properties are written as attributes; a reader also takes them written as elements, as other XMP tools may
        rewrite them.</li>
      <li>A signature, when there is one, is added after the declaration, so that it covers it.</li>
    </ul>
    <p>The <a href="../conformance/index.html">conformance files</a> break these rules one at a time, with the verdict
    a validator must give.</p>
  </article>
</div></main>
<footer><div class="wrap"><p>UDeep Figure Profile, draft v0.3 · text under CC BY 4.0.</p></div></footer>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
