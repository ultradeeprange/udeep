"""The page that the metadata's namespace URI points to (tools/build_namespace_page.py): it is at the address the
URI names, and it lists every property the reference reads and writes."""
from dataclasses import fields
from pathlib import Path

from udeep.metadata import NAMESPACE, VERSION, Declaration, _property

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://ultradeeprange.github.io/udeep/"


def test_the_namespace_uri_is_served_by_a_page_in_the_site():
    assert NAMESPACE.startswith(SITE)
    page = ROOT / "docs" / NAMESPACE[len(SITE):] / "index.html"
    assert page.is_file(), f"no page at {NAMESPACE}"
    assert f"ns/{VERSION}/" in NAMESPACE


def test_the_page_lists_every_property_of_a_declaration():
    page = (ROOT / "docs" / "ns" / VERSION / "index.html").read_text(encoding="utf-8")
    absent = [_property(f.name) for f in fields(Declaration) if f"<code>udeep:{_property(f.name)}</code>" not in page]
    assert not absent, f"the namespace page does not list {absent}"
