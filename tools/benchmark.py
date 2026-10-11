"""Where the time goes: each stage of the pipeline on a one-megapixel image, and a whole figure.

    python tools/benchmark.py            # prints a table; compare with project/performance-scope.md

Best of a few runs each, on whatever else the machine is doing: read the numbers as good to perhaps +-30 %.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from udeep import Mapping
from udeep import mpl as hp
from udeep.gainmap import write_gainmap_jpeg
from udeep.metadata import data_steps, digest_data
from udeep.view import hdr_view

N = 1000
STRETCH = {"kind": "linear", "lo": 0.0, "hi": 100.0}


def best(f, repeat: int = 3) -> float:
    times = []
    for _ in range(repeat):
        t = time.perf_counter(); f(); times.append(time.perf_counter() - t)
    return min(times) * 1e3


def main() -> None:
    warnings.simplefilter("ignore")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    d = np.random.default_rng(0).exponential(10.0, (N, N))
    grey, fire = (Mapping.from_stretch(STRETCH, 50.0, 4.0, name) for name in ("grey", "fire"))
    light = fire.light(d)
    out = os.path.join(tempfile.mkdtemp(), "bench.jpg")
    view = hdr_view(d, "extend", 50.0, STRETCH, 4.0, "grey")
    rows = [
        ("data -> position", lambda: fire.position(d)),
        ("data -> light (the mapping)", lambda: fire.light(d)),
        ("light -> colour, fire table", lambda: fire.colours(light)),
        ("light -> data (the inverse)", lambda: fire.value(light)),
        ("whole view, grey (masks, three colour passes)", lambda: hdr_view(d, "extend", 50.0, STRETCH, 4.0, "grey")),
        ("whole view, fire", lambda: hdr_view(d, "extend", 50.0, STRETCH, 4.0, "fire")),
        ("stored levels, grey", lambda: data_steps(grey)),
        ("stored levels, fire", lambda: data_steps(fire)),
        ("digest of the data", lambda: digest_data(d)),
        ("gain-map JPEG (two encodes, one decode)", lambda: write_gainmap_jpeg(view.sdr, view.hdr, out, 4.0)),
    ]
    print(f"{N} x {N} image, best of 3\n")
    for label, f in rows:
        print(f"  {label:<48}{best(f):9.1f} ms")
    print("\nwhole figure, 8 x 8 inch at 150 dpi, best of 2\n")
    for name in ("grey", "fire"):
        fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
        t0 = time.perf_counter()
        im = hp.imshow(ax, d, mode="extend", knee=50.0, stretch=STRETCH, headroom=4.0, cmap=name, unit="counts",
                       knee_rule="preset; x")
        hp.colorbar(im, ax=ax)
        build = (time.perf_counter() - t0) * 1e3
        save = best(lambda fig=fig: hp.savefig_hdr(fig, out, headroom=4.0), 2)
        compose = best(lambda fig=fig: hp._compose(fig, 4.0, True, 0.1), 2)
        cropped = best(lambda fig=fig: hp._render(fig, True, 0.1), 2)
        plain = best(lambda fig=fig: fig.savefig(out[:-4] + ".png", bbox_inches="tight"), 2)
        print(f"  {name:5} build image and colour bar {build:7.0f} ms | savefig_hdr {save:6.0f} ms "
              f"(compose {compose:.0f}, of which one cropped render {cropped:.0f}) | plain savefig {plain:5.0f} ms")
        plt.close(fig)


if __name__ == "__main__":
    main()
