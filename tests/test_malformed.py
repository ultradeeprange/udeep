"""Files come from anyone (design decision D24). A reader may refuse a damaged file with ValueError; the validator
reports it as a failed check and never raises; and nothing takes the interpreter down.

Not a fuzzer: a few thousand damaged copies of valid files, from a fixed stream of random numbers, in a few
seconds. core/tests/malformed.rs does the same for the Rust readers directly.
"""
import numpy as np
import pytest

from udeep import core, read_gainmap_jpeg, read_metadata, read_png_pq, write_gainmap_jpeg, write_png_pq
from udeep.validate import validate


@pytest.fixture(autouse=True)
def core_back_on():
    """Each test may turn the core off: put back what was there before it, which is no core at all
    where none was built."""
    was = core.enabled()
    yield
    core.enable(was)


def damaged(data: bytes, rng) -> bytes:
    out = bytearray(data)
    kind = rng.integers(7)
    at = int(rng.integers(len(out)))
    if kind == 0:
        for _ in range(1 + int(rng.integers(4))):
            out[int(rng.integers(len(out)))] ^= 1 << int(rng.integers(8))
    elif kind == 1:
        run = 1 + int(rng.integers(32))
        out[at:at + run] = rng.integers(0, 256, len(out[at:at + run]), dtype=np.uint8).tobytes()
    elif kind == 2:
        del out[at:]
    elif kind == 3:
        to = int(rng.integers(len(out)))
        out[to:to] = out[at:at + 1 + int(rng.integers(64))]
    elif kind == 4:
        del out[at:at + 1 + int(rng.integers(64))]
    elif kind == 5:
        value = [0xFFFFFFFF, 0x7FFFFFFF, 0x10000, 0, 1 << int(rng.integers(31))][int(rng.integers(5))]
        out[at:at + 4] = value.to_bytes(4, "big")
    else:
        out[at:at + 2] = [0xFFFF, 0, 1, 2, 3, int(rng.integers(65536))][int(rng.integers(6))].to_bytes(2, "big")
    return bytes(out)


def seeds(tmp_path):
    from udeep import Declaration, to_xmp
    rng = np.random.default_rng(3)
    declared = Declaration(data_unit="counts", stretch="linear", range_low=0.0, range_high=100.0, knee=40.0,
                           knee_rule="preset; a test", reference_headroom=4.0, max_data_step=0.01, colour_map="grey")
    png = tmp_path / "seed.png"
    write_png_pq(rng.uniform(0.0, 4.0, (9, 11, 3)), png, xmp=to_xmp(declared))
    sdr = rng.uniform(0.0, 1.0, (16, 24, 3))
    hdr = np.where(rng.uniform(size=(16, 24, 1)) > 0.7, sdr * 3.5, sdr)
    jpeg = tmp_path / "seed.jpg"
    write_gainmap_jpeg(sdr, hdr, jpeg, 4.0)
    return png.read_bytes(), jpeg.read_bytes()


@pytest.mark.parametrize("with_core", [True, False], ids=["core", "reference"])
def test_damaged_files_are_refused_cleanly_and_the_validator_never_raises(with_core, tmp_path):
    if with_core and not (core.available() and core.png() and core.files()):
        pytest.skip("the core was not built with its image features")
    png, jpeg = seeds(tmp_path)
    core.enable(with_core)
    rng = np.random.default_rng(20261014)
    rounds = 1500 if with_core else 300                       # the reference readers are slower
    outcome = {"refused": 0, "read": 0}
    path = tmp_path / "damaged"
    for i in range(rounds):
        seed, reader, args = ((png, read_png_pq, ()), (jpeg, read_gainmap_jpeg, (4.0,)))[i % 2]
        path.write_bytes(damaged(seed, rng))
        try:
            image = reader(path, *args)
            assert image.ndim == 3 and image.shape[2] == 3
            outcome["read"] += 1
        except ValueError:
            outcome["refused"] += 1
        if i % 50 in (0, 25):                                 # one damaged PNG and one damaged JPEG in fifty: it
            report = validate(path)                           # works a file's levels out, which takes a second.
            assert report.checks, "a report always says something"      # It never raises, whatever the file holds
        try:
            read_metadata(path)                               # None, a declaration, or a refusal
        except ValueError:
            pass
    assert outcome["refused"] > rounds // 4 and outcome["read"] > 0, outcome


def test_a_png_that_claims_a_vast_image_is_refused_before_room_is_made_for_it(tmp_path):
    import zlib
    png, _ = seeds(tmp_path)
    data = bytearray(png)
    data[16:24] = (60000).to_bytes(4, "big") * 2              # 60000 x 60000: 86 GB as float64, if believed
    data[29:33] = zlib.crc32(bytes(data[12:29])).to_bytes(4, "big")
    (tmp_path / "vast.png").write_bytes(bytes(data))
    for on in (True, False):
        if on and not (core.available() and core.png()):
            continue
        core.enable(on)
        with pytest.raises(ValueError):
            read_png_pq(tmp_path / "vast.png")
        assert not validate(tmp_path / "vast.png").ok
