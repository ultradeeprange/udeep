"""The depth half: one 12-bit detector frame stored twice, and what each file can honestly call itself.

SYNTHETIC data: a 12-bit frame (0 to 4,095 counts) with a read noise of 3 counts.

The same frame and the same mapping are stored as an 8-bit gain-map JPEG and as a 16-bit PQ PNG. For each, the
widest run of data values that share one stored level is worked out and set against the noise (profile 7.2.1: the
rounding error, a step over the square root of 12, may be at most a tenth of the noise). The JPEG's levels are far
too coarse, so it is a rendering. The PNG's are fine enough, so it may be declared a data record: a reader can
take the counts back out of it to within the noise.

The levels are worked out by the package (udeep.metadata.data_steps for the 8-bit path, data_steps_pq16 for the
16-bit one), and the PNG is read back by it (udeep.read_png_pq).
"""
import dataclasses
from pathlib import Path

import numpy as np
from _common import OUTPUT

from udeep import Declaration, hdr_view, read_metadata, read_png_pq, to_xmp, write_gainmap_jpeg, write_png_pq
from udeep.metadata import data_steps, data_steps_pq16, description, digest_data, rounding_to_noise
from udeep.validate import validate

FULL_SCALE, READ_NOISE, KNEE, HEADROOM = 4095.0, 3.0, 1000.0, 4.0
STRETCH = {"kind": "linear", "lo": 0.0, "hi": FULL_SCALE}
NOISE = ("constant", READ_NOISE)


def frame(size: int = 256) -> np.ndarray:
    rng = np.random.default_rng(20261016)
    y, x = np.mgrid[:size, :size] / size
    image = 200.0 + 500.0 * x + 250.0 * np.sin(6.0 * y) ** 2                # a smooth scene below the knee
    image += 3000.0 * np.exp(-0.5 * (((x - 0.7) ** 2 + (y - 0.35) ** 2) / 0.05 ** 2))   # and one bright feature above it
    return np.clip(np.round(image + rng.normal(0.0, READ_NOISE, image.shape)), 0.0, FULL_SCALE)


def main(folder: Path = OUTPUT) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    data = frame()
    view = hdr_view(data, "extend", KNEE, STRETCH, HEADROOM, "grey")
    mapping = view.mapping
    common = {**dataclasses.asdict(mapping), "data_unit": "counts", "knee_rule": "preset; white at 1,000 counts",
              "noise_model": NOISE[0], "noise_parameter": NOISE[1], "preset": "extend", "data_digest": digest_data(data)}
    out = {}
    for name, steps in (("jpeg", data_steps(mapping, scaled_by=HEADROOM)), ("png", data_steps_pq16(mapping))):
        against = rounding_to_noise(mapping, NOISE, steps=steps)
        declared = Declaration(**common, max_data_step=float(steps[1].max()), rounding_to_noise=against["worst"],
                               record="data-record" if against["meets"] else "rendering")
        if name == "jpeg":
            path = folder / "detector_record_hdr.jpg"
            write_gainmap_jpeg(view.sdr, view.hdr, path, HEADROOM, description=description(declared))
        else:
            path = folder / "detector_record_pq16.png"
            write_png_pq(view.hdr, path, xmp=to_xmp(declared))
        read = read_metadata(path)                               # what a reader finds in the file itself
        out[name] = {"path": path, "levels": len(steps[1]), "max_step": read.max_data_step,
                     "ratio": read.rounding_to_noise, "record": read.record, "meets": against["meets"]}
        print(f"{path.name}: {out[name]['levels']:,} levels across the range; widest step {read.max_data_step:.3g} counts; "
              f"rounding is {read.rounding_to_noise:.3g} of the noise (limit 0.1); the file declares itself a {read.record}")
    back = mapping.value(read_png_pq(out["png"]["path"])[..., 1])       # a grey figure: any channel is the light
    out["png"]["recovered_error"] = float(np.abs(back - data).max())
    out["png"]["recovered_exactly"] = bool(np.array_equal(np.round(back), data))
    print(f"read back from the PNG: every count within {out['png']['recovered_error']:.3g} of the frame; "
          f"rounded to whole counts, {'the frame itself' if out['png']['recovered_exactly'] else 'NOT the frame'}")
    report = validate(out["png"]["path"], data=data)           # the claim, checked from the file and the data
    out["png"]["report"] = report
    print(report)
    return out


if __name__ == "__main__":
    main()
