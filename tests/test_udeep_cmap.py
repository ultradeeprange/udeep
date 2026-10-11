"""udeep.cmap: y -> HDR colour."""
import numpy as np
import pytest

from udeep import cmap
from udeep.color import srgb_to_xyz

Y = np.linspace(0.0, 4.0, 801)


@pytest.mark.parametrize("name", ["fire", "ice"])
def test_sdr_part_in_gamut(name):
    rgb = cmap(Y[Y <= 1.0], name, headroom=4.0)
    assert rgb.min() >= 0.0 and rgb.max() <= 1.0


@pytest.mark.parametrize("name", ["fire", "ice"])
def test_luminance_monotone(name):
    lum = srgb_to_xyz(cmap(Y, name, headroom=4.0))[..., 1]
    assert np.all(np.diff(lum) > 0.0)               # a coarse grid: the tables' own nodes are checked further down


@pytest.mark.parametrize("name", ["fire", "ice"])
def test_ends_black_and_white_hot(name):
    assert np.all(cmap([0.0], name, headroom=4.0) < 0.02)
    assert np.allclose(cmap([4.0], name, headroom=4.0), 4.0, atol=0.02)


@pytest.mark.parametrize("name", ["fire", "ice"])
def test_no_white_band_at_sdr_white(name):
    y = np.linspace(0.9, 1.1, 201)
    rgb = cmap(y, name, headroom=4.0).astype(np.float64)
    sat = np.ptp(rgb, axis=-1) / rgb.max(axis=-1)
    assert np.max(np.abs(np.diff(sat))) < 0.01


def test_headroom_one_is_pure_sdr():
    rgb = cmap(np.linspace(0.0, 1.0, 101), "fire", headroom=1.0)
    assert rgb.max() <= 1.0


def test_shape_dtype_nan_and_bad_name():
    out = cmap(np.array([[0.5, np.nan]]), "fire", headroom=4.0)
    assert out.shape == (1, 2, 3) and out.dtype == np.float64
    assert np.all(np.isnan(out[0, 1]))
    with pytest.raises(ValueError):
        cmap([0.5], "viridis")


# ---- the table is the definition of a map (design decision D9)

import importlib
import json

from udeep.export import srgb_oetf

cm = importlib.import_module("udeep.cmap")           # the package also exports a function called cmap
TABLE_TOLERANCE = 2e-6                               # relative and absolute: udeep.golden.TABLE


@pytest.mark.parametrize("version", sorted(cm.DEFINITIONS))
@pytest.mark.parametrize("name", ["fire", "ice"])
@pytest.mark.parametrize("headroom", cm.REGISTERED_HEADROOMS)
def test_shipped_tables_are_what_the_generator_makes(name, headroom, version):
    """Every version, the old ones too: a file made with one must be readable for good.

    The shipped table is the definition (profile 6.10); the generator is how it was made. Another system's maths
    library rounds the last bits of a power or a cube root differently (first seen on Linux, 2026-10-11: a few
    units in the last place of a float32), so the generator must give the shipped table to the table tolerance
    of the golden vectors, not bit for bit."""
    shipped, made = cm.table(name, headroom, version), cm.generate(name, headroom, version=version)
    assert shipped.nodes == made.nodes == cm.NODES
    for stored, again in ((shipped.below, made.below), (shipped.above, made.above)):
        assert np.allclose(again, stored, rtol=TABLE_TOLERANCE, atol=TABLE_TOLERANCE)
    index = json.loads((cm.TABLES / "index.json").read_text(encoding="utf-8"))
    assert index["tables"][f"{name}/{version}@{headroom:g}"]["sha256"] == shipped.digest


def test_version_1_is_still_the_table_it_was_and_version_2_is_another():
    # digests of two version-1 tables as first shipped (2026-10-08): a version, once shipped, never changes
    assert cm.table("fire", 4.0, 1).digest.startswith("sha256:bbe06aa43418")
    assert cm.table("ice", 2.0, 1).digest.startswith("sha256:b33e76be5dd5")
    assert cm.VERSION == 2 and cm.table("fire", 4.0).digest == cm.table("fire", 4.0, 2).digest
    assert cm.table("fire", 4.0, 2).digest != cm.table("fire", 4.0, 1).digest
    assert cm.identifier("ice") == "ice/2" and cm.identifier("ice", 1) == "ice/1"
    assert cm.parse("fire/1") == ("fire", 1) and cm.parse("fire/2") == ("fire", 2)
    assert cm.parse("fire/3") is None and cm.parse("viridis/1") is None and cm.parse("fire") is None
    with pytest.raises(ValueError, match="version"):
        cm.table("fire", 4.0, 3)


# Every node of a VERSION 1 table after which luminance does not rise, found 2026-10-08: (map, reference headroom,
# node of the segment above white, of 4097). Each is one node wide and just under the top, where that generator's
# colour was clipped to the headroom; the deepest is 3.8e-4 of SDR white. Version 2 has none.
KNOWN_LUMINANCE_FALLS_V1 = {("fire", 2.0, 4079), ("ice", 2.0, 4085), ("ice", 4.0, 4086)}


