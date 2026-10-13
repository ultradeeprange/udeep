"""A figure drawn and saved from Julia (Makie, through the Rust core) must be a file the Python reference accepts.

The example `julia/UDeepCore/examples/figure.jl` writes a gain-map JPEG, the SDR figure of record and a caption.
Here the reference validator reads the file, with the data rebuilt independently in Python. Skipped when Julia or
cargo is not installed, or the example's environment has not been set up (`julia --project=. -e 'using Pkg;
Pkg.instantiate()'` in that directory; it needs CairoMakie).
"""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from udeep.gainmap import read_gainmap_jpeg
from udeep.metadata import caption, digest_data, digest_pixels, read_metadata
from udeep.validate import validate

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "julia" / "UDeepCore" / "examples"
JULIA, CARGO = shutil.which("julia"), shutil.which("cargo")

pytestmark = pytest.mark.skipif(JULIA is None or CARGO is None or not (EXAMPLE / "Manifest.toml").exists(),
                                reason="needs julia, cargo, and the example environment instantiated")


def pattern() -> np.ndarray:
    """The example's test pattern, made here from its definition, not read from Julia: 96 along x, 72 along y."""
    i, j = np.meshgrid(np.arange(1, 97), np.arange(1, 73), indexing="ij")
    data = 20.0 + 25.0 * ((7 * i + 13 * j) % 10) / 10.0
    bi, bj = np.meshgrid(np.arange(30, 70), np.arange(20, 50), indexing="ij")
    data[29:69, 19:49] = 55.0 + (bi - 30) * 0.9 + (bj - 20) / 4.0
    for a, b in ((8, 8), (88, 12), (12, 64), (84, 60)):
        data[a - 1, b - 1] = 97.0
    return data


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    built = subprocess.run([CARGO, "build", "--release", "--features", "jpeg,png"], cwd=ROOT / "core", capture_output=True,
                           text=True, check=False)
    assert built.returncode == 0, built.stderr[-2000:]
    out = tmp_path_factory.mktemp("julia")
    ran = subprocess.run([JULIA, "--project=.", "figure.jl", str(out)], cwd=EXAMPLE, capture_output=True, text=True,
                         check=False, encoding="utf-8")
    assert ran.returncode == 0, ran.stderr[-3000:]
    return out


def test_the_reference_validator_accepts_the_julia_figure(written):
    report = validate(written / "figure.jpg", data=pattern(), sdr=written / "figure.png")
    assert report.ok, str(report)
    assert {c.name: c.status for c in report.checks if c.status != "skipped"} == {
        "metadata": "pass", "mapping": "pass", "headroom": "pass", "precision": "pass", "data link": "pass",
        "sdr link": "pass"}
    assert not validate(written / "figure.jpg", data=pattern() + 1e-9).ok          # and it would notice other data


def test_the_julia_figure_declares_what_was_drawn(written):
    d = read_metadata(written / "figure.jpg")
    assert (d.stretch, d.range_low, d.range_high, d.knee, d.reference_headroom) == ("linear", 0.0, 100.0, 50.0, 4.0)
    assert (d.data_unit, d.data_unit_code, d.preset, d.colour_map, d.record) == ("K", "K", "highlight", "fire/3", "rendering")
    assert (d.noise_model, d.noise_parameter) == ("constant", 2.5)
    assert (d.derived_mask, d.derived_masked, d.masked) == ("isolated-3x3", 4, 0)      # the four single pixels
    assert d.data_digest == digest_data(pattern())
    png = np.asarray(Image.open(written / "figure.png").convert("RGB"))
    assert d.sdr_digest == digest_pixels(png)                                       # Makie's own PNG of the figure
    # the numbers Julia got from the Rust core are the reference's own
    from udeep import Mapping
    from udeep.metadata import max_data_step, rounding_to_noise
    m = Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 100.0}, 50.0, 4.0, "fire")
    assert d.max_data_step == pytest.approx(max_data_step(m), rel=1e-9)
    assert d.rounding_to_noise == pytest.approx(rounding_to_noise(m, ("constant", 2.5))["worst"], rel=1e-9)
    assert (written / "caption.txt").read_text(encoding="utf-8") == caption(d)


def test_the_julia_figure_lifts_the_images_and_nothing_else(written):
    png = np.asarray(Image.open(written / "figure.png").convert("RGB"))
    sdr, hdr = read_gainmap_jpeg(written / "figure.jpg", 1.0), read_gainmap_jpeg(written / "figure.jpg", 4.0)
    assert sdr.shape == hdr.shape == png.shape
    above = hdr.max(axis=-1) > 1.25                                                 # clearly above white, past JPEG error
    assert 0.08 < above.mean() < 0.2
    white_page = np.all(png == 255, axis=-1)                                        # the margins of the figure
    assert white_page.mean() > 0.3 and not np.any(above & white_page)
    ink = np.all(png < 60, axis=-1)                                                 # labels, ticks, frame, the text
    assert ink.sum() > 2000 and not np.any(above & ink)
    assert sdr.max() <= 1.0 + 1e-6 and 3.5 < hdr.max() < 4.0 * 1.05
