"""Masks: the only place where a pixel's treatment may depend on its neighbours (design decision D2).

The mapping from data to light is pointwise. Anything that needs a neighbourhood runs here, before the mapping,
and returns a mask. Pixels in a mask are held at or below SDR white; the mapping never sees their neighbours.

    isolated(above)   pixels above the knee with no 3 x 3 block of such pixels around them: the Highlight preset
                      holds these, so that noise cannot sparkle

Every derived mask has a rule name, which the file declares (`udeep:DerivedMask`), with the number of pixels it
holds (`udeep:DerivedMasked`).
"""
from __future__ import annotations

import numpy as np

ISOLATED = "isolated-3x3"        # rule name: above the knee, and removed by a 3 x 3 morphological opening


def open3(b: np.ndarray) -> np.ndarray:
    """3 x 3 morphological opening of a boolean image (erode, then dilate); outside the image counts as False."""
    p = np.pad(b, 1)
    shifts = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    h, w = b.shape
    eroded = np.logical_and.reduce([p[1 + dy:1 + dy + h, 1 + dx:1 + dx + w] for dy, dx in shifts])
    pe = np.pad(eroded, 1)
    return np.logical_or.reduce([pe[1 + dy:1 + dy + h, 1 + dx:1 + dx + w] for dy, dx in shifts]) & b


def isolated(above) -> np.ndarray:
    """The pixels of `above` (a boolean image: light above SDR white) that belong to no full 3 x 3 block of it."""
    above = np.asarray(above, dtype=bool)
    if above.ndim != 2:
        raise ValueError("the isolated-pixel rule needs a 2-D image")
    return above & ~open3(above)


def hold(light, sdr_light, mask):
    """Apply a mask to mapped light, pointwise: masked pixels take the fair SDR light (at most SDR white)."""
    return np.where(np.asarray(mask, dtype=bool), sdr_light, light)
