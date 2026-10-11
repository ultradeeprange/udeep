"""The two pictures that open the site and the README: what "range" and "depth" mean. Both are SYNTHETIC and
ILLUSTRATIVE, and both are ordinary 8-bit images, so that they read on any screen.

intro_range -- the CT phantom of make_demos.py, as the ordinary figure (clipped at white) and as an HDR display
               would show it. The second panel is a simulation for SDR screens: every value is divided by the
               headroom, so that what lies above white fits below it. The real file is docs/assets/extend_hdr.jpg.
intro_depth -- a smooth, noise-free field stored in 256 and in 4096 evenly spaced levels, then looked at through a
               narrow window of the range stretched to fill the screen, as one does when looking for faint
               structure. With 256 levels the window holds a handful of them.

Each is written for a light and for a dark page: <name>_sdr.png and <name>_dark_sdr.png, the same size.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from make_demos import phantom
from udeep import mpl as hp
from udeep.view import hdr_view

OUT = Path(__file__).resolve().parents[1] / "docs" / "assets"
HEADROOM = 4.0
WINDOW = (0.500, 0.526)                  # the part of the full range [0, 1] that the depth picture looks at


def srgb(linear):
    c = np.clip(linear, 0.0, 1.0)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def panels(titles):
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 4.7), dpi=150)
    for ax, title in zip(axes, titles, strict=True):
        ax.set_title(title, fontsize=11)
        ax.set_xticks([]); ax.set_yticks([])
    fig.subplots_adjust(left=0.02, right=0.98, top=0.91, bottom=0.02, wspace=0.04)
    return fig, axes


def both_themes(draw, name):
    for suffix, rc in (("", {}), ("_dark", hp.theme("dark"))):
        with plt.rc_context(rc):
            fig = draw()
            path = OUT / f"{name}{suffix}_sdr.png"
            fig.savefig(path)
            plt.close(fig)
            print(path.name, path.stat().st_size // 1024, "KiB")


def range_picture(view):
    def draw():
        fig, axes = panels(["An ordinary figure: the bone stops at white",
                            f"On an HDR display (simulated: all {HEADROOM:g}x dimmer)"])
        axes[0].imshow(srgb(view.sdr), interpolation="nearest")
        axes[1].imshow(srgb(view.hdr / HEADROOM), interpolation="nearest")
        return fig
    return draw


def field(height=300, width=400):
    """A smooth field inside WINDOW: a gentle slope with two faint, broad features. No noise."""
    y, x = np.mgrid[:height, :width]
    u, v = x / (width - 1), y / (height - 1)
    bump = np.exp(-((u - 0.68) ** 2 + (v - 0.42) ** 2) / 0.035) - 0.6 * np.exp(-((u - 0.28) ** 2 + (v - 0.66) ** 2) / 0.02)
    shape = 0.55 * u + 0.25 * v + 0.2 * bump
    shape = (shape - shape.min()) / (shape.max() - shape.min())
    return WINDOW[0] + (WINDOW[1] - WINDOW[0]) * shape


def depth_picture(data):
    stored = {bits: np.round(data * (2 ** bits - 1)) / (2 ** bits - 1) for bits in (8, 12)}
    count = {bits: len(np.unique(q)) for bits, q in stored.items()}
    print("levels inside the window:", count)

    def draw():
        fig, axes = panels([f"Stored in 8 bits: {count[8]} levels in this window",
                            f"Stored in 12 bits: {count[12]} levels in this window"])
        for ax, bits in zip(axes, (8, 12), strict=True):
            ax.imshow(stored[bits], cmap="gray", vmin=WINDOW[0], vmax=WINDOW[1], interpolation="nearest", aspect="auto")
        return fig
    return draw, count


if __name__ == "__main__":
    view = hdr_view(phantom(), "extend", 240.0, {"kind": "linear", "lo": -160.0, "hi": 1850.0}, HEADROOM, "grey")
    both_themes(range_picture(view), "intro_range")
    draw, _count = depth_picture(field())
    both_themes(draw, "intro_depth")
