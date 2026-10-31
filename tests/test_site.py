"""The site under docs/: every figure a page can show exists for a light and for a dark page, the two declare the
same thing about the data, the pages that follow the viewer's theme carry the styles that do it, and every page
carries a Content Security Policy that fits it and loads nothing from elsewhere."""
import base64
import dataclasses
import hashlib
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from udeep.metadata import digest_pixels, read_metadata
from udeep.validate import validate

DOCS = Path(__file__).resolve().parents[1] / "docs"
PAGES = sorted(DOCS.rglob("*.html"))
THEMED = [p for p in PAGES if p.name != "404.html" and "check" not in p.relative_to(DOCS).parts]
FIGURE = re.compile(r"_(hdr\.jpg|sdr\.png|full\.png)$")


def dark_name(path: str) -> str:
    """As docs/site.js names the dark file of a figure."""
    return FIGURE.sub(r"_dark_\1", path)


def themed_figures():
    """(page, file) for every figure file a themed image can be given: its own src and its buttons' files."""
    out = []
    for page in THEMED:
        text = page.read_text(encoding="utf-8")
        for img in re.finditer(r"<img data-themed\b[^>]*>", text, re.DOTALL):
            tag = img.group(0)
            ident = re.search(r'\bid="([^"]+)"', tag)
            files = [re.search(r'\bsrc="([^"]+)"', tag).group(1)]
            if ident:
                files += re.findall(rf'data-img="{re.escape(ident.group(1))}" data-src="([^"]+)"', text)
            out += [(page, f) for f in files]
    return out


def test_the_pages_have_themed_figures_to_test():
    figures = themed_figures()
    assert len({f for _p, f in figures}) >= 20 and {p.parent.name for p, _f in figures} == {"docs", "gallery"}


@pytest.mark.parametrize(("page", "file"), themed_figures(), ids=lambda v: v.name if isinstance(v, Path) else v.split("/")[-1])
def test_every_figure_a_page_can_show_exists_for_both_themes_at_one_size(page, file):
    assert FIGURE.search(file), f"{file}: site.js could not name its dark file"
    light, dark = (page.parent / file).resolve(), (page.parent / dark_name(file)).resolve()
    assert light.is_file() and dark.is_file(), dark
    assert Image.open(light).size == Image.open(dark).size             # the page's layout does not move with the theme


def test_each_themed_image_names_its_dark_file_before_any_script_runs():
    for page in THEMED:
        text = page.read_text(encoding="utf-8")
        for m in re.finditer(r'<picture><source data-dark srcset="([^"]+)" media="\(prefers-color-scheme: dark\)">'
                             r'<img data-themed\b[^>]*?\bsrc="([^"]+)"', text, re.DOTALL):
            assert m.group(1) == dark_name(m.group(2))
        assert text.count("<img data-themed") == text.count("<source data-dark") == text.count('class="plate themed"')


HDR = sorted(p for p in (DOCS / "assets").glob("*_hdr.jpg") if "_dark_" not in p.name)


@pytest.mark.parametrize("light", HDR, ids=lambda p: p.name)
def test_the_dark_figure_declares_what_the_light_one_does_and_is_its_own_sdr_figure(light):
    dark = light.with_name(dark_name(light.name))
    a, b = read_metadata(light), read_metadata(dark)
    assert a is not None and b is not None
    unlinked = {"sdr_digest": None, "sdr_digest_sha3": None}            # each theme's own SDR figure, by either hash
    assert dataclasses.replace(a, **unlinked) == dataclasses.replace(b, **unlinked)
    if a.sdr_digest is not None:                                        # the demo figures state none
        assert a.sdr_digest != b.sdr_digest
    if a.sdr_digest_sha3 is not None:                                   # files made before 23 October 2026 state none
        assert a.sdr_digest_sha3 != b.sdr_digest_sha3
        sdr = dark.with_name(dark.name.replace("_hdr.jpg", "_sdr.png"))
        assert b.sdr_digest == digest_pixels(np.asarray(Image.open(sdr).convert("RGB")))
        assert validate(dark, sdr=sdr).ok
    corner = np.asarray(Image.open(dark).convert("RGB"))[:4, :4]
    assert corner.max() < 40 and np.asarray(Image.open(light).convert("RGB"))[:4, :4].min() > 240


