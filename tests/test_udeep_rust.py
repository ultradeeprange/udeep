"""The Rust core, called through its C interface from Python (ctypes), must agree with the golden vectors.

This exercises the interface a host application would use: the `udeep_mapping` record laid out as the header
says, buffers in and out, error codes back. Skipped when cargo is not installed.
"""
import ctypes
import dataclasses
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from udeep import golden

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
CARGO = shutil.which("cargo")
STRETCH = {"linear": 0, "log": 1, "asinh": 2, "proportional": 3}

pytestmark = pytest.mark.skipif(CARGO is None, reason="cargo not installed")


class CMapping(ctypes.Structure):
    """udeep_mapping, field for field as in core/include/udeep_core.h."""
    _fields_ = [("range_low", ctypes.c_double), ("range_high", ctypes.c_double), ("knee", ctypes.c_double),
                ("reference_headroom", ctypes.c_double), ("stretch_width", ctypes.c_double),
                ("stretch_offset", ctypes.c_double), ("exit_slope", ctypes.c_double), ("stretch", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32)]


class CText(ctypes.Structure):
    """udeep_text: bytes of UTF-8, or a null pointer for a property that is absent."""
    _fields_ = [("data", ctypes.c_char_p), ("length", ctypes.c_size_t)]


TEXTS = ("data_unit", "knee_rule", "noise_model", "record", "derived_mask", "preset", "colour_map", "colour_map_table",
         "data_digest", "data_source", "data_unit_code", "sdr_digest")


class CDeclaration(ctypes.Structure):
    """udeep_declaration, field for field as in core/include/udeep_core.h."""
    _fields_ = [("mapping", CMapping), ("max_data_step", ctypes.c_double), ("noise_parameter", ctypes.c_double),
                ("rounding_to_noise", ctypes.c_double), ("masked", ctypes.c_uint64), ("derived_masked", ctypes.c_uint64),
                *[(name, CText) for name in TEXTS]]


@pytest.fixture(scope="session")
def lib():
    built = subprocess.run([CARGO, "build", "--release", "--features", "jpeg"], cwd=CORE, capture_output=True, text=True,
                           check=False)
    assert built.returncode == 0, built.stderr[-2000:]
    name = {"win32": "udeep_core.dll", "darwin": "libudeep_core.dylib"}.get(sys.platform, "libudeep_core.so")
    dll = ctypes.CDLL(str(CORE / "target" / "release" / name))
    doubles, record = ctypes.POINTER(ctypes.c_double), ctypes.POINTER(CMapping)
    for f in ("udeep_position", "udeep_light", "udeep_sdr_light", "udeep_value", "udeep_value_at"):
        getattr(dll, f).argtypes = [record, doubles, doubles, ctypes.c_size_t]
        getattr(dll, f).restype = ctypes.c_int32
    dll.udeep_colours.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_double, doubles, doubles,
                                    ctypes.c_size_t]
    dll.udeep_colours.restype = ctypes.c_int32
    dll.udeep_mapping_validate.argtypes = [record]
    dll.udeep_mapping_validate.restype = ctypes.c_int32
    dll.udeep_version.restype = ctypes.c_uint32
    dll.udeep_light_parallel.argtypes = [record, doubles, doubles, ctypes.c_size_t, ctypes.c_size_t]
    dll.udeep_light_parallel.restype = ctypes.c_int32
    dll.udeep_digest_data.argtypes = [ctypes.POINTER(ctypes.c_size_t), ctypes.c_size_t, doubles, ctypes.c_char_p]
    dll.udeep_digest_data.restype = ctypes.c_int32
    dll.udeep_digest_pixels.argtypes = [ctypes.c_size_t, ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint8), ctypes.c_char_p]
    dll.udeep_digest_pixels.restype = ctypes.c_int32
    dll.udeep_gainmap_jpeg.argtypes = [doubles, doubles, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_double, ctypes.c_uint32,
                                         ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
                                         ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)), ctypes.POINTER(ctypes.c_size_t)]
    dll.udeep_gainmap_jpeg.restype = ctypes.c_int32
    dll.udeep_bytes_free.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]
    dll.udeep_bytes_free.restype = None
    for f in ("udeep_description", "udeep_caption"):
        getattr(dll, f).argtypes = [ctypes.POINTER(CDeclaration), ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
                                    ctypes.POINTER(ctypes.c_size_t)]
        getattr(dll, f).restype = ctypes.c_int32
    dll.udeep_levels.argtypes = [record, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_double,
                                   ctypes.c_size_t, ctypes.c_uint32, ctypes.c_double, doubles, doubles]
    dll.udeep_levels.restype = ctypes.c_int32
    for f in ("udeep_srgb_encode", "udeep_srgb_decode"):
        getattr(dll, f).argtypes = [doubles, doubles, ctypes.c_size_t]
        getattr(dll, f).restype = ctypes.c_int32
    bytes_ = ctypes.POINTER(ctypes.c_uint8)
    dll.udeep_mask_isolated.argtypes = [bytes_, ctypes.c_size_t, ctypes.c_size_t, bytes_]
    dll.udeep_mask_isolated.restype = ctypes.c_int32
    return dll


def record(m: dict) -> CMapping:
    return CMapping(m["range_low"], m["range_high"], m["knee"], m["reference_headroom"], m["stretch_width"],
                    m["stretch_offset"], float(m["above_knee"].partition("exit=")[2]), STRETCH[m["stretch"]], 0)


