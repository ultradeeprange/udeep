"""The conformance files (docs/conformance): each case gives the verdict expected.json says it must, and the anatomy
page is what tools/build_anatomy_page.py makes of the files today."""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from udeep.validate import validate

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "docs" / "conformance"
EXPECTED = json.loads((FILES / "expected.json").read_text(encoding="utf-8"))


def tool(name):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize("name", sorted(EXPECTED["cases"]))
def test_each_case_gives_the_verdict_the_rules_require(name):
    case = EXPECTED["cases"][name]
    data = None if case["data"] is None else np.load(FILES / case["data"]["npy"])
    assert data is None or np.array_equal(data, np.loadtxt(FILES / case["data"]["csv"], delimiter=","))
    r = validate(FILES / case["file"], data=data, sdr=None if case["sdr"] is None else FILES / case["sdr"])
    assert {check: r.status(check) for check in case["checks"]} == case["checks"], str(r)
    assert r.ok == case["conforms"], str(r)


def test_the_cases_are_the_ones_the_tool_defines_and_every_file_is_there():
    assert EXPECTED == tool("make_conformance").expected()
    named = {c["file"] for c in EXPECTED["cases"].values()} | {c["sdr"] for c in EXPECTED["cases"].values() if c["sdr"]}
    named |= {f for c in EXPECTED["cases"].values() if c["data"] for f in c["data"].values()}
    assert named | {"expected.json", "index.html"} == {p.name for p in FILES.iterdir()}


def test_the_anatomy_page_is_current(tmp_path, monkeypatch):
    anatomy = tool("build_anatomy_page")
    monkeypatch.setattr(anatomy, "OUT", tmp_path / "index.html")
    anatomy.main()
    article = lambda text: text[text.index("<article>"):text.index("</article>")]
    committed = (ROOT / "docs" / "anatomy" / "index.html").read_text(encoding="utf-8")
    assert article(committed) == article((tmp_path / "index.html").read_text(encoding="utf-8")), \
        "run tools/build_anatomy_page.py, then site_chrome.py"
