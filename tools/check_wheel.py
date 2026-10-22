"""Check an INSTALLED udeep: that it is the installed one, that it carries the Rust core, and that the core writes
and reads a figure as the NumPy and Pillow implementation does. Run it from anywhere but the repository's root:

    python tools/check_wheel.py            the core must be there (a platform wheel)
    python tools/check_wheel.py --pure     the core must NOT be there (a pure wheel, or a source install without cargo)
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

import numpy as np

import udeep
from udeep import core, gainmap
from udeep.view import hdr_view

pure = "--pure" in sys.argv[1:]


def digests() -> dict:
    """What the core in use makes of one input: the light, the colours and a gain-map JPEG's bytes, digested."""
    from udeep import Mapping
    m = Mapping.from_stretch({"kind": "asinh", "lo": 0.0, "hi": 100.0, "width": 5.0}, 40.0, 4.0, "fire")
    x = np.random.default_rng(5).exponential(20.0, 300_000)
    light = core.light(m, x)
    view = hdr_view(x[:250_000].reshape(500, 500), "extend", 40.0, {"kind": "asinh", "lo": 0.0, "hi": 100.0, "width": 5.0},
                    4.0, "fire")
    with tempfile.TemporaryDirectory() as folder:
        gainmap.write_gainmap_jpeg(view.sdr, view.hdr, Path(folder) / "f.jpg", 4.0)
        jpeg = (Path(folder) / "f.jpg").read_bytes()
    digest = lambda b: hashlib.sha256(b).hexdigest()
    return {"build": core.build(), "light": digest(light.tobytes()), "colours": digest(core.colours(m, light).tobytes()),
            "jpeg": digest(jpeg)}


if "--digests" in sys.argv[1:]:
    print(json.dumps(digests()))
    sys.exit(0)
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
# on x86-64 the wheel carries the core's x86-64-v3 build too: used where the processor has it, and the same bytes
if platform.machine().lower() in ("x86_64", "amd64"):
    wider = path.with_name(path.name.replace("udeep_core", "udeep_core_v3", 1))
    assert wider.is_file(), f"an x86-64 wheel lacks the core's x86-64-v3 build, {wider.name}"
    plain_run = subprocess.run([sys.executable, __file__, "--digests"], capture_output=True, text=True, check=True,
                               env={**os.environ, "UDEEP_CORE_V3": "0"})
    plain, here = json.loads(plain_run.stdout), digests()
    assert plain["build"] == "any processor", plain
    assert here["build"] == ("x86-64-v3" if core._load().udeep_processor_x86_64_v3() == 1 else "any processor"), here
    assert {k: v for k, v in plain.items() if k != "build"} == {k: v for k, v in here.items() if k != "build"}, (
        f"the two builds of the core give different bytes: {plain} against {here}")
    print("udeep | the core's two builds give the same light, colours and JPEG; in use:", here["build"])
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