def call(f, m: CMapping, values) -> list:
    x = np.array([math.nan if v is None else v for v in values], dtype=np.float64)
    out = np.empty_like(x)
    ptr = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    assert f(ctypes.byref(m), ptr(x), ptr(out), x.size) == 0
    return [None if math.isnan(v) else v for v in out.tolist()]


def mapping_cases():
    cases = json.loads(golden.PATH.read_text(encoding="utf-8"))["cases"]
    return {name: c for name, c in cases.items() if c["kind"] == "mapping"}


def test_the_record_is_laid_out_as_the_header_says(lib):
    assert ctypes.sizeof(CMapping) == 64 and CMapping.stretch.offset == 56
    assert lib.udeep_version() == 300
    header = (CORE / "include" / "udeep_core.h").read_text(encoding="utf-8")
    fields = [name for name, _ in CMapping._fields_]
    at = [header.index(f" {name};") for name in fields]
    assert at == sorted(at)                                     # the header declares the fields in the same order


@pytest.mark.parametrize("name", mapping_cases())
def test_the_c_interface_reproduces_the_golden_mapping_cases(lib, name):
    case = mapping_cases()[name]
    m, x, want = record(case["args"]["mapping"]), case["args"]["x"], case["out"]
    assert lib.udeep_mapping_validate(ctypes.byref(m)) == 0
    got = {"position": call(lib.udeep_position, m, x), "light": call(lib.udeep_light, m, x),
           "sdr_light": call(lib.udeep_sdr_light, m, x), "value_of_light": call(lib.udeep_value, m, want["light"])}
    light = np.array([math.nan if v is None else v for v in want["light"]], dtype=np.float64)
    rgb = np.empty(3 * light.size)
    doubles = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    if case["tables"]:
        table = np.fromfile(ROOT / "src" / "udeep" / "tables" / case["tables"][0], dtype="<f4")
        status = lib.udeep_colours(table.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), table.size // 6,
                                     m.reference_headroom, doubles(light), doubles(rgb), light.size)
    else:
        status = lib.udeep_colours(None, 0, m.reference_headroom, doubles(light), doubles(rgb), light.size)
    assert status == 0
    got["colours"] = [[None if math.isnan(v) else v for v in row] for row in rgb.reshape(-1, 3).tolist()]
    problems = [p for key, value in got.items() for p in golden.agree(value, want[key], case, f"{name}.{key}")]
    assert not problems, problems[:5]


def test_input_and_output_may_be_the_same_buffer(lib):
    case = mapping_cases()["mapping_linear_grey"]
    m = record(case["args"]["mapping"])
    x = np.array([v for v in case["args"]["x"] if v is not None], dtype=np.float64)
    separate = np.empty_like(x)
    ptr = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    assert lib.udeep_light(ctypes.byref(m), ptr(x), ptr(separate), x.size) == 0
    assert lib.udeep_light(ctypes.byref(m), ptr(x), ptr(x), x.size) == 0
    assert np.array_equal(x, separate)


def test_errors_come_back_as_codes_not_crashes(lib):
    good = record(mapping_cases()["mapping_linear_grey"]["args"]["mapping"])
    x = np.zeros(4); ptr = x.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    assert lib.udeep_light(None, ptr, ptr, 4) == 1            # null record
    assert lib.udeep_light(ctypes.byref(good), None, ptr, 4) == 1
    assert lib.udeep_light(ctypes.byref(good), None, None, 0) == 0     # nothing to do is not an error
    for change, code in (({"stretch": 9}, 2), ({"range_high": -1000.0}, 3), ({"knee": 9000.0}, 4),
                         ({"reference_headroom": 0.5}, 5), ({"stretch_width": 0.0}, 6), ({"exit_slope": 7.0}, 5)):
        bad = CMapping.from_buffer_copy(good)
        for field, value in change.items():
            setattr(bad, field, value)
        assert lib.udeep_mapping_validate(ctypes.byref(bad)) == code
        assert lib.udeep_light(ctypes.byref(bad), ptr, ptr, 4) == code
    short = np.zeros(5, dtype=np.float32)
    assert lib.udeep_colours(short.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), 0, 4.0, ptr,
                               np.zeros(12).ctypes.data_as(ctypes.POINTER(ctypes.c_double)), 4) == 7


def test_the_header_names_every_error_code_the_core_returns():
    header = (CORE / "include" / "udeep_core.h").read_text(encoding="utf-8")
    source = (CORE / "src" / "lib.rs").read_text(encoding="utf-8")
    in_header = {int(n) for n in re.findall(r"UDEEP_ERROR_\w+ = (\d+)", header)}
    in_core = {int(n) for n in re.findall(r"^    [A-Z]\w+ = (\d+),$", source.partition("pub enum Error")[2]
                                          .partition("}")[0], re.MULTILINE)}
    assert in_header == in_core == set(range(1, 12))


# stretch, range_low, range_high, knee, headroom, width, offset, exit slope -> the code the core must return
UNIT = ("linear", 0.0, 1.0, 0.5, 4.0, 1.0, 0.0, 1.0)
STRAIGHT = ("proportional", 0.0, 12.0, 3.0, 4.0, 1.0, 0.0, 1.0)


