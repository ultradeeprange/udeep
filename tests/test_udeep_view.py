"""udeep.view: the two modes, extend and highlight."""
import numpy as np
import pytest

from udeep import hdr_view

STRETCH = {"kind": "linear", "lo": 0.0, "hi": 100.0}


def field(value=10.0, n=32):
    return np.full((n, n), value, dtype=np.float64)


@pytest.mark.parametrize("cmap", ["grey", "fire"])
@pytest.mark.parametrize("mode", ["highlight", "extend"])
def test_sdr_part_identical_and_bounded(mode, cmap):
    d = field(); d[5:10, 5:10] = 90.0; d[20, 3:30] = np.linspace(0, 60, 27)
    v = hdr_view(d, mode, knee=50.0, stretch=STRETCH, headroom=2.0, cmap=cmap)
    below = d <= 50.0
    assert np.allclose(v.hdr[below], v.sdr[below], atol=1e-6)
    assert v.sdr.max() <= 1.0 + 1e-6 and v.hdr.max() <= 2.0 + 1e-6


def test_value_at_the_knee_is_sdr_white():
    d = field(50.0)
    v = hdr_view(d, "extend", knee=50.0, stretch=STRETCH, headroom=2.0, cmap="grey")
    assert np.allclose(v.hdr, 1.0, atol=1e-6)


def test_highlight_holds_isolated_pixels_but_lifts_blocks():
    d = field(); d[3, 3] = 95.0; d[15:20, 15:20] = 95.0
    v = hdr_view(d, "highlight", knee=50.0, stretch=STRETCH, headroom=2.0, cmap="grey")
    assert v.hdr[3, 3].max() <= 1.0 + 1e-6          # noise-like single pixel stays at white
    assert v.hdr[17, 17].max() > 1.5                # a real 5x5 feature goes above white


def test_extend_lifts_thin_structure():
    d = field(); d[10, 2:30] = 95.0                  # 1-px line, e.g. bone cortex
    v = hdr_view(d, "extend", knee=50.0, stretch=STRETCH, headroom=2.0, cmap="grey")
    assert v.hdr[10, 15].max() > 1.5


@pytest.mark.parametrize("mode", ["highlight", "extend"])
def test_masked_pixels_are_never_lifted(mode):
    d = field(); d[8:16, 8:16] = 95.0
    mask = np.zeros_like(d, bool); mask[8:16, 8:16] = True
    v = hdr_view(d, mode, knee=50.0, stretch=STRETCH, headroom=2.0, cmap="grey", mask=mask)
    assert v.hdr[mask].max() <= 1.0 + 1e-6
    assert v.masked == int(mask.sum())


def test_percentile_knee_is_flagged_exploratory():
    d = np.random.default_rng(0).uniform(0, 100, (32, 32))
    v = hdr_view(d, "highlight", knee=None, stretch=STRETCH, headroom=2.0, cmap="grey")
    assert any("exploratory" in w for w in v.warnings)
    assert 0.0 < v.knee_value < 100.0


def test_extend_warns_when_too_much_is_above_white():
    d = field(); d[:, :16] = 95.0                    # half the plot above the knee
    v = hdr_view(d, "extend", knee=50.0, stretch=STRETCH, headroom=2.0, cmap="grey")
    assert v.above_white > 0.10
    assert any("brightness limiter" in w for w in v.warnings)


def test_colourbar_marks_sdr_white_in_data_units():
    v = hdr_view(field(), "extend", knee=50.0, stretch=STRETCH, headroom=2.0, cmap="fire")
    assert v.colourbar["sdr_white_value"] == pytest.approx(50.0)
    y = np.asarray(v.colourbar["y"])
    assert y.min() == pytest.approx(0.0) and y.max() == pytest.approx(2.0, abs=1e-6)


def test_rejects_unknown_mode():
    with pytest.raises(ValueError):
        hdr_view(field(), "sparkle", knee=50.0, stretch=STRETCH)


# ---- masks: the only neighbourhood operation, kept out of the mapping (design decision D2)

from udeep import masks


def test_isolated_rule_holds_what_a_3x3_block_does_not_cover():
    above = np.zeros((12, 12), bool)
    above[2, 2] = True                      # a single pixel
    above[5, 1:9] = True                    # a line one pixel wide
    above[8:11, 8:11] = True                # exactly one 3 x 3 block
    above[0:2, 9:12] = True                 # 2 x 3 at the edge: outside counts as not above
    got = masks.isolated(above)
    assert got[2, 2] and got[5, 1:9].all() and got[0:2, 9:12].all()
    assert not got[8:11, 8:11].any() and not got[~above].any()
    with pytest.raises(ValueError):
        masks.isolated(np.zeros((2, 2, 2), bool))


def test_the_mapping_is_pointwise_and_the_mask_is_a_separate_declared_step():
    d = field(); d[3, 3] = 95.0; d[15:20, 15:20] = 95.0
    mask = np.zeros(d.shape, bool); mask[16, 16] = True
    v = hdr_view(d, "highlight", knee=50.0, stretch=STRETCH, headroom=2.0, cmap="grey", mask=mask)
    m = v.mapping
    light, sdr = m.light(d), m.light(d, headroom=1.0)            # every pixel alone: no neighbours involved
    derived = masks.isolated((light > 1.0) & ~mask)              # the rule sees only pixels that would be lifted
    assert np.array_equal(v.y, masks.hold(light, sdr, mask | derived))
    assert (v.derived_mask, v.derived_masked, v.masked) == ("isolated-3x3", int(derived.sum()), 1)
    assert derived[3, 3] and v.derived_masked >= 1
    e = hdr_view(d, "extend", knee=50.0, stretch=STRETCH, headroom=2.0, cmap="grey", mask=mask)
    assert (e.derived_mask, e.derived_masked) == (None, 0)
    assert np.array_equal(e.y, masks.hold(light, sdr, mask))     # Extend: the mapping and the validity mask only


def test_a_pixel_whose_only_support_is_masked_is_isolated():
    """Masked pixels are never lifted, so they cannot make a block: one valid pixel among them would sparkle."""
    d = field(n=7); d[2:5, 2:5] = 90.0
    mask = np.zeros(d.shape, bool); mask[2:5, 2:5] = True; mask[3, 3] = False     # saturated all round the centre
    v = hdr_view(d, "highlight", knee=50.0, stretch=STRETCH, headroom=2.0, cmap="grey", mask=mask)
    assert v.y.max() <= 1.0 and (v.derived_masked, v.masked) == (1, 8)
    whole = hdr_view(d, "highlight", knee=50.0, stretch=STRETCH, headroom=2.0, cmap="grey")
    assert (whole.y > 1.0).sum() == 9 and whole.derived_masked == 0               # unmasked, the block is lifted
