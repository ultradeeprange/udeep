"""Render the current profile (Markdown, the source of record) as docs/profile/index.html.

    pip install markdown        python tools/build_profile_page.py        then: python tools/site_chrome.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "profile" / "udeep-figure-profile-v0.3.md"
OUT = ROOT / "docs" / "profile" / "index.html"

STYLE = """
  :root { color-scheme: dark; --ground: #111316; --panel: #191d22; --line: #2b3138; --ink: #e4e8ec; --muted: #939da7;
          --tag: #8cc2e6; --warm: #e8b04a;
          --sans: "IBM Plex Sans", "Segoe UI", system-ui, -apple-system, sans-serif;
          --cond: "IBM Plex Sans Condensed", "Arial Narrow", "Segoe UI", system-ui, sans-serif;
          --mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Menlo, monospace; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--ground); color: var(--ink); font: 16px/1.62 var(--sans); }
  .wrap { max-width: 1080px; margin: 0 auto; padding-inline: 20px; }
  a { color: var(--tag); text-underline-offset: 2px; }
  a:focus-visible { outline: 2px solid var(--tag); outline-offset: 2px; }
  header.top { border-bottom: 1px solid var(--line); }
  header.top .wrap { display: flex; gap: 20px; align-items: center; justify-content: space-between; padding-block: 14px; flex-wrap: wrap; }
  .brand { font-family: var(--mono); font-weight: 600; text-decoration: none; color: var(--ink); }
  .brand b { color: var(--warm); font-weight: 600; }
  nav { display: flex; gap: 18px; flex-wrap: wrap; font-size: 14px; }
  nav a { color: var(--muted); text-decoration: none; } nav a:hover, nav a[aria-current] { color: var(--ink); }
  .layout { display: grid; grid-template-columns: minmax(0, 220px) minmax(0, 1fr); gap: 40px; padding-block: 36px 64px; align-items: start; }
  .toc { position: sticky; top: 16px; font-size: 13.5px; display: grid; gap: 6px; max-height: calc(100vh - 32px); overflow-y: auto; }
  .toc a { color: var(--muted); text-decoration: none; } .toc a:hover { color: var(--ink); }
  .toc .label { font-family: var(--mono); font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); }
  article { max-width: 78ch; min-width: 0; }
  article h1, article h2, article h3 { font-family: var(--cond); font-weight: 600; text-wrap: balance; line-height: 1.2; }
  article h1 { font-size: clamp(30px, 4.2vw, 42px); margin: 0 0 14px; }
  article h2 { font-size: 25px; margin: 44px 0 10px; padding-top: 18px; border-top: 1px solid var(--line); }
  article h3 { font-size: 19px; margin: 28px 0 8px; }
  article p, article li { color: var(--soft); }
  article ul, article ol { padding-left: 1.3em; display: grid; gap: 6px; }
  article code { font-family: var(--mono); font-size: 13.5px; background: var(--panel); padding: 1px 4px; border-radius: 3px; }
  article blockquote { margin: 0 0 16px; padding: 10px 14px; border-left: 3px solid var(--warm); background: var(--panel); }
  article strong { color: var(--ink); }
  .scroll { overflow-x: auto; margin: 14px 0; }
  table { border-collapse: collapse; font-size: 14px; min-width: 520px; width: 100%; }
  th, td { text-align: left; padding: 8px 14px 8px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { font: 600 11px var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
  .source { font-size: 14px; color: var(--muted); margin-bottom: 26px; }
  footer { border-top: 1px solid var(--line); padding-block: 28px 48px; font-size: 13px; color: var(--muted); }
  @media (max-width: 860px) { .layout { grid-template-columns: 1fr; gap: 20px; } .toc { position: static; max-height: none; } }
"""

if __name__ == "__main__":
    import markdown                   # only the build needs it: the reference page borrows STYLE without it

    text = SRC.read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=["tables", "toc", "sane_lists"], extension_configs={"toc": {"toc_depth": "2-2"}})
    body = md.convert(text)
    body = body.replace("<table>", '<div class="scroll"><table>').replace("</table>", "</table></div>")
    toc = "".join(f'<a href="#{t["id"]}">{t["name"]}</a>' for t in md.toc_tokens)
    title = re.search(r"^# (.+)$", text, re.MULTILINE).group(1)
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Profile · UDeep</title>
<meta name="description" content="The UDeep Figure Profile, draft v0.3: a data-first specification for turning scientific data into light, with its requirements, metadata, delivery forms and conformance rules.">
<link rel="stylesheet" href="../fonts/plex.css">
<style>{STYLE}</style>
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href="../index.html">U<b>Deep</b></a>
  <nav aria-label="Site"></nav>
</div></header>
<main><div class="wrap layout">
  <nav class="toc" aria-label="Sections of the profile"><span class="label">Contents</span>{toc}</nav>
  <article>
    <p class="source">Rendered from <code>profile/{SRC.name}</code>, the source of record. Earlier drafts are kept beside it.</p>
    {body}
  </article>
</div></main>
<footer><div class="wrap"><p>{title} · text under CC BY 4.0.</p></div></footer>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print("wrote", OUT, f"({len(body) // 1024} KiB of content, {len(md.toc_tokens)} sections)")