def changed(base: tuple, **change) -> tuple:
    names = ("stretch", "range_low", "range_high", "knee", "reference_headroom", "stretch_width", "stretch_offset",
             "exit_slope")
    return tuple(change.get(name, value) for name, value in zip(names, base, strict=True))


RECORDS = [(UNIT, 0), (STRAIGHT, 0)]
RECORDS += [(changed(UNIT, **{field: bad}), 10)                              # not finite: before any other check
            for field in ("range_low", "range_high", "knee", "reference_headroom", "stretch_width", "stretch_offset")
            for bad in (math.nan, math.inf, -math.inf)]
RECORDS += [(changed(UNIT, knee=knee), code)                                 # a knee the curve cannot place
            for knee, code in ((1e-6, 0), (1.0 - 1e-6, 0), (1.0, 0), (1e-9, 4), (5e-7, 4), (1.0 - 5e-7, 4),
                               (1.0 - 1e-9, 4), (0.0, 4), (1.5, 4))]
RECORDS += [(("log", 1.0, 1e12, 1.0 + 1e-9, 4.0, 1.0, 0.0, 1.0), 4),         # inside the range, at position 3.6e-11
            (("asinh", 0.0, 20.0, 1.2, 4.0, 0.1, 0.0, 1.0), 0),
            (changed(UNIT, stretch_offset=0.25, knee=0.75), 0),
            (changed(UNIT, stretch_offset=0.25, knee=0.25 + 1e-9), 4)]
RECORDS += [(changed(STRAIGHT, **change), code)                              # light must be value / knee throughout
            for change, code in (({"range_high": 12.0 * (1.0 + 5e-10)}, 0), ({"range_high": 12.0 * (1.0 + 2e-9)}, 6),
                                 ({"range_high": 10.0}, 6), ({"reference_headroom": 2.0}, 6), ({"exit_slope": 0.5}, 6),
                                 ({"knee": 12.0, "reference_headroom": 1.0}, 0), ({"range_low": 1.0}, 6),
                                 ({"stretch_offset": 1.0}, 6))]
RECORDS += [(changed(UNIT, exit_slope=slope), code) for slope, code in ((0.0, 0), (3.0, 0), (3.5, 5), (-0.5, 5))]


@pytest.mark.parametrize(("fields", "code"), RECORDS)
def test_the_core_refuses_exactly_the_records_the_reference_refuses(lib, fields, code):
    from udeep import Mapping
    stretch, low, high, knee, headroom, width, offset, slope = fields
    c = CMapping(low, high, knee, headroom, width, offset, slope, STRETCH[stretch], 0)
    assert lib.udeep_mapping_validate(ctypes.byref(c)) == code
    try:
        Mapping(stretch, low, high, knee, headroom, width, offset, f"hermite; exit={slope:g}")
        reference_accepts = True
    except ValueError:
        reference_accepts = False
    assert reference_accepts == (code == 0)


def test_a_small_or_single_threaded_call_starts_no_thread(lib):
    """A thread costs tens of microseconds to start; the loop over one value costs well under one. So the two
    calls must take about as long when the parallel one is to use the caller's thread (threads = 1, or too few
    values for a second)."""
    import time
    m = record(mapping_cases()["mapping_linear_grey"]["args"]["mapping"])
    x = np.array([100.0]); out = np.empty_like(x)
    ptr = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    px, pout, ref = ptr(x), ptr(out), ctypes.byref(m)

    def best(call) -> float:
        times = []
        for _ in range(7):
            start = time.perf_counter()
            for _ in range(300):
                call()
            times.append((time.perf_counter() - start) / 300)
        return min(times)

    serial = best(lambda: lib.udeep_light(ref, px, pout, 1))
    for threads in (1, 0, 8):
        parallel = best(lambda t=threads: lib.udeep_light_parallel(ref, px, pout, 1, t))
        assert parallel < 5.0 * serial + 5e-6, (threads, parallel, serial)


def test_the_parallel_loop_gives_the_same_bits_and_works_in_place(lib):
    from udeep import Mapping
    m = Mapping.from_stretch({"kind": "asinh", "lo": 0.0, "hi": 20.0, "width": 0.1}, 1.2, 4.0, "grey")
    c = record(dataclasses.asdict(m))
    x = np.random.default_rng(0).uniform(-1.0, 21.0, 300_001); x[::1009] = np.nan
    ptr = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    serial = np.empty_like(x)
    assert lib.udeep_light(ctypes.byref(c), ptr(x), ptr(serial), x.size) == 0
    assert np.array_equal(serial, m.light(x), equal_nan=True) or np.allclose(serial, m.light(x), rtol=1e-12, equal_nan=True)
    for threads in (0, 1, 3, 8):
        out = np.empty_like(x)
        assert lib.udeep_light_parallel(ctypes.byref(c), ptr(x), ptr(out), x.size, threads) == 0
        assert out.tobytes() == serial.tobytes(), threads
    shared = x.copy()
    assert lib.udeep_light_parallel(ctypes.byref(c), ptr(shared), ptr(shared), shared.size, 0) == 0
    assert shared.tobytes() == serial.tobytes()
    assert lib.udeep_light_parallel(None, ptr(x), ptr(x), 4, 0) == 1


