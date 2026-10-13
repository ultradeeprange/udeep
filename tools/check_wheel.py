"""Check an INSTALLED udeep: that it is the installed one, that it carries the Rust core, and that the core writes
and reads a figure as the NumPy and Pillow implementation does. Run it from anywhere but the repository's root:

    python tools/check_wheel.py            the core must be there (a platform wheel)
    python tools/check_wheel.py --pure     the core must NOT be there (a pure wheel, or a source install without cargo)
"""
import sys
import sysconfig
import tempfile
from pathlib import Path

import numpy as np

import udeep
from udeep import core, gainmap
from udeep.view import hdr_view

pure = "--pure" in sys.argv[1:]
package = Path(udeep.__file__).resolve().parent
site = {Path(sysconfig.get_path(k)).resolve() for k in ("purelib", "platlib")}
assert any(s in package.parents for s in site), f"udeep was imported from {package}, not from site-packages"
for needed in ("golden.json", "tables/index.json", "tables/fire-3-h4.f32", "shaders/mapping.wgsl", "js/udeepcore.js"):
    assert (package / needed).is_file(), f"the package lacks {needed}"

path = core.library_path()
if pure:
    assert path is None and not core.available() and not core.enabled(), f"a pure install found a core at {path}"
    print("udeep", package, "| no core, as expected")
    sys.exit(0)

assert path is not None and path.parent == package / "_lib", f"the core was looked for and found at {path}"
assert core.available() and core.enabled() and core.files(), "the core is there but is not in use"
assert core.png(), "the core was built without its png feature: it cannot write a 16-bit PQ PNG"

data = np.random.default_rng(3).exponential(20.0, (96, 128))
stretch = {"kind": "linear", "lo": 0.0, "hi": 100.0}
with tempfile.TemporaryDirectory() as folder:
    made = {}
    for name, on in (("core", True), ("reference", False)):
        core.enable(on)
        view = hdr_view(data, "extend", 40.0, stretch, 4.0, "fire")
        file = Path(folder) / f"{name}.jpg"
        gainmap.write_gainmap_jpeg(view.sdr, view.hdr, file, 4.0)
        made[name] = (view, gainmap.read_gainmap_jpeg(file, 4.0))
    core.enable(True)
(view_c, read_c), (view_r, read_r) = made["core"], made["reference"]
assert np.allclose(view_c.hdr, view_r.hdr, rtol=2e-6, atol=2e-6), "the core maps data to light differently"
# what is read back differs from what was written by the JPEG's own loss: measured 2026-10-13, median 0.004 and
# 99th percentile 0.07 of white for both implementations on this figure
errors = {name: np.abs(np.asarray(read) - view.hdr) for name, (view, read) in made.items()}
for name, error in errors.items():
    assert np.median(error) < 0.02 and np.quantile(error, 0.99) < 0.2, f"{name}: the file does not read as written"
assert abs(np.median(errors["core"]) - np.median(errors["reference"])) < 0.005, "the two implementations' files differ"
assert np.asarray(read_c).max() <= 4.0 + 1e-6, "the core wrote a file that reads above its headroom"
with tempfile.TemporaryDirectory() as folder:
    from udeep import read_png_pq, write_png_pq
    write_png_pq(view_c.hdr, Path(folder) / "f.png")
    assert np.abs(read_png_pq(Path(folder) / "f.png") - view_c.hdr).max() < 3e-3, "the 16-bit PNG does not read back"
print("udeep", package, "| core", path.name, "in use | JPEG written and read by both implementations | PNG read back")
