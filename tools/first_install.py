"""Install udeep as a newcomer would, on this machine, and check that it runs the first time.

Needs only Python 3.11 or later: no checkout of the repository, nothing installed beforehand. It makes a fresh
virtual environment in a temporary folder, installs "udeep[mpl]" into it with the pip that comes with Python (not
upgraded, as a newcomer's is not), then runs `python -m udeep.selftest` there, from an empty folder, so that only
the installed package can be imported. Nothing outside the temporary folder is changed.

    python first_install.py                         from PyPI, once it is released there
    python first_install.py --find-links DIR        from packages built by the wheels workflow, in DIR; the
                                                    dependencies still come from PyPI, so the result is what a
                                                    PyPI release of those packages would give
    python first_install.py --git                   from the repository's main branch (builds from source)

    --expect-core      fail unless the Rust core is in use (on a platform for which a wheel is built)
    --json FILE        write what was installed, and the self-test's result, to FILE
    --keep             keep the temporary folder, and print where it is
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path

REPOSITORY = "https://github.com/ultradeeprange/udeep"


def bin_python(env: Path) -> Path:
    return env / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--find-links", type=Path, help="install udeep from the packages in this folder")
    source.add_argument("--git", action="store_true", help="install from the repository's main branch")
    parser.add_argument("--expect-core", action="store_true")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    if sys.version_info < (3, 11):                              # noqa: UP036  (it may be run with any Python)
        print(f"udeep needs Python 3.11 or later; this is {platform.python_version()}")
        return 1
    where = Path(tempfile.mkdtemp(prefix="udeep-first-install-"))
    record = {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(),
              "source": "git" if args.git else f"find-links {args.find_links}" if args.find_links else "PyPI"}
    try:
        env = where / "env"
        venv.create(env, with_pip=True)                            # the pip that comes with this Python
        python = bin_python(env)
        pip_version = subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True, text=True,
                                     check=True).stdout.split()[1]
        record["pip"] = pip_version
        requirement = f"udeep[mpl] @ git+{REPOSITORY}" if args.git else "udeep[mpl]"
        command = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--report",
                   str(where / "report.json"), requirement]
        if args.find_links:
            command[4:4] = ["--find-links", str(args.find_links.resolve())]
        print(f"installing {requirement!r} with pip {pip_version} on Python {record['python']}, {record['platform']}", flush=True)
        started = time.perf_counter()
        installed = subprocess.run(command, capture_output=True, text=True, check=False)
        record["install_seconds"] = round(time.perf_counter() - started, 1)
        if installed.returncode != 0:
            print(installed.stdout[-4000:], installed.stderr[-4000:], sep="\n")
            print(f"the install FAILED after {record['install_seconds']} s")
            record["ok"] = False
            return 1
        report = json.loads((where / "report.json").read_text(encoding="utf-8"))
        record["installed"] = []
        for item in report.get("install", []):
            meta, info = item.get("metadata", {}), item.get("download_info", {})
            url = info.get("url", "")
            record["installed"].append({"name": meta.get("name"), "version": meta.get("version"),
                                        "file": url.rsplit("/", 1)[-1] if "/" in url else url,
                                        "built here": "archive_info" in info and not url.endswith(".whl")})
        for item in record["installed"]:
            if (item["name"] or "").lower() == "udeep":
                how = "built here from source" if item["built here"] or args.git else "a ready-made wheel"
                print(f"installed udeep {item['version']} ({item['file']}: {how}) and "
                      f"{len(record['installed']) - 1} other packages in {record['install_seconds']} s")
        empty = where / "empty"                                    # so that only the installed package can be found
        empty.mkdir()
        test = [str(python), "-m", "udeep.selftest", "--json", str(where / "selftest.json")]
        if args.expect_core:
            test.append("--expect-core")
        ran = subprocess.run(test, cwd=empty, check=False, env={**os.environ, "PYTHONPATH": ""})
        record["selftest"] = json.loads((where / "selftest.json").read_text(encoding="utf-8")) \
            if (where / "selftest.json").is_file() else None
        record["ok"] = ran.returncode == 0
        return ran.returncode
    finally:
        if args.json:
            args.json.write_text(json.dumps(record, indent=1), encoding="utf-8")
        if args.keep:
            print(f"kept: {where}")
        else:
            shutil.rmtree(where, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
