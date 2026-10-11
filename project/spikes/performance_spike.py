"""Performance spike (2026-10-08): where the time goes, and what two candidate changes would buy.

Nothing here changes the library. Run:  python project/spikes/performance_spike.py > project/spikes/performance_spike.log

  A. a colour map as a table: the map is a function of light alone, so compute it once at N nodes and interpolate.
     Measured: the error against the exact per-pixel search (in 8-bit sRGB codes) and the speed.
  B. where savefig_hdr spends its time for a figure whose colour map is already cheap (grey).
"""
from __future__ import annotations

import io
import time
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from udeep import mpl as hp
from udeep.cmap import cmap
from udeep.export import srgb_oetf
from udeep.gainmap import write_gainmap_jpeg
from udeep.metadata import data_steps

warnings.simplefilter("ignore")
rng = np.random.default_rng(0)


def best(f, repeat=3):
    out = []
    for _ in range(repeat):
        t = time.perf_counter(); f(); out.append(time.perf_counter() - t)
    return min(out)


def table(name: str, headroom: float, n: int):
    """Nodes on [0, 1] and on [1, headroom] separately: the map has a corner at white."""
    lo, hi = np.linspace(0.0, 1.0, n), np.linspace(1.0, headroom, n)
    return lo, cmap(lo, name, headroom).astype(np.float64), hi, cmap(hi, name, headroom).astype(np.float64)


def lookup(y, tab):
    lo, c_lo, hi, c_hi = tab
    y = np.asarray(y, np.float64)
    out = np.empty((*y.shape, 3))
    below = y <= 1.0
    for k in range(3):
        out[..., k] = np.where(below, np.interp(y, lo, c_lo[:, k]), np.interp(y, hi, c_hi[:, k]))
    return out


print("A. colour map as a table\n")
H = 4.0
y_test = np.concatenate([rng.uniform(0.0, H, 400_000), np.linspace(0.0, H, 100_001), [1.0, H, 0.0]])
for name in ("fire", "ice"):
    exact = cmap(y_test, name, H).astype(np.float64)
    code = lambda rgb, s=1.0: srgb_oetf(np.clip(rgb / s, 0.0, 1.0)) * 255.0     # as the SDR pass and the HDR pass store it
    for n in (257, 1025, 4097, 16385):
        t0 = time.perf_counter(); tab = table(name, H, n); build = time.perf_counter() - t0
        got = lookup(y_test, tab)
        sdr_err = np.abs(code(got) - code(exact))[y_test <= 1.0].max()
        hdr_err = np.abs(code(got, H) - code(exact, H)).max()
        flips = float(np.mean(np.any(np.round(code(got, H)) != np.round(code(exact, H)), axis=-1)))
        print(f"  {name:5} {n:6} nodes: build {build * 1e3:7.1f} ms | worst error {sdr_err:.4f} codes below white, "
              f"{hdr_err:.4f} in the HDR pass | {flips:.3%} of values round to a different 8-bit code")
y_img = rng.uniform(0.0, H, (1000, 1000))
tab = table("fire", H, 4097)
t_exact = best(lambda: cmap(y_img, "fire", H), repeat=1)
t_table = best(lambda: lookup(y_img, tab))
print(f"\n  one megapixel, 'fire': exact search {t_exact * 1e3:.0f} ms, table lookup {t_table * 1e3:.0f} ms "
      f"({t_exact / t_table:.0f} x faster)")
mono = all(np.all(np.diff(table(n_, H, 4097)[1] @ [0.2126, 0.7152, 0.0722]) >= 0) for n_ in ("fire", "ice"))
print(f"  table luminance never decreases below white (needed for invertibility): {mono}")

print("\nB. inside savefig_hdr, grey, 8 x 8 inch at 150 dpi\n")
d = rng.exponential(10.0, (1000, 1000))
S = {"kind": "linear", "lo": 0.0, "hi": 100.0}
fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
im = hp.imshow(ax, d, mode="extend", knee=50.0, stretch=S, headroom=H, cmap="grey", unit="counts", knee_rule="preset; x")
hp.colorbar(im, ax=ax)
t_total = best(lambda: hp.savefig_hdr(fig, "spike.jpg", headroom=H), repeat=2)
t_tight = best(lambda: hp._render(fig, True, 0.1), repeat=2)
t_plain = best(lambda: hp._render(fig, False, 0.1), repeat=2)
buf = io.BytesIO(); fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.1); raw = buf.getvalue()
t_png_decode = best(lambda: np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"), dtype=np.float64))
base, hdr = hp._compose(fig, H, True, 0.1)
t_compose = best(lambda: hp._compose(fig, H, True, 0.1), repeat=2)
t_jpeg = best(lambda: write_gainmap_jpeg(base, hdr, "spike.jpg", H), repeat=2)
t_steps = best(lambda: data_steps(S, 50.0, H, "grey"))
t_decl = best(lambda: hp.declaration(fig, H), repeat=2)
print(f"  savefig_hdr, whole                         {t_total * 1e3:7.0f} ms   ({base.shape[1]} x {base.shape[0]} pixels)")
print(f"    compose (three renders + arithmetic)     {t_compose * 1e3:7.0f} ms")
print(f"      one render, cropped to the labels      {t_tight * 1e3:7.0f} ms   (matplotlib draw, PNG encode, PNG decode)")
print(f"        of which decoding that PNG           {t_png_decode * 1e3:7.0f} ms")
print(f"      one render, not cropped                {t_plain * 1e3:7.0f} ms   (matplotlib draw only)")
print(f"    gain-map JPEG                            {t_jpeg * 1e3:7.0f} ms")
print(f"    declaration (levels, digests)            {t_decl * 1e3:7.0f} ms   (levels alone {t_steps * 1e3:.0f} ms)")
import os

os.remove("spike.jpg")
