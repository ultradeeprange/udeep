"""The worked examples (examples/): each one runs, writes a file that conforms to the profile, and shows what its
text says it shows."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from udeep import read_metadata

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FIGURES = ["spectral_map", "microscope_frame", "simulation_field"]


def load(name: str):
    sys.path.insert(0, str(EXAMPLES))                     # the examples import their shared helper by name
    try:
        spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(EXAMPLES))
    return module


def common():
    return load("_common")


@pytest.mark.filterwarnings("ignore::udeep.mpl.PrecisionWarning")
@pytest.mark.parametrize("name", FIGURES)
def test_a_figure_example_writes_a_conforming_file_for_both_themes(name, tmp_path):
    result = common().save(load(name).build, name, tmp_path)
    assert result["report"].ok, str(result["report"])
    light, dark = read_metadata(tmp_path / f"{name}_hdr.jpg"), read_metadata(tmp_path / f"{name}_dark_hdr.jpg")
    assert light is not None and dark is not None and light.record == "rendering"
    assert light.data_digest == dark.data_digest and light.mapping == dark.mapping     # one figure, two themes
    assert (tmp_path / f"{name}_sdr.png").stat().st_size > 10_000


def test_the_spectral_knee_is_the_poisson_tail_it_is_said_to_be():
    example = load("spectral_map")
    knee = example.poisson_threshold(example.BACKGROUND, example.FALSE_ALARM)
    import math
    tail = lambda k: 1.0 - sum(math.exp(-20.0) * 20.0 ** i / math.factorial(i) for i in range(k))   # P(X >= k)
    assert tail(knee) <= example.FALSE_ALARM < tail(knee - 1)
    data = example.counts()
    assert data.shape == (300, 400) and 0.0 < (data > knee).mean() < 0.1


def test_the_saturated_pixels_of_the_microscope_frame_are_held_and_counted(tmp_path):
    example = load("microscope_frame")
    data = example.frame()
    saturated = int((data >= example.FULL_SCALE).sum())
    assert saturated > 50 and data.max() == example.FULL_SCALE and (data == np.round(data)).all()
    with pytest.warns(Warning):
        common().save(example.build, "microscope_frame", tmp_path)
    assert read_metadata(tmp_path / "microscope_frame_hdr.jpg").masked == saturated


def test_the_detector_frame_is_a_rendering_as_a_jpeg_and_a_data_record_as_a_16_bit_png(tmp_path):
    out = load("detector_record").main(tmp_path)
    jpeg, png = out["jpeg"], out["png"]
    assert jpeg["record"] == "rendering" and jpeg["ratio"] > 1.0 and jpeg["max_step"] > 10.0
    assert png["record"] == "data-record" and png["ratio"] <= 0.1 and png["max_step"] < 1.0 and png["levels"] > 30_000
    assert png["recovered_exactly"] and png["recovered_error"] <= png["max_step"] / 2 + 1e-9   # the counts come back
    assert png["report"].ok and png["report"].status("precision") == "pass" and png["report"].status("data read back") == "pass"
    declared = read_metadata(png["path"])
    assert (declared.noise_model, declared.noise_parameter, declared.data_unit) == ("constant", 3.0, "counts")
    # one figure in two files: the PNG names the JPEG's base as its SDR figure, and the check confirms it
    companion = read_metadata(jpeg["path"])
    assert declared.sdr_digest == companion.sdr_digest and declared.data_digest == companion.data_digest
    assert png["report"].status("sdr link") == "pass" and "compared by the SdrDigest it declares" in str(png["report"])
