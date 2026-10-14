"""Colour-vision deficiency: does a colour map stay readable for people who lack one cone type?

    simulate(rgb, kind)     how linear RGB looks under protanopia, deuteranopia or tritanopia
    report(table)           per kind: is lightness still strictly increasing along the map, and how much of the
                            map's perceptual length survives

The simulation is that of Machado, Oliveira & Fernandes (2009), "A physiologically-based model for simulation of
color vision deficiency", IEEE TVCG 15(6), at full severity: one 3 x 3 matrix per kind, applied to linear RGB
with BT.709 primaries. The matrices were copied from the colorspacious package's data (its cvd.py), which gives
them as the paper's supplementary data. Being linear, the same matrix serves above SDR white.

What "readable" means here, following the usual advice for scientific maps (Crameri et al. 2020): lightness must
keep rising along the map under every simulation, so that order can always be read from lightness alone. The
perceptual length that survives is reported, not judged: it says how much finer discrimination is lost.
"""
from __future__ import annotations

import numpy as np

from udeep.color import REF_WHITE_NITS, srgb_to_xyz, xyz_to_jzazbz

MATRICES = {
    "protanopia": [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
    "deuteranopia": [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
    "tritanopia": [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]],
}
KINDS = tuple(MATRICES)
PARTS = 32                       # the map is cut into this many equal runs of light to find its flattest stretch


def simulate(rgb, kind: str) -> np.ndarray:
    """Linear RGB (BT.709 primaries) as seen with the named deficiency; values the display cannot show are
    clipped at zero."""
    if kind not in MATRICES:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    return np.maximum(np.asarray(rgb, dtype=np.float64) @ np.asarray(MATRICES[kind]).T, 0.0)


def _jab(rgb) -> np.ndarray:
    return xyz_to_jzazbz(srgb_to_xyz(np.asarray(rgb, dtype=np.float64)) * REF_WHITE_NITS)


def _length(jab: np.ndarray) -> np.ndarray:
    """Perceptual distance (Jzazbz) covered by each step along a map."""
    return np.linalg.norm(np.diff(jab, axis=0), axis=1)


def report(table) -> dict:
    """For normal vision and each deficiency: whether lightness (Jz) rises at every node of the table; if not, at
    how many nodes it falls, where the first is, and the deepest dip below an earlier level as a share of the
    map's whole lightness range; the share of the map's perceptual length that survives; and the same share over
    the map's flattest stretch (one of PARTS equal runs of light, in either segment)."""
    colours = np.concatenate([np.asarray(table.below, np.float64), np.asarray(table.above, np.float64)[1:]])
    light = np.concatenate([np.linspace(0.0, 1.0, table.nodes), np.linspace(1.0, table.headroom, table.nodes)[1:]])
    normal = _jab(colours)
    base = _length(normal)
    out = {}
    for kind in ("normal", *KINDS):
        jab = normal if kind == "normal" else _jab(simulate(colours, kind))
        rise = np.diff(jab[:, 0])
        steps = _length(jab)
        runs = [np.arange(len(base))[i * len(base) // (2 * PARTS):(i + 1) * len(base) // (2 * PARTS)] for i in range(2 * PARTS)]
        kept = [steps[r].sum() / base[r].sum() for r in runs if base[r].sum() > 0.0]
        span = float(jab[-1, 0] - jab[0, 0])
        dip = float(np.max(np.maximum.accumulate(jab[:, 0]) - jab[:, 0]))      # deepest fall below an earlier level
        falling = np.flatnonzero(rise <= 0.0)
        out[kind] = {"lightness_rises": bool(falling.size == 0), "falling_steps": int(falling.size),
                     "deepest_dip": dip / span, "dip_at_light": float(light[falling[0]]) if falling.size else None,
                     "length_kept": float(steps.sum() / base.sum()), "least_kept_in_a_stretch": float(min(kept))}
    return out


def main() -> None:
    """The command line, `python -m udeep.vision`: prints `report` for each current map at each registered
    headroom."""
    import importlib

    cm = importlib.import_module("udeep.cmap")           # the package also exports a function called cmap
    for name in sorted(cm.MAPS):
        for headroom in cm.REGISTERED_HEADROOMS:
            print(f"{cm.identifier(name)} at {headroom:g} x white")
            for kind, r in report(cm.table(name, headroom)).items():
                state = "rises throughout" if r["lightness_rises"] else (
                    f"FALLS at {r['falling_steps']} nodes from light {r['dip_at_light']:.3f}, "
                    f"by {r['deepest_dip']:.3%} of the range")
                print(f"  {kind:13} lightness {state}; perceptual length kept {r['length_kept']:.0%}, "
                      f"{r['least_kept_in_a_stretch']:.0%} in the flattest stretch")


if __name__ == "__main__":
    main()