def test_the_digests_through_the_c_interface_are_the_reference_ones(lib):
    from udeep.metadata import digest_data, digest_pixels
    out = ctypes.create_string_buffer(72)
    data = np.array([[0.0, -0.0, 1.5], [np.nan, -2.25, 1e300]])
    shape = (ctypes.c_size_t * 2)(*data.shape)
    assert lib.udeep_digest_data(shape, 2, data.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), out) == 0
    assert out.value.decode() == digest_data(data) and len(out.value) == 71
    image = np.random.default_rng(1).integers(0, 256, (37, 53, 3), dtype=np.uint8)
    assert lib.udeep_digest_pixels(37, 53, image.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)), out) == 0
    assert out.value.decode() == digest_pixels(image)
    cases = json.loads(golden.PATH.read_text(encoding="utf-8"))["cases"]
    assert digest_data(data) == cases["digest_data"]["out"]          # the same array as the golden case
    assert lib.udeep_digest_pixels(37, 53, None, out) == 1


def test_a_value_with_no_dimensions_has_the_reference_digest_of_a_scalar(lib):
    """NumPy makes a 0-dimensional array a list of one before the reference digests it; the core must do the same
    for dimensions = 0, or one scalar would have two digests."""
    from udeep.metadata import digest_data
    out = ctypes.create_string_buffer(72)
    doubles = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    for value in (1.5, 0.0, -0.0, math.nan, -2.25e300):
        scalar = np.array(value)
        assert scalar.ndim == 0
        assert lib.udeep_digest_data(None, 0, doubles(scalar), out) == 0      # no shape to point at
        assert out.value.decode() == digest_data(scalar) == digest_data(np.array([value]))
        unused = (ctypes.c_size_t * 1)(7)
        assert lib.udeep_digest_data(unused, 0, doubles(scalar), out) == 0    # a pointer given is not read
        assert out.value.decode() == digest_data(scalar)
    one = np.array([[1.5]])
    assert lib.udeep_digest_data((ctypes.c_size_t * 2)(1, 1), 2, doubles(one), out) == 0
    assert out.value.decode() == digest_data(one) != digest_data(np.array(1.5))   # a stated shape still counts
    assert lib.udeep_digest_data(None, 2, doubles(one), out) == 1             # a shape is needed when there is one
    assert lib.udeep_digest_data(None, 0, None, out) == 1


def rust_gainmap(lib, sdr, hdr, headroom, description=b"", quality=95, gain_quality=100):
    sdr, hdr = (np.ascontiguousarray(a, dtype=np.float64) for a in (sdr, hdr))
    ptr = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    data, length = ctypes.POINTER(ctypes.c_uint8)(), ctypes.c_size_t()
    status = lib.udeep_gainmap_jpeg(ptr(sdr), ptr(hdr), sdr.shape[1], sdr.shape[0], headroom, quality, gain_quality,
                                      description, len(description), ctypes.byref(data), ctypes.byref(length))
    if status != 0:
        return status, None
    out = ctypes.string_at(data, length.value)
    lib.udeep_bytes_free(data, length.value)
    return 0, out


def scene():
    """A fair SDR view and its HDR view of a small image: a ramp, a block above the knee, noise below it."""
    from udeep.view import hdr_view
    rng = np.random.default_rng(5)
    d = 20.0 + 25.0 * rng.random((72, 96)); d[20:50, 30:70] = np.linspace(55.0, 99.0, 40)[None, :]
    return d, hdr_view(d, "extend", 50.0, {"kind": "linear", "lo": 0.0, "hi": 100.0}, 4.0, "fire")


def test_a_file_written_by_rust_is_read_and_validated_by_python(lib, tmp_path):
    """The independent check on both sides: Rust's writer against Python's reader and validator."""
    from udeep import read_gainmap_jpeg, read_metadata
    from udeep.gainmap import read_iso_metadata
    from udeep.metadata import Declaration, data_steps, description, digest_data
    from udeep.validate import validate
    d, v = scene()
    m = v.mapping
    declared = Declaration(**dataclasses.asdict(m), data_unit="counts", knee_rule="preset; half of full scale",
                           max_data_step=float(data_steps(m)[1].max()), preset="extend", data_digest=digest_data(d))
    status, file = rust_gainmap(lib, v.sdr, v.hdr, 4.0, description(declared))
    assert status == 0
    path = tmp_path / "rust.jpg"; path.write_bytes(file)
    from udeep.metadata import parse, to_xmp
    assert read_metadata(path) == parse(to_xmp(declared))           # the declaration survives, as its stored form
    iso = read_iso_metadata(path)
    assert iso["alternate_hdr_headroom"] == pytest.approx(2.0, abs=1e-6) and iso["gamma"] == 1.0
    report = validate(path, data=d)
    assert report.ok, str(report)
    assert [report.status(n) for n in ("metadata", "mapping", "headroom", "precision", "data link")] == ["pass"] * 5
    # and the pixels are the figure: the base is the SDR view, the full decode is the HDR view
    base, full = read_gainmap_jpeg(path, 1.0).astype(float), read_gainmap_jpeg(path, 4.0).astype(float)
    # per-pixel noise is the hardest case for JPEG, so the yardstick is the reference writer on the same scene
    from udeep import write_gainmap_jpeg
    reference = tmp_path / "python.jpg"; write_gainmap_jpeg(v.sdr, v.hdr, reference, headroom=4.0)
    reference_error = np.abs(read_gainmap_jpeg(reference, 1.0).astype(float) - v.sdr).mean()
    assert np.abs(base - v.sdr).mean() < 1.5 * reference_error, (np.abs(base - v.sdr).mean(), reference_error)
    lifted = v.hdr.max(-1) > 1.05
    assert np.median(np.abs(full.max(-1)[lifted] / v.hdr.max(-1)[lifted] - 1.0)) < 0.03
    assert full.max() <= 4.0 * 1.05


