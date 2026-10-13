"""The depth half: one 12-bit detector frame stored twice, and what each file can honestly call itself.

SYNTHETIC data: a 12-bit frame (0 to 4,095 counts) with a read noise of 3 counts.

The same frame and the same mapping are stored as an 8-bit gain-map JPEG and as a 16-bit PQ PNG. For each, the
widest run of data values that share one stored level is worked out and set against the noise (profile 7.2.1: the
rounding error, a step over the square root of 12, may be at most a tenth of the noise). The JPEG's levels are far
too coarse, so it is a rendering. The PNG's are fine enough, so it may be declared a data record: a reader can
take the counts back out of it to within the noise.

The package works out the levels of its 8-bit path (udeep.metadata.data_steps). The 16-bit PQ levels are worked
out here, in the same way, from the same functions the PNG writer uses.
"""
import dataclasses
import struct
import zlib
from pathlib import Path

import numpy as np
from _common import OUTPUT

from udeep import Declaration, Mapping, hdr_view, read_metadata, to_xmp, write_gainmap_jpeg, write_png_pq
from udeep.color import REF_WHITE_NITS, srgb_to_bt2020
from udeep.export import pq_encode
from udeep.jnd import pq_decode
from udeep.metadata import data_steps, description, digest_data, rounding_to_noise

FULL_SCALE, READ_NOISE, KNEE, HEADROOM = 4095.0, 3.0, 1000.0, 4.0
STRETCH = {"kind": "linear", "lo": 0.0, "hi": FULL_SCALE}
NOISE = ("constant", READ_NOISE)


def frame(size: int = 256) -> np.ndarray:
    rng = np.random.default_rng(20261016)
    y, x = np.mgrid[:size, :size] / size
    image = 200.0 + 500.0 * x + 250.0 * np.sin(6.0 * y) ** 2                # a smooth scene below the knee
    image += 3000.0 * np.exp(-0.5 * (((x - 0.7) ** 2 + (y - 0.35) ** 2) / 0.05 ** 2))   # and one bright feature above it
    return np.clip(np.round(image + rng.normal(0.0, READ_NOISE, image.shape)), 0.0, FULL_SCALE)


def pq16_steps(mapping: Mapping, samples: int = 1 << 20) -> tuple[np.ndarray, np.ndarray]:
    """The levels of a 16-bit PQ PNG across the declared range: where each run of data values that share one code
    starts and how wide it is, in data units. The code is the writer's own: the colour, to BT.2020, to PQ at the
    reference white, to 16 bits."""
    t = np.linspace(0.0, 1.0, samples)
    colour = mapping.colours(mapping.light_at(t))
    code = np.round(pq_encode(np.maximum(srgb_to_bt2020(colour), 0.0) * REF_WHITE_NITS) * 65535.0)
    edges = np.flatnonzero(np.any(code[1:] != code[:-1], axis=1)) + 1
    x = mapping.value_at(np.concatenate([[0.0], t[edges], [1.0]]))
    return x[:-1], np.diff(x)


def counts_from_png(path: Path, mapping: Mapping) -> np.ndarray:
    """The data, taken back out of the 16-bit PQ PNG: its codes, to light, to the value the mapping gives that light.
    The file is read here directly (16 bits a channel, rows unfiltered, as udeep.export writes them)."""
    raw = path.read_bytes()
    at, parts, size = 8, [], None
    while at < len(raw):
        length, tag = struct.unpack(">I4s", raw[at:at + 8])
        body = raw[at + 8:at + 8 + length]
        if tag == b"IHDR":
            size = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            parts.append(body)
        at += 12 + length
    width, height = size
    rows = np.frombuffer(zlib.decompress(b"".join(parts)), dtype=np.uint8).reshape(height, 1 + width * 6)
    assert not rows[:, 0].any(), "this reader takes unfiltered rows only"
    codes = rows[:, 1:].copy().view(">u2").reshape(height, width, 3)
    light = pq_decode(codes[..., 1] / 65535.0) / REF_WHITE_NITS        # a grey figure: any channel is the light
    return mapping.value(light)


def main(folder: Path = OUTPUT) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    data = frame()
    view = hdr_view(data, "extend", KNEE, STRETCH, HEADROOM, "grey")
    mapping = view.mapping
    common = {**dataclasses.asdict(mapping), "data_unit": "counts", "knee_rule": "preset; white at 1,000 counts",
              "noise_model": NOISE[0], "noise_parameter": NOISE[1], "preset": "extend", "data_digest": digest_data(data)}
    out = {}
    for name, steps in (("jpeg", data_steps(mapping, scaled_by=HEADROOM)), ("png", pq16_steps(mapping))):
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
    back = counts_from_png(out["png"]["path"], mapping)
    out["png"]["recovered_error"] = float(np.abs(back - data).max())
    out["png"]["recovered_exactly"] = bool(np.array_equal(np.round(back), data))
    print(f"read back from the PNG: every count within {out['png']['recovered_error']:.3g} of the frame; "
          f"rounded to whole counts, {'the frame itself' if out['png']['recovered_exactly'] else 'NOT the frame'}")
    return out


if __name__ == "__main__":
    main()
