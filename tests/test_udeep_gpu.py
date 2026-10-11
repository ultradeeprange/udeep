"""The reference shader (shaders/mapping.wgsl), run on a GPU and read back, must agree with the golden vectors and
with the float64 reference, to the float32 tolerances of design decision D8: 2e-6 (relative and absolute) in
position and light, 1e-5 in colour, and never as much as half an 8-bit code. A mapping for which float32 cannot do
that must be refused (udeep.gpu.check), not answered less well.

Skipped when the optional `wgpu` package or a GPU adapter is missing.
"""
import dataclasses
import json
import re
import struct

import numpy as np
import pytest

from udeep import Mapping, golden
from udeep.export import srgb_oetf

wgpu = pytest.importorskip("wgpu")
from udeep import gpu

LIGHT, COLOUR = 2e-6, 1e-5                             # D8: position and light; colour, judged by what reaches a screen
FLOAT32 = {"tol": {"rel": LIGHT, "abs": LIGHT}}
FLOAT32_COLOUR = {"tol": {"rel": COLOUR, "abs": COLOUR}}
REFUSED_GOLDEN = set()                                 # the golden mapping cases `check` refuses: none


@pytest.fixture(scope="module", autouse=True)
def adapter():
    try:
        return gpu.adapter_name()
    except (RuntimeError, OSError, wgpu.GPUError) as err:                             # no adapter, no driver: not this machine's fault
        pytest.skip(f"no usable GPU adapter: {err}")


def mapping_cases():
    cases = json.loads(golden.PATH.read_text(encoding="utf-8"))["cases"]
    return {name: c for name, c in cases.items() if c["kind"] == "mapping"}


def held_cases():
    return [name for name in mapping_cases() if name not in REFUSED_GOLDEN]


def at_position(stretch: dict, position: float, headroom: float, cmap: str = "grey", exit_slope: float = 1.0):
    """The mapping with this stretch whose knee sits at `position` of the range."""
    top = stretch["hi"] + stretch.get("offset", 0.0)
    knee = float(Mapping.from_stretch(stretch, top, headroom, cmap).value_at(np.array([position]))[0])
    return dataclasses.replace(Mapping.from_stretch(stretch, knee, headroom, cmap),
                               above_knee=f"hermite; exit={exit_slope:g}")


def image(m: Mapping, seed: int = 3) -> np.ndarray:
    """Float32 values over the whole range, and closely spaced where the mapping is hardest: the two ends and
    just above the knee. This is what the GPU is given, exactly; the reference is given the same numbers."""
    rng = np.random.default_rng(seed)
    knee = m.knee_position
    t = np.concatenate([rng.uniform(0.0, 1.0, 120_000), rng.uniform(0.985, 1.0, 40_000), rng.uniform(0.0, 0.02, 20_000),
                        knee + rng.uniform(0.0, 1.0, 20_000) * min(0.02, 1.0 - knee)])
    return m.value_at(t).astype(np.float32)


def errors(m: Mapping, x: np.ndarray) -> dict:
    """The shader's largest error against the float64 reference: each in units of its own tolerance (2e-6 for
    position and light, 1e-5 for colours), and in 8-bit codes."""
    got = gpu.run(m, x)
    want_light = m.light(x.astype(np.float64))
    want = {"position": m.position(x.astype(np.float64)), "light": want_light, "colours": m.colours(want_light)}
    tolerance = {"position": LIGHT, "light": LIGHT, "colours": COLOUR}
    out = {key: float(np.max(np.abs(got[key].astype(np.float64) - w) / (tolerance[key] * (1.0 + np.abs(w)))))
           for key, w in want.items()}
    code = lambda rgb: srgb_oetf(np.clip(rgb / m.reference_headroom, 0.0, 1.0)) * 255.0
    out["codes"] = float(np.abs(code(got["colours"].astype(np.float64)) - code(want["colours"])).max())
    return out


def assert_within_tolerance(m: Mapping):
    found = errors(m, image(m))
    assert max(found["position"], found["light"], found["colours"]) <= 1.0 and found["codes"] < 0.5, found


def unchecked_errors(m: Mapping, x: np.ndarray) -> dict:
    """`errors` for a mapping `check` refuses: what the shader does with it all the same."""
    block = gpu._pack(m)
    original, gpu.uniform_block = gpu.uniform_block, lambda _mapping: block
    try:
        return errors(m, x)
    finally:
        gpu.uniform_block = original