def test_rust_and_python_writers_agree_within_jpeg_error(lib, tmp_path):
    from udeep import read_gainmap_jpeg, write_gainmap_jpeg
    _d, v = scene()
    _status, file = rust_gainmap(lib, v.sdr, v.hdr, 4.0)
    a, b = tmp_path / "rust.jpg", tmp_path / "python.jpg"
    a.write_bytes(file); write_gainmap_jpeg(v.sdr, v.hdr, b, headroom=4.0)
    for boost in (1.0, 2.0, 4.0):
        ra, rb = read_gainmap_jpeg(a, boost).astype(float), read_gainmap_jpeg(b, boost).astype(float)
        assert np.abs(ra - rb).mean() < 0.01 * boost, boost          # two JPEG encoders: close, not identical
    assert 0.5 < a.stat().st_size / b.stat().st_size < 2.0


def test_the_writer_refuses_images_it_cannot_write(lib):
    good = np.full((8, 8, 3), 0.5)
    assert rust_gainmap(lib, good, good * 2, 4.0)[0] == 0
    assert rust_gainmap(lib, good * 3, good, 4.0)[0] == 8            # SDR outside 0..1
    assert rust_gainmap(lib, good, good, 1.0)[0] == 8                # headroom must be above 1
    assert rust_gainmap(lib, good, good, 4.0, b"x" * 70_000)[0] == 9   # metadata too large for a JPEG segment


@pytest.mark.parametrize("too_large", [math.inf, sys.float_info.max])
def test_both_writers_refuse_a_value_too_large_to_store_as_a_gain(lib, tmp_path, too_large):
    """Its gain has no bound: the file used to be written with hdrgm:GainMapMax="inf", which no reader accepts. A
    finite value near the largest number overflows the same way."""
    from udeep import write_gainmap_jpeg
    good = np.full((8, 8, 3), 0.5)
    hdr = good * 2; hdr[0, 0, 0] = too_large
    assert rust_gainmap(lib, good, hdr, 4.0) == (8, None)
    with pytest.raises(ValueError, match="too large"):
        write_gainmap_jpeg(good, hdr, tmp_path / "f.jpg", headroom=4.0)
    assert rust_gainmap(lib, good, good * 2, 4.0)[0] == 0            # the same image without it is written


def test_both_writers_take_minus_infinity_as_black(lib, tmp_path):
    from udeep import read_gainmap_jpeg, write_gainmap_jpeg
    good = np.full((8, 8, 3), 0.5)
    hdr = good * 2; hdr[0, 0, 0] = -math.inf
    status, file = rust_gainmap(lib, good, hdr, 4.0)
    assert status == 0
    a, b = tmp_path / "rust.jpg", tmp_path / "python.jpg"
    a.write_bytes(file); write_gainmap_jpeg(good, hdr, b, headroom=4.0)
    assert np.isfinite(read_gainmap_jpeg(a, 4.0)).all() and np.isfinite(read_gainmap_jpeg(b, 4.0)).all()


def test_a_nan_is_written_as_zero_as_the_reference_writes_it(lib, tmp_path):
    """A view marks a pixel with no data by NaN, in both figures; the reference writes it as black."""
    from udeep import read_gainmap_jpeg, write_gainmap_jpeg
    sdr = np.full((8, 8, 3), 0.5); hdr = sdr * 2
    sdr[:4] = np.nan; hdr[:4] = np.nan
    status, file = rust_gainmap(lib, sdr, hdr, 4.0)
    assert status == 0
    a, b = tmp_path / "rust.jpg", tmp_path / "python.jpg"
    a.write_bytes(file); write_gainmap_jpeg(sdr, hdr, b, headroom=4.0)
    ra, rb = read_gainmap_jpeg(a, 4.0).astype(float), read_gainmap_jpeg(b, 4.0).astype(float)
    assert np.isfinite(ra).all() and np.abs(ra - rb).mean() < 0.04
    assert ra[:2].max() < 0.05 and ra[6:].min() > 0.9



# ---- what a file declares, through the C interface

NOISE_MODEL = {"unknown": 0, "constant": 1, "poisson": 2, "relative": 3, "tolerance": 4}


def c_declaration(d) -> CDeclaration:
    """The reference Declaration as the C record. The mapping part is the same record everything else takes."""
    absent = lambda v: math.nan if v is None else v
    text = lambda v: CText(None, 0) if v is None else CText(v.encode(), len(v.encode()))
    m = dataclasses.asdict(d)
    return CDeclaration(record(m), d.max_data_step, absent(d.noise_parameter), absent(d.rounding_to_noise), d.masked,
                        d.derived_masked, *[text(m[name]) for name in TEXTS])


def owned_text(lib, function, c: CDeclaration):
    data, length = ctypes.POINTER(ctypes.c_uint8)(), ctypes.c_size_t()
    status = function(ctypes.byref(c), ctypes.byref(data), ctypes.byref(length))
    if status != 0:
        return status, None
    out = ctypes.string_at(data, length.value)
    lib.udeep_bytes_free(data, length.value)
    return 0, out


