"""Keep the site's shared furniture consistent on every page under docs/: favicon, link-preview tags, the HDR
limit script (site.js), and the navigation. Idempotent: run it after adding or editing a page.

    python tools/site_chrome.py
"""
from __future__ import annotations

import html
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DOCS = Path(__file__).resolve().parents[1] / "docs"
BASE = "https://ultradeeprange.github.io/udeep/"                 # where the site will be served; one place to change
REPO = "https://github.com/ultradeeprange/udeep"
NAV = [("index.html", "Home"), ("gallery/index.html", "Real data"), ("profile/index.html", "Profile"),
       ("anatomy/index.html", "Inside a file"),
       ("apps/index.html", "In your apps"), ("view/index.html", "Live view"), ("standard/index.html", "Standardisation"),
       ("trust/index.html", "Trust"),
       ("evidence/index.html", "Evidence"), ("questions/index.html", "Questions"),
       ("check/index.html", "Device checks"), ("roadmap/index.html", "Roadmap"), ("about/index.html", "About")]
CHECK_DESCRIPTIONS = {
    "check/index.html": "Test pages that show whether a screen and browser display HDR, each with controls that must pass.",
    "check/pattern/index.html": "One HDR test pattern in gain-map JPEG, HDR PNG, AVIF and HDR video, with an SDR control.",
    "check/svg/index.html": "Does HDR survive inside SVG? Inline, linked and standalone SVG, and vector colours above white.",
    "check/video/index.html": "Does 12-bit HDR video play? HEVC and AV1 at 10 and 12 bits.",
    "check/precision/index.html": "A blind banding test: HDR grey ramps rounded to 8 to 16 bits, in shuffled order.",
}
START, END = "<!-- chrome:head -->", "<!-- /chrome:head -->"
# The pages are written dark first (their own :root holds the dark palette). This block, placed after a page's own
# styles, adds the few tokens the pages share and the light palette: for a viewer whose system asks for light, and
# for an explicit choice (data-theme on the root element, set by the control in site.js or by a host page), which
# wins over the system either way. The device-check pages are test patterns on a fixed dark ground and are left out.
THEME_CSS = """<style id="theme">
  :root { --soft: #cfd5db; --warm-line: #5a4a26; --yes: #9fd49a; --no: #e39a8a; --str: #b5d49a; --yes-line: #3f5a3c;
          --plate: #111316; }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) { color-scheme: light; --ground: #f3f4f6; --panel: #ffffff; --raise: #e6e9ed;
      --line: #d2d7dd; --ink: #15191d; --muted: #56616c; --faint: #7b8691; --tag: #1c6aa0; --warm: #8f6200;
      --soft: #2c343b; --warm-line: #d8bf85; --yes: #2c7a27; --no: #b0452f; --str: #3a7a1c; --yes-line: #a9cfa4;
      --good: #2c7a27; --bad: #b0452f;
      --plate: #ffffff; }
  }
  :root[data-theme="light"] { color-scheme: light; --ground: #f3f4f6; --panel: #ffffff; --raise: #e6e9ed;
    --line: #d2d7dd; --ink: #15191d; --muted: #56616c; --faint: #7b8691; --tag: #1c6aa0; --warm: #8f6200;
    --soft: #2c343b; --warm-line: #d8bf85; --yes: #2c7a27; --no: #b0452f; --str: #3a7a1c; --yes-line: #a9cfa4;
      --good: #2c7a27; --bad: #b0452f;
    --plate: #ffffff; }
  :root[data-theme="dark"] { color-scheme: dark; }
  /* tables, the same on every page: a panel, readable headings, and a mark before each verdict */
  .scroll:has(> table) { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 4px 16px; }
  .scroll > table th { color: var(--ink); }
  .scroll > table td:first-child { font-weight: 600; color: var(--ink); }
  td.yes, td.y, td.done { color: var(--yes); } td.no, td.n { color: var(--no); } td.part, td.p, td.next { color: var(--warm); }
  td.later { color: var(--muted); }
  td.yes::before, td.y::before, td.done::before { content: "\\2713\\00a0"; font-weight: 700; }
  td.no::before, td.n::before { content: "\\2717\\00a0"; font-weight: 700; }
  td.part::before, td.p::before { content: "\\25D0\\00a0"; }
  td.next::before { content: "\\2192\\00a0"; }
  td.later::before { content: "\\25CB\\00a0"; }
  tr.ours td { background: var(--raise, var(--panel)); border-top: 2px solid var(--warm); border-bottom: 2px solid var(--warm); }
  tr.ours td:first-child { color: var(--warm); }
  picture { display: block; }
  .plate.themed { background: var(--plate); }
</style>
"""
UNTHEMED = ("check/",)                                      # single-look pages: see THEME_CSS