def test_the_tolerances_are_those_of_d8():
    assert (gpu.TOLERANCE, gpu.COLOUR_TOLERANCE) == (LIGHT, COLOUR)
    assert COLOUR < 0.5 / 1023 / 40                   # colour is still far finer than half a 10-bit code


# ---- the uniform block

def test_the_uniform_block_is_64_bytes_worked_out_in_float64():
    m = Mapping.from_stretch({"kind": "asinh", "lo": 0.0, "hi": 20.0, "width": 0.1}, 1.2, 4.0, "ice")
    block = gpu.uniform_block(m)
    assert len(block) == 64 == struct.calcsize(gpu.LAYOUT)
    f = dict(zip(gpu.FIELDS, struct.unpack(gpu.LAYOUT, block), strict=True))
    assert (f["offset_hi"], f["offset_lo"], f["low_hi"], f["low_lo"]) == (0.0, 0.0, 0.0, 0.0)
    assert (f["reference_headroom"], f["exit_slope"], f["stretch"], f["nodes"]) == (4.0, 1.0, 2, 4097)
    assert (f["reserved0"], f["reserved1"]) == (0, 0)
    assert f["scale"] == pytest.approx(10.0, rel=1e-7) and f["inv_span"] == pytest.approx(1.0 / np.arcsinh(200.0), rel=1e-7)
    assert f["shoulder_width"] == pytest.approx(1.0 - m.knee_position, rel=1e-7)
    assert f["inv_headroom_span"] == pytest.approx(1.0 / 3.0, rel=1e-7)
    grey = Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 1.0}, 0.5, 2.0)
    assert dict(zip(gpu.FIELDS, struct.unpack(gpu.LAYOUT, gpu.uniform_block(grey)), strict=True))["nodes"] == 0


def test_the_block_has_the_fields_of_the_shaders_struct_in_its_order():
    text = gpu.SHADER.read_text(encoding="utf-8")
    body = re.search(r"struct Mapping \{(.*?)\n\}", text, re.DOTALL).group(1)
    members = re.findall(r"^\s*(\w+): (f32|u32),", body, re.MULTILINE)
    assert tuple(name for name, _ in members) == gpu.FIELDS
    assert "<" + "".join("f" if kind == "f32" else "I" for _, kind in members) == "<" + "f" * 12 + "I" * 4
    assert struct.calcsize("<" + "f" * 12 + "I" * 4) == struct.calcsize(gpu.LAYOUT)
    assert "64 bytes" in text and "64 bytes" in gpu.uniform_block.__doc__


def test_a_number_that_is_subtracted_is_passed_as_a_pair_that_adds_up_to_it():
    def fields(m):
        return dict(zip(gpu.FIELDS, struct.unpack(gpu.LAYOUT, gpu.uniform_block(m)), strict=True))

    f = fields(Mapping.from_stretch({"kind": "linear", "lo": 20000.1, "hi": 20001.1, "offset": 10.3}, 20010.9, 4.0))
    assert f["offset_hi"] == float(np.float32(20010.4)) != 20010.4       # the range's low end, as the data has it
    assert f["offset_hi"] + f["offset_lo"] / f["scale"] == pytest.approx(20010.4, rel=1e-11)
    assert f["offset_lo"] != 0.0 and (f["low_hi"], f["low_lo"], f["inv_span"]) == (0.0, 0.0, 1.0)
    m = Mapping.from_stretch({"kind": "asinh", "lo": -2.3, "hi": 417.7, "width": 0.7, "offset": 0.1}, 20.0, 4.0)
    f = fields(m)
    assert f["offset_hi"] + f["offset_lo"] / f["scale"] == pytest.approx(0.1, rel=1e-11)
    assert f["low_hi"] + f["low_lo"] / f["inv_span"] == pytest.approx(np.arcsinh(-2.3 / 0.7), rel=1e-11)
    assert f["knee_hi"] + f["knee_lo"] * f["shoulder_width"] == pytest.approx(m.knee_position, rel=1e-11)
    f = fields(Mapping.from_stretch({"kind": "log", "lo": 0.37, "hi": 41.3, "offset": 5.3}, 8.6, 4.0))
    assert f["scale"] == pytest.approx(1.0 / 0.37, rel=1e-7) and f["inv_span"] == pytest.approx(1.0 / np.log(41.3 / 0.37))
    assert f["offset_hi"] + f["offset_lo"] / f["scale"] == pytest.approx(5.3, rel=1e-11)


# ---- the golden vectors

