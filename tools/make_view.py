"""Place the live view's copies of the project's own code and tables beside its page (docs/view/).

The page is static and can only load what is in docs/, so it carries copies of four things: the reference shader,
the JavaScript port, the WebAssembly binding, and the registered colour-map tables it offers. They are copies, never
edited there: this puts them in place, and tests/test_view.py fails if one differs from its source.

It also builds the Rust core as WebAssembly for the page (udeep_core.wasm), where cargo and the
wasm32-unknown-unknown target are installed. That file is built, not kept in the repository: the pages workflow
(.github/workflows/pages.yml) builds it from the source each time the site is published, and the page works
without it, on the JavaScript port.

    python tools/make_view.py
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE, VIEW = ROOT / "src" / "udeep", ROOT / "docs" / "view"
MAPS = ("fire", "ice")               # the page offers these at every registered headroom, in the current version


def copies() -> dict[Path, Path]:
    """What the page carries, and where each comes from: {the copy: its source}."""
    cm = importlib.import_module("udeep.cmap")           # the package also exports a function called cmap
    pairs = {VIEW / "mapping.wgsl": SOURCE / "shaders" / "mapping.wgsl", VIEW / "udeepcore.js": SOURCE / "js" / "udeepcore.js",
             VIEW / "udeepwasm.js": SOURCE / "js" / "udeepwasm.js"}
    for name in MAPS:
        for headroom in cm.REGISTERED_HEADROOMS:
            file = cm._file(name, headroom)
            pairs[VIEW / "tables" / file] = SOURCE / "tables" / file
    return pairs


WASM = VIEW / "udeep_core.wasm"


def build_wasm() -> Path | None:
    """The core as WebAssembly for the page: without the image codecs, which the page does not use, so it is small.
    Built into core/target/view, apart from the full build the tests make. None where it cannot be built here."""
    command = ["cargo", "build", "--release", "--target", "wasm32-unknown-unknown", "--manifest-path",
               str(ROOT / "core" / "Cargo.toml"), "--target-dir", str(ROOT / "core" / "target" / "view")]
    if shutil.which("cargo") is None or subprocess.run(command, check=False).returncode != 0:
        return None
    return ROOT / "core" / "target" / "view" / "wasm32-unknown-unknown" / "release" / "udeep_core.wasm"


def main() -> None:
    for copy, source in copies().items():
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, copy)
        print(copy.relative_to(ROOT).as_posix(), copy.stat().st_size, "bytes")
    built = build_wasm()
    if built is None:
        print("udeep_core.wasm not built (cargo, or its wasm32-unknown-unknown target, is missing): the page uses the port")
    else:
        shutil.copyfile(built, WASM)
        print(WASM.relative_to(ROOT).as_posix(), WASM.stat().st_size, "bytes, built")


if __name__ == "__main__":
    main()
