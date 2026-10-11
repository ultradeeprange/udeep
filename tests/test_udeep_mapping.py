"""udeep.mapping: one record of parameters defines the chain value -> position -> light -> colour (D3),
in float64 (D8), on any array library that follows the Array API standard (D22), and invertibly (profile 6.3)."""
import dataclasses
import math

import array_api_strict as strict
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from udeep import Mapping, read_metadata, scale, tone
from udeep import mpl as hp

CASES = {
    "linear": Mapping.from_stretch({"kind": "linear", "lo": -160.0, "hi": 700.0}, 240.0, 4.0, "grey"),
    "offset": Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 90.0, "offset": 10.0}, 55.0, 2.0, "fire"),
    "log": Mapping.from_stretch({"kind": "log", "lo": 1.0, "hi": 1e4}, 100.0, 8.0, "ice"),
    "asinh": Mapping.from_stretch({"kind": "asinh", "lo": 0.0, "hi": 20.0, "width": 0.1}, 1.2, 4.0, "ice"),
    "proportional": Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 12.0}, 3.0, 4.0, "fire", proportional=True),
    "capped": Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 1.0}, 0.2, 2.0, "grey"),   # entry slope capped
}


def inside(m: Mapping, n=2001):
    return m.value_at(np.linspace(0.0, 1.0, n))


@pytest.mark.parametrize("m", CASES.values(), ids=CASES.keys())
def test_light_is_the_scale_and_tone_curve_and_meets_its_end_points(m):
    x = inside(m)
    t = scale(x - m.stretch_offset, m.kind, m.range_low, m.range_high, m.stretch_width)
    assert np.array_equal(m.light(x), tone(t, m.knee_position, m.reference_headroom))
    assert m.light(x).dtype == np.float64 and m.position(x).dtype == np.float64
    assert m.light(np.array([m.knee]))[0] == pytest.approx(1.0, abs=1e-12)
    assert m.light(x)[0] == 0.0 and m.light(x)[-1] == pytest.approx(m.reference_headroom, abs=1e-12)
    assert np.all(np.diff(m.light(x)) > 0.0)                          # strictly increasing across the range


@pytest.mark.parametrize("m", CASES.values(), ids=CASES.keys())
def test_the_mapping_can_be_inverted(m):
    x = inside(m)[1:]                                                 # the bottom of a log range is light 0 exactly
    y = m.light(x)
    assert np.allclose(m.value(y), x, rtol=1e-9, atol=1e-9 * (m.range_high - m.range_low))
    light = np.linspace(0.0, m.reference_headroom, 1001)
    assert np.allclose(m.light(m.value(light)), light, rtol=0.0, atol=1e-12 * m.reference_headroom)
    assert np.isnan(m.value(np.array([np.nan]))[0])


@pytest.mark.parametrize("m", CASES.values(), ids=CASES.keys())
def test_the_fair_sdr_rendering_is_the_same_below_the_knee_and_white_above(m):
    x = inside(m)
    hdr, sdr = m.light(x), m.light(x, headroom=1.0)
    below = hdr <= 1.0
    assert np.array_equal(sdr[below], hdr[below]) and np.all(sdr[~below] == 1.0)


@pytest.mark.parametrize("m", CASES.values(), ids=CASES.keys())
def test_it_runs_on_any_array_api_library_and_gives_the_same_numbers(m):
    """array_api_strict refuses anything outside the standard, so passing here means CuPy, PyTorch and the rest
    can run the same code (given their Array API namespaces)."""
    x = inside(m, 501).reshape(3, 167)
    xs = strict.asarray(x)
    for name in ("position", "light"):
        got = getattr(m, name)(xs)
        assert type(got) is type(xs)
        assert np.allclose(np.asarray(got), getattr(m, name)(x), rtol=1e-14, atol=1e-14)
    colours = m.colours(m.light(xs))
    assert type(colours) is type(xs) and colours.shape == (3, 167, 3)
    assert np.allclose(np.asarray(colours), m.colours(m.light(x)), rtol=1e-14, atol=1e-14)
    assert np.allclose(np.asarray(m.value(m.light(xs))), m.value(m.light(x)), rtol=1e-12, atol=1e-12)


def test_grey_is_the_identity_and_a_map_comes_from_its_table():
    y = np.linspace(0.0, 4.0, 9)
    assert np.array_equal(CASES["linear"].colours(y), np.stack([y, y, y], -1))
    assert CASES["log"].table.name == "ice" and CASES["log"].table.nodes == 4097
    assert CASES["linear"].table is None


def test_a_record_refuses_what_it_cannot_define():
    good = dataclasses.asdict(CASES["linear"])
    for change, word in (({"stretch": "sqrt"}, "stretch"), ({"range_high": -200.0}, "range"), ({"knee": 900.0}, "knee"),
                         ({"reference_headroom": 0.5}, "headroom"), ({"above_knee": "sigmoid"}, "knee"),
                         ({"stretch": "log"}, "log"), ({"stretch": "proportional"}, "proportional")):
        with pytest.raises(ValueError, match=word):
            Mapping(**{**good, **change})
    with pytest.raises(ValueError, match="registered"):
        _ = dataclasses.replace(CASES["linear"], colour_map="viridis/1").table


