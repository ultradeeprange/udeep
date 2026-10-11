"""udeep.vision: do the colour maps stay readable with a colour-vision deficiency?"""
import importlib

import numpy as np
import pytest

from udeep.vision import KINDS, MATRICES, report, simulate

cm = importlib.import_module("udeep.cmap")           # the package also exports a function called cmap

# Where lightness fell in VERSION 1 of the maps, as recorded 2026-10-08: (map, reference headroom, vision). All are
# under a thousandth of the map's lightness range. Version 2 was made to remove them and has none.
#   ice, red-green deficiencies: near 15 % of white the map ran along the edge of the gamut (red at zero).
#   the entries at headroom 2: the last node before the top, where the colour was clipped to the headroom.
KNOWN_FALLS_V1 = ({("ice", h, kind) for h in cm.REGISTERED_HEADROOMS for kind in ("protanopia", "deuteranopia")}
                  | {("ice", 2.0, "normal"), ("ice", 2.0, "tritanopia"), ("fire", 2.0, "tritanopia")})


def reports(version=cm.VERSION):
    return {(name, h): report(cm.table(name, h, version)) for name in sorted(cm.MAPS) for h in cm.REGISTERED_HEADROOMS}


def test_the_simulation_keeps_greys_and_changes_colours():
    grey = np.array([[0.0, 0.0, 0.0], [0.5, 0.5, 0.5], [4.0, 4.0, 4.0]])      # including above SDR white
    for kind in KINDS:
        assert np.allclose(np.sum(MATRICES[kind], axis=1), 1.0, atol=2e-6)     # each row sums to 1: white stays white
        assert np.allclose(simulate(grey, kind), grey, atol=1e-5)
    red, green = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    normal = np.linalg.norm(red - green)
    assert np.linalg.norm(simulate(red, "deuteranopia") - simulate(green, "deuteranopia")) < 0.8 * normal
    with pytest.raises(ValueError):
        simulate(grey, "monochromacy")


def test_lightness_rises_at_every_node_for_every_kind_of_vision():
    """The point of version 2: order can be read from lightness alone, by anyone, at every shipped headroom."""
    found = {(name, h, kind) for (name, h), r in reports().items() for kind, v in r.items() if not v["lightness_rises"]}
    assert found == set()
    assert len(reports()) == 8 and all(set(r) == {"normal", *KINDS} for r in reports().values())


def test_version_1_falls_exactly_where_recorded():
    found = {(name, h, kind) for (name, h), r in reports(1).items() for kind, v in r.items() if not v["lightness_rises"]}
    assert found == KNOWN_FALLS_V1
    worst = max(v["deepest_dip"] for r in reports(1).values() for v in r.values())
    assert 0.0 < worst < 1e-3, worst                                # measured: 0.042 % (ice at 2x, protanopia)


def test_most_of_each_maps_perceptual_length_survives():
    kept = {(name, h, kind): r[kind]["length_kept"] for (name, h), r in reports().items() for kind in KINDS}
    assert min(kept.values()) > 0.7                                 # measured: 74 % (ice at 2x, tritanopia)
    assert min(v for (name, _h, _k), v in kept.items() if name == "fire") > 0.8