def og_image() -> None:
    """A 1200 x 630 link-preview image: the project name and one line, beside a real figure."""
    w, h = 1200, 630
    im = Image.new("RGB", (w, h), "#111316")
    d = ImageDraw.Draw(im)
    try:
        big, mid, small = (ImageFont.truetype("arialbd.ttf", 76), ImageFont.truetype("arial.ttf", 34),
                           ImageFont.truetype("arial.ttf", 24))
    except OSError:
        big = mid = small = ImageFont.load_default()
    fig = Image.open(DOCS / "assets" / "gw250114_sdr.png").convert("RGB")
    fig.thumbnail((560, 520))
    im.paste(fig, (w - fig.width - 48, (h - fig.height) // 2))
    d.text((56, 150), "U", fill="#e4e8ec", font=big)
    d.text((56 + d.textlength("U", font=big), 150), "Deep", fill="#e8b04a", font=big)
    for i, line in enumerate(["Scientific figures that record", "how data became light,", "and use the range of", "modern screens."]):
        d.text((56, 270 + i * 46), line, fill="#cdd3d9", font=mid)
    d.text((56, 540), "A data-first draft profile and reference tools", fill="#939da7", font=small)
    im.save(DOCS / "assets" / "og.png", optimize=True)


def head_block(page: Path, title: str, desc: str) -> str:
    depth = len(page.relative_to(DOCS).parts) - 1
    up = "../" * depth
    url = BASE + ("" if page.relative_to(DOCS).as_posix() == "index.html" else page.relative_to(DOCS).parent.as_posix() + "/")
    e = lambda s: html.escape(s, quote=True)
    return (f'{START}\n<link rel="icon" href="{up}favicon.svg" type="image/svg+xml">\n'
            f'<link rel="canonical" href="{url}">\n'
            f'<meta property="og:type" content="website">\n<meta property="og:site_name" content="UDeep">\n'
            f'<meta property="og:title" content="{e(title)}">\n<meta property="og:description" content="{e(desc)}">\n'
            f'<meta property="og:url" content="{url}">\n<meta property="og:image" content="{BASE}assets/og.png">\n'
            f'<meta name="twitter:card" content="summary_large_image">\n'            + ("" if page.relative_to(DOCS).as_posix().startswith(UNTHEMED) else THEME_CSS)
            + f'<script src="{up}site.js" defer></script>\n{END}')


def nav_html(page: Path) -> str:
    here = page.relative_to(DOCS).as_posix()
    up = "../" * (len(page.relative_to(DOCS).parts) - 1)
    links = "".join(f'<a href="{up}{href}"' + (' aria-current="page"' if href == here else "") + f">{label}</a>"
                    for href, label in NAV)
    return f'<nav aria-label="Site">{links}<a href="{REPO}">GitHub</a></nav>'


def main() -> None:
    og_image()
    for page in sorted(DOCS.rglob("*.html")):
        rel = page.relative_to(DOCS).as_posix()
        if rel == "404.html":                               # served at any depth, so it stands alone with absolute links
            continue
        s = page.read_text(encoding="utf-8")
        title = html.unescape(re.search(r"<title>(.*?)</title>", s, re.DOTALL).group(1)).strip()
        m = re.search(r'<meta name="description" content="([^"]*)">', s)
        if m is None and rel in CHECK_DESCRIPTIONS:
            desc = CHECK_DESCRIPTIONS[rel]
            s = s.replace("</title>", f'</title><meta name="description" content="{html.escape(desc, quote=True)}">', 1)
        elif m is None:
            raise SystemExit(f"{rel}: no meta description")
        else:
            desc = html.unescape(m.group(1))
        s = re.sub(re.escape(START) + r".*?" + re.escape(END) + r"\n?", "", s, flags=re.DOTALL)
        if s.count("</head>") != 1:
            raise SystemExit(f"{rel}: expected exactly one </head>")
        s = s.replace("</head>", head_block(page, title, desc) + "\n</head>", 1)
        if '<header class="top">' in s:
            s, n = re.subn(r"<nav aria-label=\"[^\"]*\">.*?</nav>", nav_html(page), s, count=1, flags=re.DOTALL)
            if n != 1:
                raise SystemExit(f"{rel}: navigation not found")
        page.write_text(s, encoding="utf-8")
        print("ok", rel)


if __name__ == "__main__":
    main()