@pytest.mark.parametrize("name", held_cases())
def test_the_shader_reproduces_the_golden_mapping_cases(name):
    case = mapping_cases()[name]
    m = Mapping(**case["args"]["mapping"])
    keep = [i for i, v in enumerate(case["args"]["x"]) if v is not None]      # NaN is outside the shader's contract
    got = gpu.run(m, np.array([case["args"]["x"][i] for i in keep]))
    problems = []
    for key in ("position", "light", "sdr_light", "colours"):
        want = [case["out"][key][i] for i in keep]
        tolerance = FLOAT32_COLOUR if key == "colours" else FLOAT32
        problems += golden.agree(got[key].astype(np.float64).tolist(), want, tolerance, f"{name}.{key}")
    assert not problems, problems[:5]


@pytest.mark.parametrize("name", held_cases())
def test_across_a_whole_image_the_shader_stays_within_tolerance_and_half_a_code(name):
    assert_within_tolerance(Mapping(**mapping_cases()[name]["args"]["mapping"]))


def test_no_golden_mapping_case_is_refused():
    refused = set()
    for name, case in mapping_cases().items():
        try:
            gpu.check(Mapping(**case["args"]["mapping"]))
        except ValueError:
            refused.add(name)
    assert refused == REFUSED_GOLDEN == set()
    assert "mapping_offset_fire" in held_cases()      # fire/1 at headroom 2: refused while colour was held to 2e-6


# ---- the shipped colour maps at the shipped headrooms, and a mapping an earlier rule let through wrongly

SHIPPED_STRETCHES = {"linear": {"kind": "linear", "lo": 273.15, "hi": 303.15},
                     "log": {"kind": "log", "lo": 1.0, "hi": 1e4},
                     "asinh": {"kind": "asinh", "lo": 0.0, "hi": 20.0, "width": 0.1}}


# every registered version: the steep cases further down are built on fire/1, whose clipped top (corrected in
# version 2) made one very steep step; it stays registered, so it must stay held
@pytest.mark.parametrize("cmap", ["fire", "ice", "fire/1", "ice/1"])
@pytest.mark.parametrize("headroom", [2.0, 4.0, 8.0, 16.0])
@pytest.mark.parametrize("kind", SHIPPED_STRETCHES)
def test_a_shipped_colour_map_at_a_shipped_headroom_is_held(kind, headroom, cmap):
    for position in (0.1, 0.5):
        assert_within_tolerance(at_position(SHIPPED_STRETCHES[kind], position, headroom, cmap))


def test_fire_at_headroom_2_is_held_by_the_colour_tolerance_and_would_not_be_by_the_lights():
    """The steep stretch near the top of fire/1 multiplies the light's error: within 1e-5, and beyond 2e-6."""
    m = at_position({"kind": "log", "lo": 1.0, "hi": 30.0}, 0.7, 2.0, "fire/1")
    found = errors(m, image(m))
    assert LIGHT / COLOUR < found["colours"] <= 1.0 and found["codes"] < 0.5, found


def test_a_mapping_found_out_of_tolerance_in_review_is_held():
    """asinh over a narrow range with a low knee and ice at headroom 2: accepted while colour was held to 2e-6, and
    measured at 1.3 to 1.55 times that. It is accepted still, and holds the colour tolerance."""
    stretch = {"kind": "asinh", "lo": 0.009051034380260723, "hi": 0.026729437747287844, "width": 2.0403241856084198,
               "offset": -0.012404294912130997}
    m = at_position(stretch, 0.15257950789001287, 2.0, "ice")
    assert max(gpu.expected_error(m).values()) <= 1.0
    for seed in (3, 5, 11):
        found = errors(m, image(m, seed))
        assert max(found["position"], found["light"], found["colours"]) <= 1.0 and found["codes"] < 0.5, found


# ---- ranges whose ends no float32 holds (the values given to the GPU are still float32, exactly)