def test_pages_follow_the_theme_except_the_device_checks():
    for page in PAGES:
        text = page.read_text(encoding="utf-8")
        rel = page.relative_to(DOCS).as_posix()
        if page in THEMED:
            assert text.count('<style id="theme">') == 1, rel
            block = text[text.index('<style id="theme">'):]
            block = block[:block.index("</style>")]
            # the system setting, and an explicit choice that wins over it in both directions
            assert "@media (prefers-color-scheme: light)" in block and ':root:not([data-theme="dark"])' in block, rel
            assert ':root[data-theme="light"]' in block and ':root[data-theme="dark"]' in block, rel
            assert text.index('<style id="theme">') > text.index("</style>"), rel      # after the page's own styles
            style = text[:text.index('<style id="theme">')]
            own = style[style.index("}", style.index(":root")):]                       # past the page's own palette
            assert re.findall(r"#[0-9a-fA-F]{6}\b", own.replace("#ffffff", "")) == [], rel   # only tokens, and white mats
        else:
            assert '<style id="theme">' not in text, rel                # a fixed look: test patterns, the 404 page


def test_the_light_palette_defines_every_token_a_page_uses():
    for page in THEMED:
        text = page.read_text(encoding="utf-8")
        block = text[text.index('<style id="theme">'):]
        block = block[:block.index("</style>")]
        light = block[block.index(':root[data-theme="light"]'):]
        light = set(re.findall(r"(--[a-z-]+):", light[:light.index("}")]))
        colours = set(re.findall(r"(--[a-z-]+): *#", text[:text.index('<style id="theme">')])) | \
            set(re.findall(r"(--[a-z-]+): *#", block[:block.index("@media")]))
        assert colours <= light, (page.relative_to(DOCS).as_posix(), sorted(colours - light))


def test_site_js_names_dark_files_as_the_tools_write_them():
    script = (DOCS / "site.js").read_text(encoding="utf-8")
    assert r"/_(hdr\.jpg|sdr\.png|full\.png)$/" in script and '"_dark_$1"' in script


CSP = re.compile(r'<meta charset="utf-8">\n?<meta http-equiv="Content-Security-Policy" content="([^"]+)">')


def sha256(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


def test_every_page_has_a_content_security_policy_that_fits_it():
    """Straight after <meta charset>: the site's own files only, no inline script, and of inline styles only the page's
    own <style> blocks, by hash (tools/site_chrome.py writes it; run it after editing a page)."""
    for page in PAGES:
        text = page.read_text(encoding="utf-8")                  # newlines as \n, as the browser hashes a <style>
        rel = page.relative_to(DOCS).as_posix()
        m = CSP.search(text)
        assert m and text.index(m.group(0)) < text.index("<title>"), rel
        policy = dict(d.strip().split(" ", 1) for d in m.group(1).split(";"))
        assert policy["default-src"] == "'self'" and policy["object-src"] == policy["base-uri"] == "'none'", rel
        assert policy["script-src"] == ("'self' 'wasm-unsafe-eval'" if rel == "view/index.html" else "'self'"), rel
        assert policy.get("img-src", "'self'") == ("'self' data:" if rel == "check/svg/index.html" else "'self'"), rel
        styles = {sha256(css) for css in re.findall(r"<style\b[^>]*>(.*?)</style>", text, re.DOTALL)}
        assert set(policy["style-src"].split()) == {"'self'", *styles}, rel
        assert "'unsafe-inline'" not in m.group(1) and "'unsafe-eval'" not in m.group(1) and "http" not in m.group(1), rel


def test_no_page_runs_inline_code_or_loads_another_sites_files():
    for page in PAGES:
        text = page.read_text(encoding="utf-8")
        rel = page.relative_to(DOCS).as_posix()
        assert not re.search(r"<script(?![^>]*\ssrc=)", text), rel             # every script is a file
        assert not re.search(r'\sstyle="|\son[a-z]+="', text), rel             # no style= and no onclick= and friends
        for tag in re.findall(r"<(?:script|img|source|video|audio|iframe|embed|object)\b[^>]*>", text):
            assert not re.search(r'\s(?:src|srcset|data)="(?:https?:)?//', tag), (rel, tag)
        for tag in re.findall(r"<link\b[^>]*>", text):
            assert 'rel="canonical"' in tag or not re.search(r'\shref="(?:https?:)?//', tag), (rel, tag)
    for css in DOCS.rglob("*.css"):
        assert not re.search(r"url\(\s*['\"]?(?:https?:)?//|@import", css.read_text(encoding="utf-8")), css
    assert 'createElement("style")' not in (DOCS / "site.js").read_text(encoding="utf-8")   # its styles: site.css