def declarations():
    from udeep.metadata import Declaration
    full = Declaration(data_unit="counts s\u207b\u00b9", stretch="asinh", range_low=0.0, range_high=5000.0, knee=1200.5,
                       knee_rule="physical; detector saturation", reference_headroom=4.0, max_data_step=31.25,
                       noise_model="poisson", noise_parameter=1.0, stretch_width=40.0, stretch_offset=2.5,
                       above_knee="hermite; exit=0.5", masked=17, derived_mask="isolated-3x3", derived_masked=4,
                       record="rendering", preset="highlight", colour_map="fire/1", rounding_to_noise=0.073,
                       data_digest="sha256:" + "ab" * 32, data_source="doi:10.5281/zenodo.0000000",
                       data_unit_code="s-1", sdr_digest="sha256:" + "cd" * 32)
    least = Declaration(data_unit="K", stretch="linear", range_low=0.0, range_high=100.0, knee=50.0,
                        knee_rule="preset", reference_headroom=2.0, max_data_step=0.4)
    proportional = Declaration(data_unit="Jy/beam", stretch="proportional", range_low=0.0, range_high=3e-3,
                               knee=1e-3, knee_rule="statistical; 5 sigma", reference_headroom=3.0,
                               max_data_step=1.2e-5, noise_model="constant", noise_parameter=2e-4,
                               rounding_to_noise=0.0173, colour_map="grey", record="data-record")
    return {"full": full, "least": least, "proportional": proportional}


@pytest.mark.parametrize("name", ["full", "least", "proportional"])
def test_the_c_interface_writes_the_reference_description_and_caption(lib, name):
    from udeep import metadata
    d = declarations()[name]
    c = c_declaration(d)
    status, element = owned_text(lib, lib.udeep_description, c)
    assert status == 0 and element == metadata.description(d)
    assert metadata.parse(metadata._packet(element)) == d                       # and the reference reads it back
    status, text = owned_text(lib, lib.udeep_caption, c)
    assert status == 0 and text.decode() == metadata.caption(d)


def test_the_declaration_record_is_laid_out_as_the_header_says():
    header = (CORE / "include" / "udeep_core.h").read_text(encoding="utf-8")
    body = header.partition("typedef struct udeep_declaration {")[2].partition("} udeep_declaration;")[0]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    names = [n.strip() for line in body.split(";") if line.strip()
             for n in line.split(None, 1)[1].split(",")]
    assert names == [f for f, _ in CDeclaration._fields_]
    assert ctypes.sizeof(CText) == 2 * ctypes.sizeof(ctypes.c_size_t)
    assert ctypes.sizeof(CDeclaration) == 64 + 5 * 8 + len(TEXTS) * ctypes.sizeof(CText)


def test_a_declaration_the_profile_would_refuse_comes_back_as_a_code(lib):
    d = declarations()["full"]
    both = (lib.udeep_description, lib.udeep_caption)
    for missing in ("data_unit", "knee_rule", "noise_model", "record"):          # the four that must be there
        c = c_declaration(d); setattr(c, missing, CText(None, 0))
        assert [owned_text(lib, f, c)[0] for f in both] == [11, 11], missing
    c = c_declaration(d); c.data_unit = CText(b"\xff\xfe", 2)                    # not UTF-8
    assert [owned_text(lib, f, c)[0] for f in both] == [11, 11]
    c = c_declaration(d); c.noise_model = CText(b"gaussian", 8)                  # no such model
    assert [owned_text(lib, f, c)[0] for f in both] == [11, 11]
    c = c_declaration(d); c.noise_parameter = math.nan                           # a model without its parameter
    assert [owned_text(lib, f, c)[0] for f in both] == [11, 11]
    c = c_declaration(d); c.max_data_step = math.inf
    assert [owned_text(lib, f, c)[0] for f in both] == [11, 11]
    c = c_declaration(d); c.mapping.knee = 1e9                                   # the mapping's own code comes back
    assert owned_text(lib, lib.udeep_description, c)[0] == lib.udeep_mapping_validate(ctypes.byref(c.mapping)) != 0
    data, length = ctypes.POINTER(ctypes.c_uint8)(), ctypes.c_size_t()
    assert lib.udeep_description(None, ctypes.byref(data), ctypes.byref(length)) == 1
    assert lib.udeep_description(ctypes.byref(c_declaration(d)), None, ctypes.byref(length)) == 1
    lib.udeep_bytes_free(None, 0)                                              # null is ignored


def c_levels(lib, mapping, scaled_by, noise=("unknown", None), samples=1 << 16):
    from udeep import golden as _g  # noqa: F401  (keeps the import order of this file's other helpers)
    m = record(dataclasses.asdict(mapping))
    table = mapping.table
    if table is None:
        pointer, nodes = None, 0
    else:
        flat = np.frombuffer(table.to_bytes(), dtype="<f4").copy()
        pointer, nodes = flat.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), flat.size // 6
    step, ratio = ctypes.c_double(), ctypes.c_double()
    status = lib.udeep_levels(ctypes.byref(m), pointer, nodes, scaled_by, samples, NOISE_MODEL[noise[0]],
                                0.0 if noise[1] is None else noise[1], ctypes.byref(step), ctypes.byref(ratio))
    return status, step.value, ratio.value