AWKWARD = {
    "kelvin": lambda: at_position({"kind": "linear", "lo": 273.15, "hi": 303.15}, 0.37, 4.0, "ice"),
    "kelvin, one degree": lambda: at_position({"kind": "linear", "lo": 273.15, "hi": 274.15}, 0.37, 4.0, "ice"),
    "far from zero": lambda: at_position({"kind": "linear", "lo": 20000.1, "hi": 20001.1}, 0.4, 4.0, "ice"),
    "far from zero, grey": lambda: at_position({"kind": "linear", "lo": 100000.1, "hi": 100001.1}, 0.4, 8.0),
    "offset": lambda: at_position({"kind": "linear", "lo": 0.0, "hi": 1.0, "offset": 1000.1}, 0.5, 8.0, "ice"),
    "offset and range": lambda: at_position({"kind": "linear", "lo": 20000.1, "hi": 20001.1, "offset": 10.3}, 0.4, 4.0),
    "offset against range": lambda: at_position({"kind": "linear", "lo": -1000.0, "hi": -999.0, "offset": 1000.1}, 0.4,
                                                 4.0, "ice"),
    "proportional": lambda: Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 0.3 * 4.0}, 0.3, 4.0, "fire",
                                                 proportional=True),
    "log": lambda: at_position({"kind": "log", "lo": 0.37, "hi": 4130.3, "offset": 5.3}, 0.4, 4.0, "ice"),
    "asinh": lambda: at_position({"kind": "asinh", "lo": -2.3, "hi": 417.7, "width": 0.7, "offset": 0.1}, 0.4, 4.0, "ice"),
    "exit slope": lambda: at_position({"kind": "linear", "lo": 6562.3, "hi": 6563.3}, 0.3, 8.0, "ice", 2.0),
}


@pytest.mark.parametrize("name", AWKWARD)
def test_a_range_no_float32_holds_stays_within_tolerance_and_half_a_code(name):
    assert_within_tolerance(AWKWARD[name]())


# ---- what float32 cannot do is refused, and what is let through holds

NARROW_ASINH = {"kind": "asinh", "lo": 50.0, "hi": 140.0, "width": 65.0, "offset": -1.4}
LIMITS = {
    # name: (the mapping just inside the limit, the one just outside, what is out of tolerance outside)
    "knee near the top": (lambda: at_position({"kind": "linear", "lo": 273.15, "hi": 303.15}, 0.88, 4.0),
                          lambda: at_position({"kind": "linear", "lo": 273.15, "hi": 303.15}, 0.97, 4.0), "light"),
    "knee near the top, more headroom": (lambda: at_position({"kind": "linear", "lo": 273.15, "hi": 303.15}, 0.7, 16.0),
                                         lambda: at_position({"kind": "linear", "lo": 273.15, "hi": 303.15}, 0.9, 16.0),
                                         "light"),
    "knee near the bottom of a log range": (lambda: at_position({"kind": "log", "lo": 1.0, "hi": 1e4}, 0.02, 4.0),
                                            lambda: at_position({"kind": "log", "lo": 1.0, "hi": 1e4}, 0.003, 4.0),
                                            "light"),
    "narrow log range": (lambda: at_position({"kind": "log", "lo": 3.3, "hi": 6.6}, 0.5, 4.0),
                         lambda: at_position({"kind": "log", "lo": 3.3, "hi": 3.31}, 0.5, 4.0), "position"),
    "knee near the bottom of a narrow asinh range": (lambda: at_position(NARROW_ASINH, 0.2, 8.0, "grey", 0.5),
                                                     lambda: at_position(NARROW_ASINH, 0.04, 8.0, "grey", 0.5),
                                                     "light"),
    "headroom near 1 with a colour map": (lambda: at_position({"kind": "linear", "lo": 0.1, "hi": 1.3}, 0.5, 1.05, "fire/1"),
                                          lambda: at_position({"kind": "linear", "lo": 0.1, "hi": 1.3}, 0.5, 1.01, "fire/1"),
                                          "colours"),
    "a steep colour map under a steep curve": (
        lambda: at_position({"kind": "log", "lo": 1.0, "hi": 30.0}, 0.7, 2.0, "fire/1"),
        lambda: at_position({"kind": "log", "lo": 1.0, "hi": 30.0}, 0.8, 2.0, "fire/1", 3.0), "colours"),
}


@pytest.mark.parametrize("name", LIMITS)
def test_a_mapping_float32_cannot_hold_is_refused_and_one_inside_the_limit_holds(name):
    inside, outside, what = LIMITS[name]
    assert_within_tolerance(inside())
    m = outside()
    with pytest.raises(ValueError, match=f"cannot hold this mapping to its tolerance: {what} would be out"):
        gpu.run(m, image(m)[:64])
    with pytest.raises(ValueError, match="cannot hold this mapping"):
        gpu.uniform_block(m)
    assert gpu.expected_error(m)[what] > 1.0


