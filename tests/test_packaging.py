"""What a newcomer meets first: the self-test that says whether udeep works on their machine, and the README that
PyPI shows as the project's page. The install itself, on fresh machines, is the wheels workflow's first-install
jobs (tools/first_install.py)."""
import json
import re
import tomllib
from pathlib import Path

from udeep import core, selftest

ROOT = Path(__file__).resolve().parents[1]


def test_the_selftest_passes_here_and_says_what_it_used(tmp_path):
    assert selftest.main(["--json", str(tmp_path / "result.json"), "--keep", str(tmp_path / "files")]) == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["ok"] and [s["name"] for s in result["steps"]] == ["figure", "data record"]
    assert all(s["status"] == "pass" for s in result["steps"])
    assert {"figure.png", "figure.jpg", "record.png"} <= {p.name for p in (tmp_path / "files").iterdir()}


def test_the_selftest_fails_when_the_core_is_expected_and_not_in_use():
    """Its control: the same run, with the core switched off and asked for, must fail."""
    was = core.enabled()
    core.enable(False)
    try:
        result = selftest.run(expect_core=True)
    finally:
        core.enable(was)
    assert not result["ok"] and result["steps"][0] == {"name": "core", "status": "fail",
                                                       "detail": "the Rust core was expected but is not in use"}


def test_the_readme_links_work_on_pypi():
    """PyPI shows the README as the project's page, away from the repository, so a relative link or image there
    leads nowhere: every one must be a full address."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    relative = re.findall(r"\]\((?!https?:|#|mailto:)([^)\s]+)\)", readme) + \
        re.findall(r'\b(?:src|srcset)="(?!https?:)([^"]+)"', readme)
    assert relative == []


def test_the_package_says_where_its_project_is():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert {"Homepage", "Documentation", "Source", "Issues", "Questions", "Changelog"} <= set(project["urls"])
    assert project["requires-python"] == ">=3.11" and "Programming Language :: Python :: 3.11" in project["classifiers"]
