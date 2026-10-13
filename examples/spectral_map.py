"""A spectral line scan with faint lines in counting noise. Highlight: only what noise alone would rarely reach.

SYNTHETIC data: Poisson counts on a flat background of 20 counts a bin, with three lines added, one of them weak.

The knee is statistical. It is the smallest count that noise alone, at a mean of 20, reaches with a probability of
one in 10,000 or less: the Poisson tail, worked out below and not assumed. A bin brighter than white is therefore
one that noise would give about once in 10,000 bins, which is about 12 of this scan's 120,000. Everything below
the knee is the ordinary figure.
"""
import math

import matplotlib.pyplot as plt
import numpy as np
from _common import save

from udeep import mpl as hp

BACKGROUND, FALSE_ALARM = 20.0, 1e-4


def poisson_threshold(mean: float, tail: float) -> int:
    """The smallest count k that a Poisson variable of this mean reaches, or passes, with probability <= tail."""
    k, term, below = 0, math.exp(-mean), 0.0
    while 1.0 - below > tail:                    # below = P(X < k)
        below += term
        k += 1
        term *= mean / k
    return k


def counts(positions: int = 300, channels: int = 400) -> np.ndarray:
    rng = np.random.default_rng(20261013)
    channel = np.arange(channels)[None, :]
    position = np.linspace(0.0, 1.0, positions)[:, None]
    expected = np.full((positions, channels), BACKGROUND)
    for centre, width, height, where in ((90, 3.0, 60.0, 0.30), (210, 4.0, 25.0, 0.55), (320, 3.0, 9.0, 0.75)):
        present = np.exp(-((position - where) / 0.12) ** 2)               # each line is there over part of the scan
        expected = expected + height * present * np.exp(-0.5 * ((channel - centre) / width) ** 2)
    return rng.poisson(expected).astype(float)


def build():
    data = counts()
    knee = poisson_threshold(BACKGROUND, FALSE_ALARM)
    rule = f"statistical; Poisson noise of mean {BACKGROUND:g} reaches this with probability {FALSE_ALARM:g} or less"
    fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=150)
    img = hp.imshow(ax, data, mode="highlight", knee=float(knee), stretch={"kind": "linear", "lo": 0.0, "hi": 100.0},
                    cmap="fire", headroom=4.0, unit="counts", knee_rule=rule, noise=("poisson", 1.0),
                    aspect="auto", origin="lower")
    hp.colorbar(img, ax=ax, label="counts per bin")
    ax.set(title="Synthetic line scan: three lines in counting noise", xlabel="energy channel", ylabel="position (step)")
    return fig


if __name__ == "__main__":
    print("knee:", poisson_threshold(BACKGROUND, FALSE_ALARM), "counts")
    save(build, "spectral_map")
