"""The Python reference page (docs/reference/): it is what tools/build_reference_page.py makes from the source now."""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("build_reference_page", ROOT / "tools" / "build_reference_page.py")
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)
sys.path.pop(0)


def article(page: str) -> str:
    return re.search(r"<article>.*</article>", page, re.DOTALL).group(0)


def test_the_page_is_current():
    """The site's chrome changes the head and the navigation, not the article."""
    made = article(reference.page())
    assert article(reference.OUT.read_text(encoding="utf-8")) == made, "run tools/build_reference_page.py, then site_chrome.py"


def test_every_module_is_listed_and_every_link_in_the_page_has_its_target():
    modules = sorted(p.stem for p in reference.PACKAGE.glob("*.py") if p.stem != "__init__")
    assert sorted(reference.MODULES) == modules
    page = reference.page()
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    assert set(re.findall(r'href="#([^"]+)"', page)) <= ids
    assert {"mpl.savefig_hdr", "view.hdr_view", "mapping.Mapping", "metadata.Declaration", "validate.validate"} <= ids


def test_a_docstring_is_shown_as_it_is_written():
    text = reference.prose("One line.\n\n    code(here) < 2\n\nThen `this` & that.")
    want = '<p>One line.</p>\n<pre class="block">code(here) &lt; 2</pre>\n<p>Then <code>this</code> &amp; that.</p>'
    assert text == want
    assert "No description" in reference.prose(None)