@pytest.mark.parametrize("name", [n for n, (_, _, what) in LIMITS.items()])
def test_what_is_refused_really_is_out_of_tolerance(name):
    """The refusal is not caution for its own sake: run without the check, these are out, in the part named."""
    m, what = LIMITS[name][1](), LIMITS[name][2]
    found = unchecked_errors(m, image(m))
    assert found[what] > 1.0, found


# Mappings an earlier form of the rule let through, each measured out of tolerance: what the rule missed, and in what.
MISSED = {
    "the rounding of asinh's argument": (lambda: at_position(NARROW_ASINH, 0.055, 8.0, "grey", 0.5), "light"),
    "the rounding of the light where the curve is flat and the table steep": (
        lambda: at_position({"kind": "log", "lo": 1.0, "hi": 1e3}, 0.31, 1.0205, "fire/1"), "colours"),
}


@pytest.mark.parametrize("name", MISSED)
def test_what_an_earlier_rule_let_through_is_refused_and_really_is_out(name):
    m, what = MISSED[name][0](), MISSED[name][1]
    assert gpu.expected_error(m)[what] > 1.0
    with pytest.raises(ValueError, match=f"cannot hold this mapping to its tolerance: {what} would be out"):
        gpu.check(m)
    found = unchecked_errors(m, image(m))
    assert found[what] > 1.0, found


def test_the_slope_of_a_table_is_taken_from_its_nodes_and_not_averaged_away():
    """fire/1 at headroom 2 has one step, 17 nodes below its top, where blue climbs 19 units per unit of light.
    Lights several nodes apart must still find it."""
    table = Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 1.0}, 0.5, 2.0, "fire/1").table
    steepest = float(np.abs(np.diff(table.above.astype(np.float64), axis=0)).max() * (table.nodes - 1))
    assert 19.0 < steepest < 19.5 and int(np.abs(np.diff(table.above[:, 2])).argmax()) == table.nodes - 18
    for count in (50, 1000, 16_385, 100_001):
        light = np.linspace(0.0, 2.0, count)
        assert gpu._table_slope(table, light).max() == pytest.approx(steepest, rel=1e-12)
    sparse = np.linspace(0.0, 2.0, 1000)
    assert np.abs(np.gradient(table.lookup(sparse), sparse, axis=0)).max() < 0.6 * steepest     # what differences see
    far = gpu._table_slope(table, np.linspace(0.0, 1.9, 1000))                 # and it is not found where it is not
    assert far.max() < 0.6 * steepest


def test_a_refusal_names_the_tolerance_of_the_part_that_fails():
    with pytest.raises(ValueError, match=r"colours would be out by about \S+ times 1e-05 x"):
        gpu.check(LIMITS["headroom near 1 with a colour map"][1]())
    with pytest.raises(ValueError, match=r"light would be out by about \S+ times 2e-06 x"):
        gpu.check(LIMITS["knee near the top"][1]())


def test_headroom_1_and_a_grey_map_at_any_headroom_are_not_refused():
    for headroom, cmap in ((1.0, "fire"), (1.0, "grey"), (1.0001, "grey"), (1.05, "grey")):
        assert_within_tolerance(at_position({"kind": "linear", "lo": 0.1, "hi": 1.3}, 0.5, headroom, cmap))


@pytest.mark.parametrize(("lo", "hi"), [(0.0, 1e-39), (-1e39, 1.0), (0.0, 1e39), (1e-320, 2e-320)])
def test_a_mapping_whose_numbers_do_not_fit_float32_is_a_value_error(lo, hi):
    m = Mapping.from_stretch({"kind": "linear", "lo": lo, "hi": hi}, 0.5 * (lo + hi), 4.0)
    assert np.allclose(m.position(np.array([lo, hi])), [0.0, 1.0])            # the float64 reference takes it
    with pytest.raises(ValueError, match="does not fit a float32"):
        gpu.uniform_block(m)
    with pytest.raises(ValueError, match="does not fit a float32"):
        gpu.run(m, np.array([0.0]))


def test_a_log_range_wider_than_float32_is_a_value_error():
    m = Mapping.from_stretch({"kind": "log", "lo": 1e-30, "hi": 1e30}, 1.0, 4.0)
    with pytest.raises(ValueError, match="top of the range, as the stretch is given it"):
        gpu.uniform_block(m)


def test_the_shader_refuses_what_is_outside_its_contract():
    m = Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 1.0}, 0.5, 2.0)
    with pytest.raises(ValueError, match="NaN"):
        gpu.run(m, np.array([0.1, np.nan]))
    with pytest.raises(ValueError, match="values at a time"):
        gpu.run(m, np.array([]))
