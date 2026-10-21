"""Verification of a data record read back as data (values_pq16, and the Rust core's udeep_values_pq16).

The claim under test: each pixel comes back as the middle of the run of data values that share its codes, so to
within half that run's width, whatever the stretch, the colour map or where in the range the value lies.

The oracle does not use the reader's method. The reader samples positions evenly, refines the gaps that skip a
code, and finds where each code changes; the oracle starts from each data value and walks out from it in data
space, doubling its step until the codes change and then halving back to the change, on both sides. The two
agree only if the reader found every run, and found it at the right place.

Controls: with the refinement switched off (REFINE_STEPS = 0, the reader before 21 October 2026) the same check
must fail near black, where the levels are narrower than the sampling (the positive control), and must still
pass in the middle of the range, where they are not (the null control). Without these the check could not be
told apart from one that cannot fail. The Rust core is held to the same oracle in tests/test_udeep_rust.py.
"""
import dataclasses

import numpy as np
import pytest

from udeep import Declaration, Mapping, core, metadata, to_xmp, write_png_pq, write_png_sdr
from udeep.metadata import data_steps_pq16, digest_data, digest_sdr, pq16_codes, rounding_to_noise, values_pq16

LINEAR = {"kind": "linear", "lo": 0.0, "hi": 4095.0}
MAPPINGS = {                                                       # name: (stretch, knee, proportional)
    "linear": (LINEAR, 1000.0, False),
    "linear, knee at the middle": (LINEAR, 2048.0, False),
    "log": ({"kind": "log", "lo": 1.0, "hi": 1e5}, 1e3, False),
    "asinh": ({"kind": "asinh", "lo": -50.0, "hi": 5000.0, "width": 10.0}, 500.0, False),
    "proportional": ({"kind": "linear", "lo": 0.0, "hi": 400.0}, 100.0, True),
}
CASES = [(name, cmap, 4.0) for name in MAPPINGS for cmap in ("grey", "fire", "ice")] + \
        [("linear", "fire", h) for h in (2.0, 3.0, 8.0, 16.0)] + [("linear", "ice", 3.0), ("log", "grey", 16.0)]


@pytest.fixture(autouse=True)
def reference_only():
    """The reference's own arithmetic, whatever core this machine has."""
    was = core.enabled()
    core.enable(False)
    yield
    core.enable(was)


def mapping(name: str, cmap: str, headroom: float) -> Mapping:
    stretch, knee, proportional = MAPPINGS[name]
    if proportional:
        stretch = {**stretch, "hi": knee * headroom}
    return Mapping.from_stretch(stretch, knee, headroom, cmap, proportional=proportional)


def values_to_check(m: Mapping) -> np.ndarray:
    """Near the bottom of the range (down to 1e-14 of it), round the knee, near the top, at random, and at and
    past both ends."""
    lo, hi, span = m.range_low, m.range_high, m.range_high - m.range_low
    near = lambda a, b, n: 10.0 ** np.linspace(a, b, n)
    bottom = lo * (1.0 + near(-14, -1, 400)) if m.stretch == "log" else lo + span * near(-14, -2, 400)
    knee = m.knee + span * np.concatenate([near(-12, -3, 60), -near(-12, -3, 60)])
    top = hi - span * near(-12, -2, 100)
    rng = np.random.default_rng(17)
    return np.concatenate([bottom, knee, top, rng.uniform(lo, hi, 600), [lo, hi, lo - 0.1 * span, hi + 0.1 * span]])


def runs_around(m: Mapping, x: np.ndarray, white: float = 203.0) -> tuple[np.ndarray, np.ndarray]:
    """For each value, the two ends of the run of data values around it that share its 16-bit codes, found by
    walking out from it in data space. A value outside the range is held at the range's end, and so is it here."""
    lo, hi = m.range_low, m.range_high
    x = np.clip(np.asarray(x, dtype=np.float64), lo, hi)
    code = lambda v: pq16_codes(m.colours(m.light(v)), white)
    own = code(x)
    same = lambda v: np.all(code(v) == own, axis=-1)
    ends = []
    for sign, bound in ((-1.0, lo), (1.0, hi)):
        inside, outside = x.copy(), np.full_like(x, np.nan)
        step = np.full_like(x, (hi - lo) * 2.0 ** -60)
        open_ = np.ones(x.shape, bool)                       # still walking out
        for _ in range(90):
            trial = np.clip(inside + sign * step, lo, hi) if sign < 0 else np.minimum(inside + sign * step, hi)
            trial = np.maximum(trial, lo)
            keep = same(trial)
            at_bound = trial == bound
            outside = np.where(open_ & ~keep, trial, outside)
            inside = np.where(open_ & keep, trial, inside)
            open_ &= keep & ~at_bound
            step *= 2.0
            if not open_.any():
                break
        assert not open_.any()
        bracketed = ~np.isnan(outside)                       # the others ran into the range's end, inside the run
        for _ in range(80):
            mid = 0.5 * (inside + np.where(bracketed, outside, inside))
            keep = same(mid)
            inside = np.where(bracketed & keep, mid, inside)
            outside = np.where(bracketed & ~keep, mid, outside)
        ends.append(np.where(bracketed, 0.5 * (inside + outside), bound))
    return ends[0], ends[1]