def test_a_proportional_mapping_is_light_equal_to_value_over_knee_or_it_is_refused():
    """Profile 6.6: light = value / (value at SDR white), over the whole range. That holds only when the top of
    the range is knee x headroom, so nothing else may be declared proportional."""
    for knee, headroom in ((3.0, 4.0), (0.7, 2.0), (7.0, 1.0), (1.0 / 3.0, 16.0)):
        m = Mapping("proportional", 0.0, knee * headroom, knee, headroom)
        x = inside(m)
        assert np.allclose(m.light(x), x / knee, rtol=1e-12, atol=1e-12)
    for headroom in (8.0, 2.0, 1.0):                                  # range 0..12 with the knee at 3 needs 4
        with pytest.raises(ValueError, match="knee x reference_headroom"):
            Mapping("proportional", 0.0, 12.0, 3.0, headroom)
    Mapping("proportional", 0.0, 12.0 * (1.0 + 1e-12), 3.0, 4.0)      # rounding in the product is not a refusal
    with pytest.raises(ValueError, match="proportional"):             # only exit=1 leaves the upper part straight
        Mapping("proportional", 0.0, 12.0, 3.0, 4.0, above_knee="hermite; exit=0")
    Mapping("linear", 0.0, 12.0, 3.0, 8.0)                            # other stretches are not affected


def test_a_knee_the_curve_would_have_to_move_is_refused():
    """The curve cannot place a knee closer than KNEE_MIN to either end of the range, so it used to draw another."""
    for knee, top in ((1.0, 1e7), (1.0 - 1e-8, 1.0)):
        with pytest.raises(ValueError, match="knee"):
            Mapping("linear", 0.0, top, knee, 4.0)
    with pytest.raises(ValueError, match="knee"):
        Mapping("log", 1.0, 1e4, 1.0 + 1e-9, 4.0)
    at_top = Mapping("linear", 0.0, 1.0, 1.0, 4.0)                    # exactly at the top: the full-range SDR plot
    assert at_top.knee_position == 1.0 and at_top.light(np.array([1.0]))[0] == 1.0
    for knee in (2e-6, 1.0 - 2e-6):                                   # just inside the limits: drawn as declared
        m = Mapping("linear", 0.0, 1.0, knee, 4.0)
        assert m.knee_position == knee
        assert m.light(np.array([knee, 1.0])) == pytest.approx([1.0, 4.0], abs=1e-9)


@pytest.mark.parametrize("field", ["range_low", "range_high", "knee", "reference_headroom", "stretch_width",
                                   "stretch_offset"])
@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_every_number_in_a_record_must_be_finite(field, bad):
    good = dataclasses.asdict(CASES["asinh"])
    with pytest.raises(ValueError, match=f"{field} must be a finite number"):
        Mapping(**{**good, field: bad})


def test_the_form_above_the_knee_is_checked_when_the_record_is_made():
    good = dataclasses.asdict(CASES["linear"])
    for text in ("hermite; exit=3.5", "hermite; exit=1.2.3", "hermite; exit=.", "hermite; exit=-1", "hermite; exit=1e2",
                 "hermite; exit=", "hermite; exit=1 ", "hermite;exit=1", "sigmoid"):
        with pytest.raises(ValueError, match="above the knee"):
            Mapping(**{**good, "above_knee": text})
    for text, slope in (("hermite; exit=0", 0.0), ("hermite; exit=1", 1.0), ("hermite; exit=0.5", 0.5),
                        ("hermite; exit=3", 3.0), ("hermite; exit=3.0", 3.0)):
        m = Mapping(**{**good, "above_knee": text})
        assert m.exit_slope == slope
        assert np.isfinite(m.light(inside(m, 11))).all()              # whatever is accepted can be computed with


def test_a_file_gives_back_the_record_its_figure_was_made_with(tmp_path):
    """D3: the declaration is the parameter block. What is read from the file computes the same light."""
    d = np.linspace(0.0, 100.0, 2400).reshape(40, 60)
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)
    img = hp.imshow(ax, d, mode="extend", knee=50.0, stretch={"kind": "linear", "lo": 0.0, "hi": 100.0}, cmap="fire",
                    headroom=4.0, unit="counts", knee_rule="preset; half of full scale")
    path = tmp_path / "f.jpg"
    hp.savefig_hdr(fig, path)
    made_with, read_back = img._udeep["view"].mapping, read_metadata(path).mapping
    assert read_back == made_with
    # to the tolerance of a colour-map table: the view may have been mapped by the Rust core, this line by NumPy
    assert np.allclose(read_back.colours(read_back.light(d)), img._udeep["view"].hdr, rtol=2e-6, atol=2e-6)
    plt.close(fig)


def test_a_knee_exactly_at_the_limit_is_accepted_whatever_the_rounding():
    """The limit is a position of 1e-6. Working a data value out from that position and back loses a few bits, and
    the test used to refuse about four such knees in ten."""
    from udeep.scale import unscale
    rng = np.random.default_rng(11)
    for kind in ("linear", "log", "asinh"):
        for _ in range(400):
            lo = float(rng.uniform(1e-3, 50.0)) if kind == "log" else float(rng.uniform(-100.0, 100.0))
            hi = lo + float(rng.uniform(1e-2, 1e5))
            for position in (1e-6, 1.0 - 1e-6):
                knee = float(unscale(np.array([position]), kind, lo, hi, 1.0)[0])
                Mapping(kind, lo, hi, knee, 4.0)                          # must not raise
    with pytest.raises(ValueError, match="knee"):
        Mapping("linear", 0.0, 1.0, 5e-7, 4.0)                            # well inside the limit is still refused
    with pytest.raises(ValueError, match="knee"):
        Mapping("linear", 0.0, 1.0, 1.0 - 5e-7, 4.0)