LEVEL_CASES = [
    ({"kind": "linear", "lo": 0.0, "hi": 100.0}, 50.0, 4.0, "fire", ("constant", 0.5)),
    ({"kind": "asinh", "lo": 0.0, "hi": 5000.0, "width": 40.0}, 1200.0, 4.0, "ice", ("poisson", 1.0)),
    ({"kind": "log", "lo": 1.0, "hi": 1e4}, 300.0, 2.0, "grey", ("relative", 0.02)),
    ({"kind": "linear", "lo": -5.0, "hi": 20.0, "offset": 1.5}, 12.0, 8.0, "fire", ("tolerance", 0.05)),
    ({"kind": "linear", "lo": 0.0, "hi": 100.0}, 50.0, 3.0, "fire", ("unknown", None)),   # a table that is carried
]


@pytest.mark.parametrize("case", LEVEL_CASES, ids=lambda c: f"{c[0]['kind']}-{c[3]}-{c[4][0]}")
def test_the_c_interface_gives_the_reference_levels_and_criterion(lib, case):
    from udeep import Mapping, metadata
    stretch, knee, headroom, cmap, noise = case
    m = Mapping.from_stretch(stretch, knee, headroom, cmap)
    for scaled_by in (headroom, 2.0 * headroom):
        status, step, ratio = c_levels(lib, m, scaled_by, noise)
        steps = metadata.data_steps(m, scaled_by)
        assert status == 0 and step == pytest.approx(float(steps[1].max()), rel=1e-9)
        against = metadata.rounding_to_noise(m, noise, steps=steps)
        if against is None:
            assert math.isnan(ratio)
        else:
            assert ratio == pytest.approx(min(against["worst"], 1e9), rel=1e-9)


def test_the_levels_function_refuses_what_it_cannot_use(lib):
    from udeep import Mapping
    m = Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 100.0}, 50.0, 4.0, "grey")
    assert c_levels(lib, m, 4.0, ("constant", 0.5))[0] == 0
    c = record(dataclasses.asdict(m))
    step, ratio = ctypes.c_double(), ctypes.c_double()
    call = lambda model, parameter: lib.udeep_levels(ctypes.byref(c), None, 0, 4.0, 1 << 12, model, parameter,
                                                       ctypes.byref(step), ctypes.byref(ratio))
    assert call(9, 1.0) == 11                                                    # no such noise model
    assert [call(1, v) for v in (0.0, -1.0, math.nan, math.inf)] == [11] * 4     # a parameter that means nothing
    assert call(0, math.nan) == 0 and math.isnan(ratio.value)                    # unknown: the parameter is unused
    assert lib.udeep_levels(ctypes.byref(c), None, 0, 4.0, 1 << 12, 0, 0.0, None, ctypes.byref(ratio)) == 1
    assert lib.udeep_levels(None, None, 0, 4.0, 1 << 12, 0, 0.0, ctypes.byref(step), ctypes.byref(ratio)) == 1


def test_the_srgb_signal_through_the_c_interface_is_the_reference_one(lib):
    from udeep.export import srgb_oetf
    from udeep.mpl import _eotf
    ptr = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    linear = np.concatenate([np.linspace(0.0, 1.0, 4097), [-0.5, 1.5, 0.0031308, math.nan]])
    out = np.empty_like(linear)
    assert lib.udeep_srgb_encode(ptr(linear), ptr(out), linear.size) == 0
    assert np.allclose(out[:-1], srgb_oetf(np.clip(linear[:-1], 0.0, 1.0)), rtol=0.0, atol=1e-15) and math.isnan(out[-1])
    codes = np.arange(256.0) / 255.0
    back = np.empty_like(codes)
    assert lib.udeep_srgb_decode(ptr(codes), ptr(back), codes.size) == 0
    assert np.allclose(back, _eotf(codes), rtol=1e-15, atol=0.0)
    assert lib.udeep_srgb_encode(ptr(back), ptr(back), back.size) == 0          # in place, and there and back
    assert np.allclose(back, codes, rtol=0.0, atol=1e-14)
    assert lib.udeep_srgb_encode(None, ptr(out), 3) == 1 and lib.udeep_srgb_decode(None, None, 0) == 0


def test_the_isolated_rule_through_the_c_interface_is_the_reference_one(lib):
    from udeep import masks
    rng = np.random.default_rng(11)
    ptr = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
    for shape, density in (((40, 57), 0.7), ((3, 3), 1.0), ((1, 9), 1.0), ((25, 2), 0.9), ((64, 64), 0.93)):
        above = rng.random(shape) < density
        given, out = np.ascontiguousarray(above, dtype=np.uint8) * 7, np.full(shape, 9, dtype=np.uint8)   # any non-zero
        assert lib.udeep_mask_isolated(ptr(given), shape[0], shape[1], ptr(out)) == 0
        assert np.array_equal(out, masks.isolated(above).astype(np.uint8)), shape
    assert lib.udeep_mask_isolated(None, 2, 2, ptr(out)) == 1 and lib.udeep_mask_isolated(None, 0, 5, None) == 0
    assert lib.udeep_mask_isolated(ptr(out), sys.maxsize, 4, ptr(out)) == 8     # a size that cannot exist