def luminance_falls(name, headroom, version):
    t = cm.table(name, headroom, version)
    rises = [np.diff(srgb_to_xyz(seg.astype(np.float64))[..., 1]) > 0.0 for seg in (t.below, t.above)]
    assert rises[0].all(), f"{name}/{version}@{headroom:g}: luminance does not rise below white"
    return {(name, headroom, int(i)) for i in np.flatnonzero(~rises[1])}


def test_luminance_rises_at_every_node_of_every_current_table():
    found = set().union(*(luminance_falls(name, h, cm.VERSION) for name in sorted(cm.MAPS) for h in cm.REGISTERED_HEADROOMS))
    assert found == set()


def test_version_1_tables_fall_exactly_where_recorded():
    """Version 1 stays as it was shipped, faults and all: these are what version 2 was made to remove."""
    found = set().union(*(luminance_falls(name, h, 1) for name in ("fire", "ice") for h in cm.REGISTERED_HEADROOMS))
    assert found == KNOWN_LUMINANCE_FALLS_V1


def test_the_top_of_every_current_table_is_white_and_was_not_in_version_1():
    for name in sorted(cm.MAPS):
        for h in cm.REGISTERED_HEADROOMS:
            top = cm.table(name, h).above[-1]
            assert np.array_equal(top, np.full(3, h, np.float32)), (name, h, top)          # exactly the headroom, neutral
            old = cm.table(name, h, 1).above[-1].astype(np.float64) / h                     # clipped in red, short elsewhere
            assert old[0] == 1.0 and np.allclose(old[1:], [0.99909, 0.99870], atol=1e-5), (name, h, old)


def test_the_versions_differ_little():
    """How far a colour moved between the versions, in one channel, as a share of white below it and of the headroom
    above. fire is a correction only (the grey it is measured from). ice also asks for less chroma at its second
    node, so its blues near 15 to 45 % of the range are less saturated: measured 0.150 of white at most."""
    limits = {"fire": 0.04, "ice": 0.16}
    for name in ("fire", "ice"):
        for h in cm.REGISTERED_HEADROOMS:
            a, b = cm.table(name, h, 1), cm.table(name, h, 2)
            moved = max(float(np.abs(a.below - b.below).max()), float(np.abs(a.above - b.above).max()) / h)
            assert 0.0 < moved < limits[name], (name, h, moved)
    assert float(np.abs(cm.table("ice", 2.0, 1).below - cm.table("ice", 2.0, 2).below).max()) > 0.1


@pytest.mark.parametrize("name", ["fire", "ice"])
def test_below_white_a_map_does_not_depend_on_the_headroom(name):
    first = cm.table(name, 2.0).below
    assert all(np.array_equal(cm.table(name, h).below, first) for h in cm.REGISTERED_HEADROOMS)
    assert np.array_equal(cm.table(name, 1.0).below, first)        # an SDR figure uses the same lower segment


@pytest.mark.parametrize("name", ["fire", "ice"])
def test_lookup_is_exact_at_nodes_and_close_between(name):
    nodes = np.concatenate([np.linspace(0.0, 1.0, cm.NODES), np.linspace(1.0, 4.0, cm.NODES)[1:]])
    # at a node the table holds the generator's own value: to the last bit where the table was made, and to the
    # table tolerance under another maths library
    assert np.allclose(cmap(nodes, name, 4.0), cm.search(nodes, name, 4.0), rtol=TABLE_TOLERANCE, atol=TABLE_TOLERANCE)
    y = np.random.default_rng(1).uniform(0.0, 4.0, 200_000)
    got, exact = cmap(y, name, 4.0).astype(float), cm.search(y, name, 4.0).astype(float)
    code = lambda rgb, s: srgb_oetf(np.clip(rgb / s, 0.0, 1.0)) * 255.0
    assert np.abs(code(got, 1.0) - code(exact, 1.0))[y <= 1.0].max() < 0.3      # measured 0.22 / 0.14 (fire / ice)
    assert np.abs(code(got, 4.0) - code(exact, 4.0)).max() < 0.15               # measured 0.08


def test_outside_the_range_takes_the_end_colour():
    assert np.array_equal(cmap([-1.0, 9.0], "fire", 4.0), cmap([0.0, 4.0], "fire", 4.0))


def test_an_unregistered_headroom_gets_a_small_table_that_fits_in_a_file():
    t = cm.table("fire", 3.0)
    assert t.nodes == cm.EMBED_NODES and not cm.is_registered(3.0) and cm.is_registered(4.0) and cm.is_registered(1.0)
    text = t.to_text()
    assert len(text) < 16_000                                       # a JPEG metadata segment holds 65,533 bytes
    back = cm.Table.from_text("fire", 3.0, text)
    assert back.to_bytes() == t.to_bytes() and back.digest == t.digest
    y = np.linspace(0.0, 3.0, 1001)
    assert np.array_equal(back.lookup(y), cmap(y, "fire", 3.0))
    lum = srgb_to_xyz(cmap(y, "fire", 3.0))[..., 1]
    assert np.all(np.diff(lum) >= -1e-6)


def test_identifier_carries_the_version():
    assert cm.identifier("fire") == f"fire/{cm.VERSION}"
