"""Write the page that the metadata's namespace URI points to (docs/ns/<version>/index.html), from the profile.

    pip install markdown        python tools/build_namespace_page.py        then: python tools/site_chrome.py

A reader never needs to fetch a namespace URI; the page is for people who look one up. Its table of properties is
copied from section 8.1 of the profile, which is the definition, so the two cannot drift apart. Its example is the
declaration carried by the worked example's data record (examples/output/detector_record_pq16.png), as the file
holds it.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from build_profile_page import STYLE

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from udeep.metadata import NAMESPACE, VERSION, packet  # noqa: E402

SRC = ROOT / "profile" / f"udeep-figure-profile-v{VERSION}.md"
EXAMPLE = ROOT / "examples" / "output" / "detector_record_pq16.png"
BASE = "https://ultradeeprange.github.io/udeep/"
OUT = ROOT / "docs" / "ns" / VERSION / "index.html"


def section_8_1(text: str) -> tuple[str, str]:
    """The table of 8.1 and the paragraph after it that says which properties are required, as Markdown."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line.strip() == "| Property | Type | Meaning |")
    end = next(i for i in range(start, len(lines)) if not lines[i].strip())
    after = next(i for i in range(end, len(lines)) if lines[i].strip())
    stop = next(i for i in range(after, len(lines)) if not lines[i].strip())
    dedent = lambda block: "\n".join(line[2:] if line.startswith("  ") else line for line in block)
    return dedent(lines[start:end]), dedent(lines[after:stop])


def example() -> str:
    """The worked example's declaration, one property to a line."""
    element = re.search(rb"<rdf:Description .*?/>", packet(EXAMPLE.read_bytes()), re.S).group(0).decode("utf-8")
    parts = re.findall(r'\S+?="[^"]*"|\S+?=\'[^\']*\'', element)
    return "<rdf:Description\n" + "\n".join("    " + p for p in parts) + "/>"


def main() -> None:
    import markdown

    if NAMESPACE != f"{BASE}ns/{VERSION}/":
        raise SystemExit(f"the namespace is {NAMESPACE}, which this page would not be served at")
    table, required = section_8_1(SRC.read_text(encoding="utf-8"))
    md = markdown.Markdown(extensions=["tables"])
    body = md.convert(table + "\n\n" + required)
    body = body.replace("<table>", '<div class="scroll"><table>').replace("</table>", "</table></div>")
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>udeep namespace {VERSION} · UDeep</title>
<meta name="description" content="The udeep XMP namespace, version {VERSION}: the properties in which a figure file declares how its data became light, as the UDeep Figure Profile defines them.">
<link rel="stylesheet" href="../../fonts/plex.css">
<style>{STYLE}
  .single {{ padding-block: 36px 64px; }}
  .uri {{ font-family: var(--mono); font-size: 15px; overflow-wrap: anywhere; color: var(--ink); }}
  pre {{ background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: 14px 16px; overflow-x: auto;
         font: 13px/1.55 var(--mono); color: var(--ink); }}
</style>
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href="../../index.html">U<b>Deep</b></a>
  <nav aria-label="Site"></nav>
</div></header>
<main><div class="wrap single">
  <article>
    <h1>The <code>udeep</code> namespace, version {VERSION}</h1>
    <p class="uri">{html.escape(NAMESPACE)}</p>
    <p>This address is the name of the XMP namespace in which a figure file declares how its data became light: the
    metadata of the <a href="../../profile/index.html">UDeep Figure Profile</a>, draft v{VERSION}, section 8. The
    usual prefix is <code>udeep</code>. A reader never needs to fetch the address; this page is for people who look
    it up. The address is provisional until version 1.0 of the profile.</p>
    <h2>Properties</h2>
    <p>Copied from section 8.1 of the profile, which defines them. Sections
    <a href="../../profile/index.html#8-declared-metadata">8.3 to 8.6</a> say which numbers have the offset removed,
    how numbers are written, which declarations a reader refuses, and how the two digests are taken.</p>
    {body}
    <h2>An example</h2>
    <p>The declaration in the repository's worked example of a data record,
    <a href="https://github.com/ultradeeprange/udeep/blob/main/examples/output/{EXAMPLE.name}"><code>{EXAMPLE.name}</code></a>:
    a synthetic 12-bit detector frame stored as a 16-bit PQ PNG. It is shown one property to a line; in the file it
    is one element.</p>
    <pre><code>{html.escape(example())}</code></pre>
  </article>
</div></main>
<footer><div class="wrap"><p>UDeep Figure Profile, draft v{VERSION} · text under CC BY 4.0.</p></div></footer>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
