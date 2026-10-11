"""Demo figures for the project site. Both datasets are SYNTHETIC (made here, seeded), so the site
carries no third-party or unpublished data.

extend    -- a CT-style phantom: soft tissue with low-contrast lesions and a bone ring with internal
             structure. Knee = 240 HU, the top of the standard soft-tissue window (W400/L40), so the
             SDR figure is that window and the bone detail lives above white.
highlight -- a noise-only spectrogram (exponential power per bin) with a faint chirp injected. Knee =
             the 1e-4 false-alarm level of the noise, ln(1e4)/ln(2) = 13.3x the median; only bins
             that noise alone would exceed once in 10,000 go above white.

Each writes <name>_hdr.jpg (gain map; the ordinary figure on SDR screens) and <name>_sdr.png (the
same figure as matplotlib saves it), and the same again for a dark page as <name>_dark_hdr.jpg and
<name>_dark_sdr.png (udeep.mpl.theme): the same data and mapping, other furniture.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from udeep import mpl as hp

OUT = Path(__file__).resolve().parents[1] / "docs" / "assets"
HEADROOM = 4.0
rng = np.random.default_rng(20260927)


def ellipse(n, cx, cy, a, b, angle=0.0):
    y, x = np.mgrid[:n, :n] / n * 2 - 1
    c, s = np.cos(angle), np.sin(angle)
    xr, yr = (x - cx) * c + (y - cy) * s, -(x - cx) * s + (y - cy) * c
    return (xr / a) ** 2 + (yr / b) ** 2 <= 1


def phantom(n=512):
    hu = np.full((n, n), -1000.0)                                  # air
    body = ellipse(n, 0, 0, 0.85, 0.7)
    hu[body] = 40.0                                                 # soft tissue
    for cx, cy, r, d in [(-0.35, 0.2, 0.10, 25), (0.3, 0.25, 0.07, -30), (0.05, -0.35, 0.08, 45),
                         (0.4, -0.2, 0.05, 60)]:
        hu[ellipse(n, cx, cy, r, r)] += d                           # low-contrast lesions (+-25..60 HU)
    ring = ellipse(n, 0, 0.05, 0.42, 0.34) & ~ellipse(n, 0, 0.05, 0.36, 0.28)
    y, x = np.mgrid[:n, :n]
    trabecular = 250 + 900 * (0.5 + 0.5 * np.sin(x / 3.1) * np.sin(y / 4.3)) ** 2   # bone texture 250..1150
    hu[ring] = 700 + trabecular[ring]                               # cortical + texture: 950..1850
    hu[ellipse(n, 0, 0.05, 0.30, 0.24) & ~ellipse(n, 0, 0.05, 0.27, 0.21)] = 300 + trabecular[
        ellipse(n, 0, 0.05, 0.30, 0.24) & ~ellipse(n, 0, 0.05, 0.27, 0.21)] * 0.6
    hu += rng.normal(0, 6, hu.shape) * body                         # CT noise, ~6 HU
    return hu


def spectrogram(nt=400, nf=300):
    power = rng.exponential(1.0, (nf, nt))                         # noise: exponential power, mean 1
    t = np.arange(nt); f0 = 40 + 0.0012 * t**2                      # quadratic chirp
    for k in range(nt):
        fi = round(f0[k])
        if fi < nf - 2:
            power[fi - 1:fi + 2, k] += 10.0 * np.array([0.4, 1.0, 0.4])
    return power


def figure(data, name, mode, knee, stretch, cmap, title, xlabel, ylabel, unit, declare, extent=None):
    one_figure(data, name, mode, knee, stretch, cmap, title, xlabel, ylabel, unit, declare, extent)   # matplotlib's own look
    with plt.rc_context(hp.theme("dark")):
        one_figure(data, name + "_dark", mode, knee, stretch, cmap, title, xlabel, ylabel, unit, declare, extent)


def one_figure(data, name, mode, knee, stretch, cmap, title, xlabel, ylabel, unit, declare, extent=None):
    fig, ax = plt.subplots(figsize=(6.4, 5.2), dpi=150)
    img = hp.imshow(ax, data, mode=mode, knee=knee, stretch=stretch, headroom=HEADROOM, cmap=cmap,
                    extent=extent, aspect="auto" if extent else "equal", origin="lower" if extent else "upper",
                    **declare)
    hp.colorbar(img, ax=ax, label=unit)
    ax.set_title(title, fontsize=11); ax.set_xlabel(xlabel); ax.set_ylabel(ylabel)
    fig.tight_layout()
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}_sdr.png", bbox_inches="tight", pad_inches=0.1)
    hp.savefig_hdr(fig, OUT / f"{name}_hdr.jpg", headroom=HEADROOM)
    checked = hp.validate(fig, OUT / f"{name}_hdr.jpg")
    if not checked.ok:
        raise RuntimeError(str(checked))
    plt.close(fig)


if __name__ == "__main__":
    figure(phantom(), "extend", "extend", 240.0, {"kind": "linear", "lo": -160.0, "hi": 1850.0}, "grey",
           "Synthetic CT phantom: soft-tissue window, bone above white", "x (pixel)", "y (pixel)", "HU",
           {"unit": "HU", "knee_rule": "preset; the top of the W400/L40 soft-tissue window", "noise": ("constant", 6.0)})
    p = spectrogram()
    knee = float(np.log(1e4) / np.log(2) * np.median(p))
    print("highlight knee (13.3 x median):", round(knee, 3), "median", round(float(np.median(p)), 3))
    figure(p, "highlight", "highlight", knee, {"kind": "linear", "lo": 0.0, "hi": 20.0}, "fire",
           "Synthetic spectrogram: a chirp in noise", "time (s)", "frequency (Hz)",
           "power / mean noise",
           {"unit": "mean noise power", "knee_rule": "statistical; noise alone exceeds it once in 10,000 tiles"},
           extent=(0, 4.0, 0, 300))
    for f in sorted(OUT.glob("*")):
        print(f.name, f.stat().st_size // 1024, "KiB")
