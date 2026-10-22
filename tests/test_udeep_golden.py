"""golden.json is the contract every implementation must reproduce (design decision D14); Python must match it too."""
import copy
import json

from udeep import golden

KINDS = {"scale", "tone", "pq_encode", "srgb_oetf", "cmap", "table", "mapping", "levels", "levels_pq16", "mask_isolated", "digest_data",
         "digest_pixels", "digest_data_sha3", "digest_pixels_sha3", "xmp", "caption", "iso21496"}
COUNTS = {"scale": 3, "tone": 11, "pq_encode": 1, "srgb_oetf": 1, "cmap": 2, "table": 24, "mapping": 8, "levels": 32, "levels_pq16": 8,
          "mask_isolated": 1, "digest_data": 1, "digest_pixels": 2, "digest_data_sha3": 1, "digest_pixels_sha3": 2, "xmp": 2,
          "caption": 1, "iso21496": 1}


def refuse(constant: str):
    raise ValueError(f"{constant} is not JSON: another language could not read this file")


def committed() -> dict:
    return json.loads(golden.PATH.read_text(encoding="utf-8"), parse_constant=refuse)


def cases_of(kind: str) -> dict:
    return {name: c for name, c in committed()["cases"].items() if c["kind"] == kind}


def test_the_contract_has_the_cases_it_is_said_to_have():
    cases = committed()["cases"]
    assert {kind: len(cases_of(kind)) for kind in KINDS} == COUNTS and len(cases) == sum(COUNTS.values()) == 101


def test_the_data_criterion_is_pinned_for_every_noise_model():
    levels = cases_of("levels")
    by_model = {model: [c for c in levels.values() if c["args"]["noise"][0] == model]
                for model in ("constant", "poisson", "relative", "tolerance")}
    assert sum(len(v) for v in by_model.values()) == len(levels) and all(len(v) == 8 for v in by_model.values())
    for model, limit in (("constant", 0.1), ("poisson", 0.1), ("relative", 0.1), ("tolerance", 1.0)):
        outs = [c["out"] for c in by_model[model]]
        assert all(o["limit"] == limit for o in outs) and {o["meets"] for o in outs} == {True, False}, model
        assert all(o["meets"] == (o["rounding_to_noise"] <= limit) == (o["failing_fraction"] == 0.0) for o in outs)
    for model in ("poisson", "relative"):
        assert all(c["args"]["noise"][1] != 1.0 for c in by_model[model])       # 1 hides multiplying for dividing
        outs = [c["out"] for c in by_model[model]]
        assert any(o["rounding_to_noise"] == golden.UNBOUNDED and o["at"] == 0.0 for o in outs)    # a range from zero
        assert any(o["rounding_to_noise"] < 1.0 and o.get("at", 0.0) > 0.0 for o in outs)          # and one above it
    assert not any("at" in c["out"] for model in ("constant", "tolerance") for c in by_model[model])
    assert levels["levels_linear_grey_poisson"]["out"]["at"] < 0.0                # no counts below zero


def test_exit_slopes_other_than_the_reference_are_pinned():
    declared = {c["args"]["mapping"]["above_knee"] for kind in ("mapping", "levels") for c in cases_of(kind).values()}
    assert declared == {"hermite; exit=1", "hermite; exit=0.5", "hermite; exit=2"}
    assert {c["args"].get("exit_slope", 1.0) for c in cases_of("tone").values()} == {0.0, 0.5, 1.0, 2.0}
    tone = committed()["cases"]
    half, two = tone["shoulder_0.8_4.0_exit_0.5"]["out"], tone["shoulder_0.8_4.0_exit_2.0"]["out"]
    assert half != two and half[-1] == two[-1] == 4.0                             # other curves to the same top


def test_the_pixel_digest_is_pinned_on_an_image_that_is_not_square():
    shapes = [c["args"]["shape"] for c in cases_of("digest_pixels").values()]
    assert [2, 2, 3] in shapes and any(s[0] != s[1] for s in shapes)


def test_the_serialised_declarations_read_back_as_they_were_given():
    from udeep.metadata import Declaration, description, parse, to_xmp

    for name, case in cases_of("xmp").items():
        d = Declaration(**case["args"]["declaration"])
        assert description(d).decode("utf-8") == case["out"] and parse(to_xmp(d)) == d, name
    out = committed()["cases"]["xmp_description_floats"]["out"]
    for text in ('RangeHigh="1700000002.3456"', 'Knee="1700000001.23456"', 'MaxDataStep="1e-05"', 'NoiseParameter="1e+16"',
                 'StretchWidth="1.5e-07"', 'StretchOffset="-0.3333333333333333"', 'RoundingToNoise="0.1"',
                 'ReferenceHeadroom="4"', 'RangeLow="1700000000"'):
        assert f"udeep:{text}" in out, text
    assert "to 16383 DN" in committed()["cases"]["caption"]["out"]                # six figures: the declared range top


def test_python_matches_committed_golden():
    want, fresh = committed(), json.loads(json.dumps(golden.make()))        # through JSON, as a port would read it
    assert (want["format"], want["profile"]) == (golden.FORMAT, "0.3")
    assert want["cases"].keys() == fresh["cases"].keys()
    problems = [m for name, case in want["cases"].items()
                for m in golden.agree(fresh["cases"][name]["out"], case["out"], case, name)]
    assert not problems, problems[:5]


def test_every_case_states_its_kind_and_how_closely_it_must_agree():
    cases = committed()["cases"]
    assert {c["kind"] for c in cases.values()} == KINDS
    for name, c in cases.items():
        assert ("tol" in c) != bool(c.get("exact")), name                   # one or the other, never neither
        if "tol" in c:
            assert 0.0 < c["tol"]["rel"] <= 2e-6 and 0.0 < c["tol"]["abs"] <= 2e-6, name


def test_the_comparison_is_not_vacuous():
    cases = committed()["cases"]
    for name in ("mapping_log_ice", "tone_0.8_4.0", "levels_linear_grey"):
        case = cases[name]
        wrong = copy.deepcopy(case["out"])
        if isinstance(wrong, dict):
            key = next(k for k, v in wrong.items() if isinstance(v, (list, float)))
            wrong[key] = [v * 1.0001 if isinstance(v, float) else v for v in wrong[key]] if isinstance(wrong[key], list) \
                else wrong[key] * 1.0001
        else:
            wrong = [v * 1.0001 if v else v for v in wrong]
        assert golden.agree(wrong, case["out"], case, name), name            # a 0.01 % change must be caught
    exact = cases["digest_data"]
    assert golden.agree(exact["out"][:-1] + "0", exact["out"], exact, "digest_data") or exact["out"].endswith("0")
    assert golden.agree(None, 1.0, cases["pq_encode"], "nan")                # a NaN where a number is expected


def test_the_sixteen_bit_cases_are_grey_and_hold_the_levels_not_the_samples():
    """Grey, so that the PQ path is pinned without a float32 table; and sampled finer than the levels, so that the
    count is the file's levels and not the number of samples."""
    for name, c in cases_of("levels_pq16").items():
        a, out = c["args"], c["out"]
        assert a["mapping"]["colour_map"] == "grey" and a["white_nits"] == golden.WHITE_NITS == 203.0, name
        assert 10_000 < out["count"] < a["samples"] / 4, name
    eight, sixteen = cases_of("levels")["levels_linear_grey"]["out"], cases_of("levels_pq16")["levels_pq16_linear_grey"]["out"]
    assert sixteen["count"] > 50 * eight["count"] and sixteen["max_data_step"] < eight["max_data_step"] / 50
