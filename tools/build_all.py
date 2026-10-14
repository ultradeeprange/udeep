"""Rebuild every generated file of the repository, in the order they depend on one another.

    python tools/build_all.py            everything that needs nothing but the repository
    python tools/build_all.py --check    the same, then fail if any generated file has changed (what CI runs)
    python tools/build_all.py --figures  also the figures, which need the downloaded data and take minutes

Each step is a tool of its own and can be run alone; this is only their order, written down once:

    colour tables         tools/make_tables.py         never rewrites a table that exists
    golden vectors        python -m udeep.golden       from the NumPy reference (not in --check: see below)
    live view's copies    tools/make_view.py           the shader, the JavaScript port and the tables, copied
    profile page          tools/build_profile_page.py  from profile/*.md (needs `pip install markdown`)
    namespace page        tools/build_namespace_page.py  docs/ns/<version>/, from the profile's 8.1 (markdown too)
    reference page        tools/build_reference_page.py
    evidence table        tools/evidence.py
    site chrome           tools/site_chrome.py         last: it rewrites the head and navigation of every page

--check leaves out the golden vectors. They are made from the reference's arithmetic, whose last bits depend on the
processor's routines (project/performance-scope.md, 4g), so another machine may write them a little differently;
tests/test_udeep_golden.py holds them to their stated tolerances instead.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

STEPS = [
    ("colour tables", [PY, "tools/make_tables.py"], True),
    ("golden vectors", [PY, "-m", "udeep.golden"], False),
    ("live view's copies", [PY, "tools/make_view.py"], True),
    ("profile page", [PY, "tools/build_profile_page.py"], True),
    ("namespace page", [PY, "tools/build_namespace_page.py"], True),
    ("reference page", [PY, "tools/build_reference_page.py"], True),
    ("evidence table", [PY, "tools/evidence.py"], True),
]
FIGURES = [
    ("demonstration figures", [PY, "tools/make_demos.py"]),
    ("gallery", [PY, "tools/make_showcase.py"]),
    ("opening pictures", [PY, "tools/make_intro.py"]),
]
CHROME = ("site chrome", [PY, "tools/site_chrome.py"])


def run(name: str, command: list[str]) -> None:
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
    if done.returncode != 0:
        sys.exit(f"{name}: failed\n{done.stdout[-2000:]}{done.stderr[-2000:]}")
    print(f"{name}: done")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if a generated file is not what the tools make")
    parser.add_argument("--figures", action="store_true", help="also remake the figures (needs the data)")
    args = parser.parse_args()
    steps = [(name, command) for name, command, checked in STEPS if checked or not args.check]
    if args.figures:
        steps += FIGURES
    for name, command in [*steps, CHROME]:
        run(name, command)
    if args.check:
        # docs/assets is left out: its pictures, the share image among them, are drawn with the machine's fonts.
        # Content is compared (git diff), not the files' state: where git turns line endings on checkout, a file
        # rewritten with the same text would otherwise be reported as changed.
        paths = ["--", "docs", ":!docs/assets", "src/udeep/tables", "evidence"]
        git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        changed = (git("diff", "--name-only", "HEAD", *paths)
                   + git("ls-files", "--others", "--exclude-standard", *paths)).strip()
        if changed:
            sys.exit("these generated files are not what the tools make; run tools/build_all.py and commit them:\n"
                     + changed)
        print("every generated file is current")


if __name__ == "__main__":
    main()