def misread(m: Mapping, x: np.ndarray) -> np.ndarray:
    """Which values the reader does not give as the middle of their own run."""
    start, end = runs_around(m, x)
    light = m.colours(m.light(x))
    back = values_pq16(m, light)
    width = end - start
    span = m.range_high - m.range_low
    wrong = np.isnan(back) | (np.abs(back - 0.5 * (start + end)) > 1e-5 * width + 1e-10 * span)
    return wrong


@pytest.mark.parametrize(("name", "cmap", "headroom"), CASES)
def test_every_value_comes_back_as_the_middle_of_its_own_level(name, cmap, headroom):
    m = mapping(name, cmap, headroom)
    x = values_to_check(m)
    wrong = misread(m, x)
    assert not wrong.any(), (f"{wrong.sum()} of {x.size} values misread, for example "
                             f"{np.sort(x[wrong])[:8]}")


def test_the_check_fails_near_black_without_the_refinement_and_passes_elsewhere(monkeypatch):
    """The positive control: the reader as it was before 21 October 2026, with no refinement, misreads values just
    above the bottom of the range. The null control: the same reader, in the middle of the range, does not."""
    monkeypatch.setattr(metadata, "REFINE_STEPS", 0)
    for cmap in ("grey", "fire"):
        m = mapping("linear", cmap, 4.0)
        bottom = 4095.0 * 10.0 ** np.linspace(-6, -3, 200)              # 0.004 to 4 counts
        middle = np.random.default_rng(2).uniform(1000.0, 3000.0, 400)
        assert misread(m, bottom).sum() > 50, cmap
        assert not misread(m, middle).any(), cmap


def faint_record(tmp_path, noise: float = 3.0):
    """A true data record of faint data: counts from 0 to 3 under a read noise of 3, with a brighter block, as a
    16-bit PQ PNG with its declaration, and the SDR figure it names."""
    from udeep.view import hdr_view
    rng = np.random.default_rng(23)
    data = rng.uniform(0.0, 3.0, (30, 40))
    data[10:20, 10:30] = rng.uniform(0.0, 4095.0, (10, 20))
    view = hdr_view(data, "extend", 1000.0, LINEAR, 4.0, "fire")
    steps = data_steps_pq16(view.mapping)
    against = rounding_to_noise(view.mapping, ("constant", noise), steps=steps)
    assert against["meets"]
    d = Declaration(**dataclasses.asdict(view.mapping), data_unit="counts", knee_rule="preset; white at 1,000 counts",
                    noise_model="constant", noise_parameter=noise, rounding_to_noise=against["worst"],
                    max_data_step=float(steps[1].max()), record="data-record", data_digest=digest_data(data),
                    sdr_digest=digest_sdr(view.sdr))
    write_png_pq(view.hdr, tmp_path / "faint.png", xmp=to_xmp(d))
    write_png_sdr(view.sdr, tmp_path / "faint_sdr.png")
    return tmp_path / "faint.png", data


def test_a_faint_data_record_failed_the_validator_before_the_fix_and_passes_now(tmp_path, monkeypatch):
    from udeep.validate import validate
    path, data = faint_record(tmp_path)
    report = validate(path, data=data, sdr=tmp_path / "faint_sdr.png")
    assert report.ok and report.status("data read back") == "pass", str(report)
    monkeypatch.setattr(metadata, "REFINE_STEPS", 0)
    before = validate(path, data=data, sdr=tmp_path / "faint_sdr.png")
    assert before.status("data read back") == "fail", str(before)

