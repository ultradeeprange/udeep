"""A fluorescence frame with dim structure and a few saturated beads. Extend, with the saturated pixels held.

SYNTHETIC data: a 12-bit camera frame. Faint filaments reach about 900 counts; four beads are far brighter, and
the brightest two saturate the camera at 4,095 counts.

The knee is a preset: white is put at 1,000 counts, just above the brightest filament, so the filaments use the
whole of the ordinary figure and the beads go above white. A saturated pixel is not a measurement, only a lower
bound, so those pixels are given as a mask: the figure holds them at white, never lifts them, and the file counts
them.
"""
import matplotlib.pyplot as plt
import numpy as np
from _common import save

from udeep import mpl as hp

FULL_SCALE, READ_NOISE = 4095.0, 15.0


def frame(size: int = 360) -> np.ndarray:
    rng = np.random.default_rng(20261014)
    y, x = np.mgrid[:size, :size] / size
    image = np.full((size, size), 120.0)                                  # camera offset and background
    for k in range(7):                                                    # filaments: faint curved lines
        phase, slope = rng.uniform(0, 6.28), rng.uniform(-0.6, 0.6)
        line = 0.15 + 0.11 * k + slope * (x - 0.5) + 0.03 * np.sin(9 * x + phase)
        image += rng.uniform(350, 780) * np.exp(-0.5 * ((y - line) / 0.006) ** 2)
    for cx, cy, peak in ((0.25, 0.3, 2600.0), (0.7, 0.2, 9000.0), (0.6, 0.72, 15000.0), (0.2, 0.8, 1900.0)):
        image += peak * np.exp(-0.5 * (((x - cx) ** 2 + (y - cy) ** 2) / 0.012 ** 2))
    image += rng.normal(0.0, READ_NOISE, image.shape)
    return np.clip(np.round(image), 0.0, FULL_SCALE)                      # the camera's 12 bits


def build():
    data = frame()
    saturated = data >= FULL_SCALE
    fig, ax = plt.subplots(figsize=(5.8, 5.0), dpi=150)
    img = hp.imshow(ax, data, mode="extend", knee=1000.0, stretch={"kind": "linear", "lo": 0.0, "hi": FULL_SCALE},
                    cmap="grey", headroom=4.0, mask=saturated, unit="counts",
                    knee_rule="preset; white at 1,000 counts, just above the brightest filament",
                    noise=("constant", READ_NOISE))
    hp.colorbar(img, ax=ax, label="counts")
    ax.set(title=f"Synthetic fluorescence frame: {int(saturated.sum())} saturated pixels held at white",
           xlabel="x (pixel)", ylabel="y (pixel)")
    return fig


if __name__ == "__main__":
    save(build, "microscope_frame")
