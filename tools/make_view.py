"""Place the live view's copies of the project's own code and tables beside its page (docs/view/).

The page is static and can only load what is in docs/, so it carries copies of three things: the reference shader,
the JavaScript port, and the registered colour-map tables it offers. They are copies, never edited there: this
puts them in place, and tests/test_site.py fails if one differs from its source.

    python tools/make_view.py
"""
from __future__ import annotations

import importlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE, VIEW = ROOT / "src" / "udeep", ROOT / "docs" / "view"
MAPS = ("fire", "ice")               # the page offers these at every registered headroom, in the current version


def copies() -> dict[Path, Path]:
    """What the page carries, and where each comes from: {the copy: its source}."""
    cm = importlib.import_module("udeep.cmap")           # the package also exports a function called cmap
    pairs = {VIEW / "mapping.wgsl": SOURCE / "shaders" / "mapping.wgsl", VIEW / "udeepcore.js": SOURCE / "js" / "udeepcore.js"}
    for name in MAPS:
        for headroom in cm.REGISTERED_HEADROOMS:
            file = cm._file(name, headroom)
            pairs[VIEW / "tables" / file] = SOURCE / "tables" / file
    return pairs


def main() -> None:
    for copy, source in copies().items():
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, copy)
        print(copy.relative_to(ROOT).as_posix(), copy.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