@pytest.mark.parametrize("case", LEVEL_CASES, ids=lambda c: f"{c[0]['kind']}-{c[3]}")
def test_position_to_value_through_the_c_interface_is_the_reference_one(lib, case):
    from udeep import Mapping
    m = Mapping.from_stretch(case[0], case[1], case[2], "grey")
    t = np.linspace(0.0, 1.0, 513)
    got = np.array(call(lib.udeep_value_at, record(dataclasses.asdict(m)), t.tolist()))
    assert np.allclose(got, m.value_at(t), rtol=1e-12, atol=1e-12)
    assert np.allclose(m.position(got), t, rtol=0.0, atol=1e-9)                  # and it is the inverse of position



# ---- the writer keeps a file within its peak (hold, and the closed loop on its own decoded gain map)

def top_block(cmap, headroom, shape=(72, 99), top=100.0):
    """Noisy data below the knee with flat blocks at the very top of the range and hard edges: the case that read
    up to 9 % over its headroom with the fire and ice maps before the writers checked their own gain map."""
    from udeep.view import hdr_view
    d = 10.0 + 35.0 * np.random.default_rng(3).random(shape)
    d[20:50, 30:70] = top; d[60:, 80:] = top
    v = hdr_view(d, "extend", 50.0, {"kind": "linear", "lo": 0.0, "hi": 100.0}, headroom, cmap)
    return np.asarray(v.sdr, np.float64), np.asarray(v.hdr, np.float64)


@pytest.mark.parametrize("cmap", ["fire", "ice"])
@pytest.mark.parametrize("headroom", [4.0, 8.0])
def test_a_flat_block_at_the_top_written_by_rust_passes_the_reference_readers_limit(lib, tmp_path, cmap, headroom):
    """The Rust writer holds the file within its peak on ITS decoder (core/tests/gainmap.rs). Pillow decodes the same
    JPEGs a code or two differently, which is a few per cent of a lifted pixel, so here the bound is the validator's:
    5 % over the headroom. Measured 2026-10-10 over 190 coloured cases: worst 1.042, none over 1.05 (58 over before)."""
    from udeep.gainmap import read_gainmap_jpeg, read_iso_metadata
    from udeep.validate import HEADROOM_TOLERANCE
    sdr, hdr = top_block(cmap, headroom)
    status, data = rust_gainmap(lib, sdr, hdr, headroom)
    assert status == 0
    path = tmp_path / "top.jpg"; path.write_bytes(data)
    out = read_gainmap_jpeg(path, headroom).astype(np.float64)
    assert out.max() <= headroom * HEADROOM_TOLERANCE
    block = out[24:46, 34:66].max(-1) / hdr[24:46, 34:66].max(-1)
    assert np.median(block) > 0.96 and block.min() > 0.88            # held under the top, not thrown away
    assert data.count(b'hdrgm:GainMapMin="0"') == 1 and read_iso_metadata(path)["gain_map_min"] == 0.0


def test_a_figure_that_lifts_nothing_written_by_rust_reads_the_same_at_any_headroom(lib, tmp_path):
    from udeep.gainmap import read_gainmap_jpeg
    sdr, hdr = top_block("fire", 4.0, top=40.0)                      # the blocks are below the knee
    assert np.array_equal(sdr, hdr)
    status, data = rust_gainmap(lib, sdr, hdr, 4.0)
    path = tmp_path / "flat.jpg"; path.write_bytes(data)
    assert status == 0 and np.array_equal(read_gainmap_jpeg(path, 4.0), read_gainmap_jpeg(path, 1.0))


def test_unlifted_pixels_written_by_rust_carry_no_gain(lib, tmp_path):
    from udeep.gainmap import read_gainmap_jpeg
    sdr, hdr = top_block("ice", 4.0, shape=(72, 96))
    status, data = rust_gainmap(lib, sdr, hdr, 4.0)
    path = tmp_path / "held.jpg"; path.write_bytes(data)
    shown, base = read_gainmap_jpeg(path, 4.0).astype(np.float64), read_gainmap_jpeg(path, 1.0).astype(np.float64)
    assert status == 0 and np.abs(shown[:16] - base[:16]).max() < 1e-6          # rows with no lifted pixel near
    assert shown[24:46, 34:66].max() > 3.0                                      # and the block is lifted


def test_rust_refuses_a_gain_map_it_cannot_hold_and_never_writes_one_over(lib, tmp_path):
    """Below gain quality 100 a coloured map may not be holdable: the call then fails with the codec error (9) and
    gives no bytes. What it does write is within the reference reader's limit."""
    from udeep.gainmap import read_gainmap_jpeg
    from udeep.validate import HEADROOM_TOLERANCE
    sdr, hdr = top_block("ice", 8.0)
    statuses = {}
    for gain_quality in (100, 95, 90, 80, 60, 40, 20):
        status, data = rust_gainmap(lib, sdr, hdr, 8.0, gain_quality=gain_quality)
        statuses[gain_quality] = status
        assert status in (0, 9) and (data is None) == (status == 9)
        if status == 0 and gain_quality == 100:
            path = tmp_path / "q.jpg"; path.write_bytes(data)
            assert read_gainmap_jpeg(path, 8.0).max() <= 8.0 * HEADROOM_TOLERANCE
    assert statuses[100] == 0 and 9 in statuses.values(), statuses
