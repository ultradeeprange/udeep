"""The Rust core, called through its C interface from Python (ctypes), must agree with the golden vectors.

This exercises the interface a host application would use: the `udeep_mapping` record laid out as the header
says, buffers in and out, error codes back. Skipped when cargo is not installed.
"""
import ctypes
import dataclasses
import hashlib
import importlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import test_udeep_readback as readback

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
         "data_digest", "data_source", "data_unit_code", "sdr_digest", "data_digest_sha3", "sdr_digest_sha3")


class CCriterion(ctypes.Structure):
    """udeep_criterion, field for field as in core/include/udeep_core.h: 64 bytes on every machine."""
    _fields_ = [("count", ctypes.c_uint64), ("max_data_step", ctypes.c_double), ("first_start", ctypes.c_double),
                ("rounding_to_noise", ctypes.c_double), ("at", ctypes.c_double), ("failing_fraction", ctypes.c_double),
                ("limit", ctypes.c_double), ("meets", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]


class CDeclaration(ctypes.Structure):
    """udeep_declaration, field for field as in core/include/udeep_core.h."""
    _fields_ = [("mapping", CMapping), ("max_data_step", ctypes.c_double), ("noise_parameter", ctypes.c_double),
                ("rounding_to_noise", ctypes.c_double), ("masked", ctypes.c_uint64), ("derived_masked", ctypes.c_uint64),
                *[(name, CText) for name in TEXTS]]


@pytest.fixture(scope="session")
def lib():
    built = subprocess.run([CARGO, "build", "--release", "--features", "jpeg,png"], cwd=CORE, capture_output=True, text=True,
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
    dll.udeep_colour_table.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_double, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_float)]
    dll.udeep_colour_table.restype = ctypes.c_int32
    dll.udeep_values_pq16.argtypes = [record, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_double,
                                      ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint16), ctypes.c_size_t, doubles]
    dll.udeep_values_pq16.restype = ctypes.c_int32
    dll.udeep_png_pq_values.argtypes = [ctypes.c_char_p, ctypes.c_size_t, record, ctypes.POINTER(ctypes.c_float),
                                        ctypes.c_size_t, ctypes.c_double, ctypes.c_size_t, doubles, ctypes.c_size_t]
    dll.udeep_png_pq_values.restype = ctypes.c_int32
    dll.udeep_mapping_validate.argtypes = [record]
    dll.udeep_mapping_validate.restype = ctypes.c_int32
    dll.udeep_version.restype = ctypes.c_uint32
    dll.udeep_processor_x86_64_v3.restype = ctypes.c_uint32
    dll.udeep_built_for_x86_64_v3.restype = ctypes.c_uint32
    dll.udeep_light_parallel.argtypes = [record, doubles, doubles, ctypes.c_size_t, ctypes.c_size_t]
    dll.udeep_light_parallel.restype = ctypes.c_int32
    dll.udeep_digest_data.argtypes = [ctypes.POINTER(ctypes.c_size_t), ctypes.c_size_t, doubles, ctypes.c_char_p]
    dll.udeep_digest_data.restype = ctypes.c_int32
    dll.udeep_digest_pixels.argtypes = [ctypes.c_size_t, ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint8), ctypes.c_char_p]
    dll.udeep_digest_pixels.restype = ctypes.c_int32
    dll.udeep_digest_data_sha3.argtypes = dll.udeep_digest_data.argtypes
    dll.udeep_digest_data_sha3.restype = ctypes.c_int32
    dll.udeep_digest_pixels_sha3.argtypes = dll.udeep_digest_pixels.argtypes
    dll.udeep_digest_pixels_sha3.restype = ctypes.c_int32
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
    dll.udeep_scale.argtypes = [ctypes.c_uint32, ctypes.c_double, ctypes.c_double, ctypes.c_double, doubles, doubles,
                                ctypes.c_size_t]
    dll.udeep_scale.restype = ctypes.c_int32
    dll.udeep_tone.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_double, doubles, doubles, ctypes.c_size_t]
    dll.udeep_tone.restype = ctypes.c_int32
    dll.udeep_pq_encode.argtypes = [doubles, doubles, ctypes.c_size_t]
    dll.udeep_pq_encode.restype = ctypes.c_int32
    dll.udeep_digest_bytes.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
    dll.udeep_digest_bytes.restype = ctypes.c_int32
    dll.udeep_digest_bytes_sha3.argtypes = dll.udeep_digest_bytes.argtypes
    dll.udeep_digest_bytes_sha3.restype = ctypes.c_int32
    dll.udeep_iso21496_block.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_char_p]
    dll.udeep_iso21496_block.restype = ctypes.c_int32
    dll.udeep_levels_criterion.argtypes = [record, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_double,
                                           ctypes.c_size_t, ctypes.c_uint32, ctypes.c_double, ctypes.c_uint32,
                                           ctypes.POINTER(CCriterion)]
    dll.udeep_levels_criterion.restype = ctypes.c_int32
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


def test_the_library_exports_every_function_the_header_declares(lib):
    """A C host links against the header: each function it declares must be in the library, except the two that
    only a WebAssembly build has (tests/test_udeep_wasm.py holds that build to the whole header)."""
    header = re.sub(r"/\*.*?\*/", "", (CORE / "include" / "udeep_core.h").read_text(encoding="utf-8"), flags=re.DOTALL)
    declared = set(re.findall(r"\b(udeep_\w+)\(", header)) - {"udeep_buffer_alloc", "udeep_buffer_free"}
    assert len(declared) == 42 and not [name for name in sorted(declared) if not hasattr(lib, name)]
    assert not hasattr(lib, "udeep_buffer_alloc")               # memory is the host's own outside WebAssembly


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


def c_table(lib, name: str, headroom: float, nodes: int = 257, version: int = 3) -> tuple[int, np.ndarray]:
    out = np.zeros(6 * nodes, dtype=np.float32)
    status = lib.udeep_colour_table(name.encode(), len(name.encode()), version, headroom, nodes,
                                    out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
    return status, out


@pytest.mark.parametrize(("name", "headroom", "version"),
                         [("fire", 3.0, 3), ("ice", 6.0, 3), ("fire", 1.5, 3), ("ice", 12.5, 3), ("fire", 1.0, 3),
                          ("ice", 5.0, 2), ("fire", 5.0, 1)])
def test_the_core_makes_a_table_at_any_headroom_as_the_reference_does(lib, name, headroom, version):
    """The generator ported to the core, against the reference's generate, at headrooms with no published table
    and at the node count a file carries: within the version-3 floor, 1e-7 of white (core/tests/cmap.rs holds the
    port to every published table)."""
    cm = importlib.import_module("udeep.cmap")      # the package also exports a function called cmap
    status, made = c_table(lib, name, headroom, cm.EMBED_NODES, version)
    assert status == 0
    reference = np.frombuffer(cm.generate(name, headroom, cm.EMBED_NODES, version).to_bytes(), dtype="<f4")
    assert np.max(np.abs(made.astype(np.float64) - reference)) <= 1e-7


def test_the_core_refuses_a_table_for_what_is_not_a_registered_map(lib):
    assert c_table(lib, "viridis", 3.0)[0] == 7 and c_table(lib, "fire", 3.0, version=4)[0] == 7
    assert c_table(lib, "fire", 3.0, nodes=1)[0] == 7 and c_table(lib, "fire", 0.5)[0] == 5
    assert c_table(lib, "fire", math.nan)[0] == 5 and c_table(lib, "fire", math.inf)[0] == 10
    assert lib.udeep_colour_table(None, 0, 3, 3.0, 257, None) == 1


@pytest.mark.parametrize(("cmap", "headroom"), [("grey", 4.0), ("fire", 4.0), ("ice", 8.0), ("fire", 3.0)])
def test_the_core_reads_a_data_record_back_as_the_reference_does(lib, tmp_path, cmap, headroom):
    """udeep_png_pq_values against the reference's values_pq16, on a file the reference wrote: no NaN in either,
    and the same values to the last few bits of the bisection that finds each level's ends. The data runs from
    just above the bottom of the range, where the levels are narrower than the sampling, past both ends."""
    from udeep import Mapping, core
    from udeep.export import write_png_pq
    from udeep.metadata import data_steps_pq16, read_png_pq, values_pq16
    was = core.enabled()
    core.enable(False)
    try:
        m = Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 4095.0}, 2048.0, headroom, cmap)
        rng = np.random.default_rng(3)
        data = np.concatenate([np.arange(0.0, 3.0, 0.01), rng.uniform(-100.0, 4200.0, 1700)]).reshape(40, 50)
        write_png_pq(m.colours(m.light(data)), tmp_path / "record.png")
        reference = values_pq16(m, read_png_pq(tmp_path / "record.png"))
    finally:
        core.enable(was)
    table = None if m.table is None else np.ascontiguousarray(np.concatenate([m.table.below, m.table.above]), "<f4")
    pointer = None if table is None else table.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    nodes = 0 if table is None else table.size // 6
    c = record({"range_low": 0.0, "range_high": 4095.0, "knee": 2048.0, "reference_headroom": headroom,
                "stretch_width": m.stretch_width, "stretch_offset": 0.0, "above_knee": "hermite; exit=1",
                "stretch": "linear"})
    file = (tmp_path / "record.png").read_bytes()
    out = np.empty(data.size)
    into = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    assert lib.udeep_png_pq_values(file, len(file), ctypes.byref(c), pointer, nodes, 203.0, 1 << 20, into(out),
                                   data.size) == 0
    back = out.reshape(data.shape)
    assert not np.isnan(back).any() and not np.isnan(reference).any()
    assert np.abs(back - reference).max() <= 1e-9 * 4095.0
    assert np.abs(back - np.clip(data, 0.0, 4095.0)).max() <= data_steps_pq16(m)[1].max() / 2 * 1.0001
    # the same from the codes alone, and what is refused
    codes = codes_of(file)
    again = np.empty(data.size)
    assert lib.udeep_values_pq16(ctypes.byref(c), pointer, nodes, 203.0, 1 << 20,
                                 codes.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)), data.size, into(again)) == 0
    assert np.array_equal(again, out)
    assert lib.udeep_png_pq_values(file, len(file), ctypes.byref(c), pointer, nodes, 203.0, 1 << 20, into(out),
                                   data.size - 1) == 8
    assert lib.udeep_png_pq_values(file, len(file), ctypes.byref(c), pointer, nodes, -1.0, 1 << 20, into(out),
                                   data.size) == 11


def codes_of(file: bytes) -> np.ndarray:
    """A 16-bit RGB PNG's codes from its bytes, by a reader of this test's own (unfiltered rows, as both writers
    leave them), flat."""
    import struct
    import zlib
    at, idat, size = 8, b"", None
    while at < len(file):
        length, kind = struct.unpack(">I4s", file[at:at + 8])
        body = file[at + 8:at + 8 + length]
        if kind == b"IHDR":
            size = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        at += 12 + length
    rows = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(size[1], size[0] * 6 + 1)
    assert not rows[:, 0].any()
    return np.ascontiguousarray(rows[:, 1:]).view(">u2").astype(np.uint16).reshape(-1)


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


def test_the_sha3_digests_through_the_c_interface_are_the_reference_ones(lib):
    """The core's own SHA3-256 (core/src/sha3.rs) against Python's hashlib, through the digests a file declares and
    on bytes of every length either side of the 136-byte block."""
    from udeep.metadata import digest_data, digest_pixels
    out = ctypes.create_string_buffer(74)
    data = np.array([[0.0, -0.0, 1.5], [np.nan, -2.25, 1e300]])
    shape = (ctypes.c_size_t * 2)(*data.shape)
    assert lib.udeep_digest_data_sha3(shape, 2, data.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), out) == 0
    assert out.value.decode() == digest_data(data, "sha3-256") and len(out.value) == 73
    image = np.random.default_rng(1).integers(0, 256, (37, 53, 3), dtype=np.uint8)
    assert lib.udeep_digest_pixels_sha3(37, 53, image.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)), out) == 0
    assert out.value.decode() == digest_pixels(image, "sha3-256")
    message = bytes(np.random.default_rng(2).integers(0, 256, 700, dtype=np.uint8))
    for length in [*range(300), 543, 544, 545, 700]:
        assert lib.udeep_digest_bytes_sha3(message[:length], length, out) == 0
        assert out.value.decode() == "sha3-256:" + hashlib.sha3_256(message[:length]).hexdigest(), length
    assert lib.udeep_digest_bytes_sha3(None, 0, out) == 0
    assert lib.udeep_digest_bytes_sha3(None, 3, out) == 1 and lib.udeep_digest_bytes_sha3(b"abc", 3, None) == 1
    assert lib.udeep_digest_data_sha3(None, 2, None, out) == 1 and lib.udeep_digest_pixels_sha3(2, 2, None, out) == 1


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


def c_criterion(lib, mapping: dict, table_file: str | None, scaled_by: float, samples: int, noise, pq16: int = 0):
    """udeep_levels_criterion on a mapping record as the golden vectors give it: (status, the record)."""
    if table_file is None:
        pointer, nodes = None, 0
    else:
        flat = np.fromfile(ROOT / "src" / "udeep" / "tables" / table_file, dtype="<f4")
        pointer, nodes = flat.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), flat.size // 6
    out = CCriterion()
    status = lib.udeep_levels_criterion(ctypes.byref(record(mapping)), pointer, nodes, scaled_by, samples,
                                        NOISE_MODEL[noise[0]], 0.0 if noise[1] is None else noise[1], pq16,
                                        ctypes.byref(out))
    return status, out


def test_the_criterion_in_full_through_the_c_interface_reproduces_every_golden_levels_case(lib):
    """Every output of a levels case: the count, the widest level, where the first starts, the worst ratio and
    where it was taken, the failing fraction, the limit and the verdict."""
    cases = golden_cases("levels")
    assert len(cases) == 32
    for name, c in cases.items():
        a = c["args"]
        status, out = c_criterion(lib, a["mapping"], (c["tables"] or [None])[0], a["mapping"]["reference_headroom"],
                                  a["samples"], a["noise"])
        assert status == 0, name
        got = {"count": out.count, "max_data_step": out.max_data_step, "first_start": out.first_start,
               "rounding_to_noise": out.rounding_to_noise, "failing_fraction": out.failing_fraction,
               "limit": out.limit, "meets": bool(out.meets), "at": out.at}
        got = {k: got[k] for k in c["out"]}                         # `at` only where the case states it
        problems = golden.agree(got, c["out"], c, name)
        assert not problems, problems[:5]


def test_the_criterion_in_full_through_the_c_interface_reproduces_every_golden_sixteen_bit_case(lib):
    """The same for a 16-bit PQ PNG: pq16 set, and the white level where an 8-bit case gives the headroom."""
    cases = golden_cases("levels_pq16")
    assert len(cases) == 8
    for name, c in cases.items():
        a = c["args"]
        status, out = c_criterion(lib, a["mapping"], None, a["white_nits"], a["samples"], a["noise"], pq16=1)
        assert status == 0, name
        got = {"count": out.count, "max_data_step": out.max_data_step, "first_start": out.first_start,
               "rounding_to_noise": out.rounding_to_noise, "failing_fraction": out.failing_fraction,
               "limit": out.limit, "meets": bool(out.meets), "at": out.at}
        got = {k: got[k] for k in c["out"]}
        problems = golden.agree(got, c["out"], c, name)
        assert not problems, problems[:5]


def test_the_criterion_in_full_agrees_with_the_two_short_forms_and_the_reference(lib):
    from udeep import Mapping, metadata
    m = Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 4095.0}, 1000.0, 4.0, "grey")
    record_ = dataclasses.asdict(m)
    noise = ("constant", 3.0)
    status, full = c_criterion(lib, record_, None, 203.0, 1 << 18, noise, pq16=1)
    assert status == 0 and ctypes.sizeof(CCriterion) == 64
    step, ratio, count = ctypes.c_double(), ctypes.c_double(), ctypes.c_size_t()
    assert lib.udeep_levels_pq16(ctypes.byref(record(record_)), None, ctypes.c_size_t(0), ctypes.c_double(203.0),
                                 ctypes.c_size_t(1 << 18), ctypes.c_uint32(1), ctypes.c_double(3.0), ctypes.byref(step),
                                 ctypes.byref(ratio), ctypes.byref(count)) == 0
    assert (full.count, full.max_data_step, full.rounding_to_noise) == (count.value, step.value, ratio.value)
    steps = metadata.data_steps_pq16(m, samples=1 << 18)
    reference = metadata.rounding_to_noise(m, noise, steps=steps)
    assert abs(full.count - len(steps[1])) <= max(2, len(steps[1]) // 2000)
    assert full.first_start == pytest.approx(float(steps[0][0]), abs=1e-9)
    assert full.failing_fraction == pytest.approx(reference["failing_fraction"], abs=1e-3)
    assert (full.limit, bool(full.meets)) == (reference["limit"], reference["meets"]) == (0.1, True)
    status, unknown = c_criterion(lib, record_, None, 4.0, 4096, ("unknown", None))
    assert status == 0 and unknown.count > 0 and unknown.meets == 0
    assert all(math.isnan(v) for v in (unknown.rounding_to_noise, unknown.at, unknown.failing_fraction, unknown.limit))
    out = CCriterion()
    call = lambda model, parameter, pq16, scaled_by=4.0: lib.udeep_levels_criterion(
        ctypes.byref(record(record_)), None, 0, scaled_by, 4096, model, parameter, pq16, ctypes.byref(out))
    assert (call(9, 1.0, 0), call(1, 0.0, 0), call(1, 1.0, 2), call(1, 1.0, 1, scaled_by=-1.0)) == (11, 11, 11, 11)
    assert lib.udeep_levels_criterion(ctypes.byref(record(record_)), None, 0, 4.0, 4096, 0, 0.0, 0, None) == 1
    assert lib.udeep_levels_criterion(None, None, 0, 4.0, 4096, 0, 0.0, 0, ctypes.byref(out)) == 1


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


def stage(lib, f, values, *parameters) -> tuple[int, list]:
    """A per-value function of the C interface on `values` (None for NaN), as the golden vectors write them."""
    x = np.array([math.nan if v is None else v for v in values], dtype=np.float64)
    out = np.empty_like(x)
    ptr = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    status = f(*parameters, ptr(x), ptr(out), x.size)
    return status, [None if math.isnan(v) else v for v in out.tolist()]


def golden_cases(kind: str) -> dict:
    cases = json.loads(golden.PATH.read_text(encoding="utf-8"))["cases"]
    return {name: c for name, c in cases.items() if c["kind"] == kind}


def test_the_stages_and_the_bytes_through_the_c_interface_reproduce_the_golden_vectors(lib):
    """Scale, tone, PQ, a table's digest and the ISO 21496-1 block: the kinds the mapping record does not reach."""
    computed = {}
    for name, c in golden_cases("scale").items():
        a = c["args"]
        computed[name] = stage(lib, lib.udeep_scale, a["x"], STRETCH[a["kind"]], a["lo"], a["hi"], a["width"])
    for name, c in golden_cases("tone").items():
        a = c["args"]
        computed[name] = stage(lib, lib.udeep_tone, a["t"], a["knee"], a["headroom"], a.get("exit_slope", 1.0))
    for name, c in golden_cases("pq_encode").items():
        computed[name] = stage(lib, lib.udeep_pq_encode, c["args"]["nits"])
    out = ctypes.create_string_buffer(72)
    for name, c in golden_cases("table").items():
        data = (ROOT / "src" / "udeep" / "tables" / c["args"]["file"]).read_bytes()
        computed[name] = (lib.udeep_digest_bytes(data, len(data), out),
                          {"sha256": out.value.decode(), "nodes": len(data) // 24, "bytes": len(data)})
    block = ctypes.create_string_buffer(61)
    for name, c in golden_cases("iso21496").items():
        a = c["args"]
        computed[name] = (lib.udeep_iso21496_block(a["gain_map_min"], a["gain_map_max"], a["hdr_capacity_max"], block),
                          block.raw.hex())
    assert len(computed) == 3 + 11 + 1 + 24 + 1
    cases = json.loads(golden.PATH.read_text(encoding="utf-8"))["cases"]
    for name, (status, got) in computed.items():
        assert status == 0, name
        problems = golden.agree(got, cases[name]["out"], cases[name], name)
        assert not problems, problems[:5]


# kind, lo, hi, width, and the code the core gives: 0 where the reference's scale accepts them
SCALES = [("linear", 0.0, 1.0, 1.0, 0), ("log", 1e-3, 10.0, 1.0, 0), ("asinh", -5.0, 5.0, 0.1, 0),
          ("linear", 1.0, 1.0, 1.0, 3), ("linear", 2.0, 1.0, 1.0, 3), ("log", 0.0, 10.0, 1.0, 3),
          ("log", -1.0, 10.0, 1.0, 3), ("asinh", 0.0, 1.0, 0.0, 6), ("linear", 0.0, 1.0, -1.0, 6),
          ("linear", math.nan, 1.0, 1.0, 10), ("asinh", 0.0, 1.0, math.nan, 10), ("proportional", 0.0, 1.0, 1.0, 2),
          ("cubic", 0.0, 1.0, 1.0, 2)]
# knee, headroom, exit slope, and the code
TONES = [(0.5, 4.0, 1.0, 0), (1.0, 1.0, 0.0, 0), (1e-6, 16.0, 3.0, 0), (0.0, 4.0, 1.0, 4), (1.5, 4.0, 1.0, 4),
         (-0.5, 4.0, 1.0, 4), (0.5, 0.5, 1.0, 5), (0.5, 4.0, 3.5, 5), (0.5, 4.0, -0.1, 5), (math.nan, 4.0, 1.0, 10),
         (0.5, math.nan, 1.0, 10), (0.5, 4.0, math.nan, 10)]
KIND_CODE = {"linear": 0, "log": 1, "asinh": 2, "proportional": 3, "cubic": 9}


@pytest.mark.parametrize(("kind", "lo", "hi", "width", "code"), SCALES)
def test_the_bare_scale_is_refused_where_the_reference_refuses_it(lib, kind, lo, hi, width, code):
    from udeep.scale import scale
    x = [-1.0, 0.0, 0.5, 2.0, None]
    status, got = stage(lib, lib.udeep_scale, x, KIND_CODE[kind], lo, hi, width)
    assert status == code
    try:
        want = scale(np.array([math.nan if v is None else v for v in x]), kind, lo, hi, width)
    except ValueError:
        want = None
    assert (want is None) == (code != 0)
    if code == 0:
        assert np.allclose(np.array(got[:-1], dtype=float), want[:-1], rtol=1e-12, atol=1e-12) and got[-1] is None


@pytest.mark.parametrize(("knee", "headroom", "exit_slope", "code"), TONES)
def test_the_tone_curve_is_refused_where_the_reference_refuses_it(lib, knee, headroom, exit_slope, code):
    from udeep.tone import tone
    t = [0.0, 0.25, 0.5, 0.75, 1.0, None]
    status, got = stage(lib, lib.udeep_tone, t, knee, headroom, exit_slope)
    assert status == code
    try:
        want = tone(np.array([math.nan if v is None else v for v in t]), knee, headroom, exit_slope)
    except ValueError:
        want = None
    assert (want is None) == (code != 0)
    if code == 0:
        assert np.allclose(np.array(got[:-1], dtype=float), want[:-1], rtol=1e-12, atol=1e-12) and got[-1] is None


def test_the_iso_block_and_the_bytes_digest_refuse_what_they_cannot_write(lib):
    import hashlib
    import struct

    from udeep.gainmap import _iso_metadata
    block = ctypes.create_string_buffer(61)
    for values, code in (((-0.0125, 2.0, 2.0), 0), ((0.0, 0.0, 0.0), 0), ((math.nan, 2.0, 2.0), 10),
                         ((0.0, math.inf, 2.0), 10), ((0.0, 2.0, -1.0), 8), ((0.0, 2200.0, 2.0), 8),
                         ((-2200.0, 2.0, 2.0), 8), ((0.0, 2.0, 4300.0), 8), ((0.0, 2.0, 4294.0), 0)):
        assert lib.udeep_iso21496_block(*values, block) == code, values
        try:
            reference = _iso_metadata(*values)
        except (ValueError, OverflowError, struct.error):
            reference = None
        assert (reference is None) == (code != 0), values
        if code == 0:
            assert block.raw == reference
    assert lib.udeep_iso21496_block(0.0, 2.0, 2.0, None) == 1
    out = ctypes.create_string_buffer(72)
    for data in (b"", b"abc", bytes(range(256)) * 9):
        assert lib.udeep_digest_bytes(data, len(data), out) == 0
        assert out.value.decode() == "sha256:" + hashlib.sha256(data).hexdigest()
    assert lib.udeep_digest_bytes(None, 0, out) == 0 and out.value.decode() == "sha256:" + hashlib.sha256(b"").hexdigest()
    assert lib.udeep_digest_bytes(None, 3, out) == 1 and lib.udeep_digest_bytes(b"abc", 3, None) == 1


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


# ---- a C host: the header read by a C compiler

# How the C host is built for each check: the flags a C compiler is given, MSVC's and then cc's.
C_CHECKS = {
    "plain": (["/W4", "/WX"], ["-std=c99", "-Wall", "-Wextra", "-pedantic", "-Werror"]),
    # out-of-bounds access, use after free, double free; and, with cc, undefined behaviour and leaks
    "sanitizers": (["/W4", "/WX", "/fsanitize=address", "/Zi"],
                   ["-std=c99", "-Wall", "-Wextra", "-Werror", "-g", "-fno-omit-frame-pointer",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]),
    # the compiler's own static analysis, with its findings as errors: compiled, not linked
    "analysis": (["/W4", "/WX", "/analyze", "/c"], ["-std=c99", "-Wall", "-Wextra", "-Werror", "-fanalyzer", "-c"]),
}


def vcvars() -> Path | None:
    """vcvars64.bat of a Visual Studio install, or None. Every install is looked at, previews and ones the
    installer counts as incomplete too: on some machines there are only those."""
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / \
        "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return None
    found = subprocess.run([str(vswhere), "-all", "-prerelease", "-products", "*", "-property", "installationPath"],
                           capture_output=True, text=True, check=False)
    scripts = [Path(line.strip()) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat" for line in found.stdout.splitlines()
               if line.strip()]
    return next((v for v in scripts if v.is_file()), None)


def c_build(source: Path, out: Path, check: str = "plain") -> list[str] | None:
    """The command line that compiles `source` against the header for `check` (C_CHECKS) and links it to the built
    library: MSVC (through vcvars64) on Windows, `cc` elsewhere. None when there is no C compiler here. On Windows
    the batch file also prints the compiler's folder (CL_DIR=...), where MSVC's sanitizer runtime is."""
    include, release = CORE / "include", CORE / "target" / "release"
    msvc, cc_flags = C_CHECKS[check]
    compile_only = "/c" in msvc
    if sys.platform == "win32":
        script_vars = vcvars()
        if script_vars is None:
            return None
        link = "" if compile_only else f' "{release / "udeep_core.dll.lib"}" /Fe"{out}"'
        script = out.with_suffix(".bat")
        script.write_text(f'@call "{script_vars}" >nul\r\n'
                          f'@cl /nologo {" ".join(msvc)} /I"{include}" "{source}"{link} /Fo"{out.parent}\\\\" '
                          f'/Fd"{out.parent}\\\\" || exit /b 1\r\n'
                          '@for %%i in (cl.exe) do @echo CL_DIR=%%~dp$PATH:i\r\n', encoding="ascii")
        return ["cmd", "/d", "/c", str(script)]
    cc = shutil.which("cc")
    if cc is None:
        return None
    link = [] if compile_only else [f"-L{release}", "-ludeep_core", "-lm"]
    return [cc, *cc_flags, f"-I{include}", str(source), *link, "-o", str(out.with_suffix(".o") if compile_only else out)]


def c_compiler_or_skip(tmp_path: Path) -> None:
    trivial = tmp_path / "trivial.c"
    trivial.write_text("int main(void) { return 0; }\n", encoding="ascii")
    probe = c_build(trivial, tmp_path / "trivial.exe")
    if probe is None:
        pytest.skip("no C compiler here")
    if subprocess.run(probe, capture_output=True, text=True, check=False).returncode != 0:
        pytest.skip("a C compiler is here but cannot build a program")


def build_host(tmp_path: Path, check: str) -> tuple[Path, str]:
    """The C host built for `check`: its path, and on Windows the compiler's folder (empty elsewhere)."""
    c_compiler_or_skip(tmp_path)
    host = tmp_path / f"header_check_{check}.exe"
    built = subprocess.run(c_build(CORE / "tests" / "header_check.c", host, check), capture_output=True, text=True,
                           check=False)
    assert built.returncode == 0, built.stdout + built.stderr
    return host, next((line[7:].strip() for line in built.stdout.splitlines() if line.startswith("CL_DIR=")), "")


def host_input() -> str:
    case = mapping_cases()["mapping_linear_grey"]
    m = case["args"]["mapping"]
    x = ["nan" if v is None else repr(v) for v in case["args"]["x"]]
    return " ".join([repr(m[k]) for k in ("range_low", "range_high", "knee", "reference_headroom", "stretch_width",
                                          "stretch_offset")] + [m["above_knee"].partition("exit=")[2],
                                                                str(STRETCH[m["stretch"]]), str(len(x))] + x)


def run_host(command: list[str], extra_path: str = "") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    release = str(CORE / "target" / "release")
    for name in ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):          # where the program finds the library
        env[name] = os.pathsep.join(p for p in (release, extra_path, env.get(name, "")) if p)
    env["LSAN_OPTIONS"] = f"suppressions={CORE / 'tests' / 'lsan.supp'}"   # the JPEG decoder's, at exit
    # the program prints the library's UTF-8 as it is (the caption has a multiplication sign)
    return subprocess.run(command, input=host_input(), capture_output=True, encoding="utf-8", check=False, env=env)


def host_gets_the_golden_results(stdout: str) -> None:
    from udeep import metadata
    case = mapping_cases()["mapping_linear_grey"]
    lines = {line.split(" ", 1)[0]: line.split(" ", 1)[1] for line in stdout.splitlines()}
    assert lines["sizes"].split() == [str(v) for v in (ctypes.sizeof(CMapping), CMapping.stretch.offset,
                                                       ctypes.sizeof(CDeclaration), ctypes.sizeof(CText))]
    assert lines["version"] == "300" and lines["validate"] == "0"

    def numbers(key):
        status, *rest = lines[key].split()
        return int(status), [None if v == "nan" else float(v) for v in rest]

    for key in ("position", "light", "sdr_light"):
        status, got = numbers(key)
        assert status == 0 and not golden.agree(got, case["out"][key], case, key), key
    tone = golden_cases("tone")["tone_0.8_4.0"]
    status, got = numbers("tone")
    assert status == 0 and not golden.agree(got, tone["out"], tone, "tone")
    iso = golden_cases("iso21496")["iso21496_block"]
    assert lines["iso"] == "0 " + iso["out"]
    assert lines["digest"] == "0 sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    levels = golden_cases("levels")["levels_linear_grey"]
    status, size, count, *rest = lines["criterion"].split()
    assert (status, size) == ("0", "64") and levels["args"]["noise"] == ["constant", 8.6]
    got = dict(zip(("max_data_step", "first_start", "rounding_to_noise", "failing_fraction", "limit"), map(float, rest[:5]),
                   strict=True))
    got.update(count=int(count), meets=rest[5] == "1")
    assert not golden.agree({k: got[k] for k in levels["out"]}, levels["out"], levels, "criterion")
    least = declarations()["least"]
    assert lines["description"] == "0 " + metadata.description(least).decode("utf-8")
    assert lines["caption"] == "0 " + metadata.caption(least)
    # the two files: written, measured, read back (8 x 6); pixel 5 of the PNG is 3 x 15/17 white in each channel
    png = lines["png"].split()
    assert png[:5] == ["0", "0", "0", "8", "6"] and abs(float(png[5]) - 3.0 * 15 / 17) < 1e-3
    assert lines["jpeg"].split() == ["0", "0", "0", "8", "6"]


def test_a_c_host_compiled_against_the_header_gets_the_golden_results(lib, tmp_path):
    """The header has been checked by reading it (field order, error codes); here a C compiler builds a program
    against it and the program calls the library, so a declaration that does not match the library fails."""
    host, _ = build_host(tmp_path, "plain")
    ran = run_host([str(host)])
    assert ran.returncode == 0, ran.stdout + ran.stderr
    host_gets_the_golden_results(ran.stdout)


def test_the_c_host_runs_clean_under_the_sanitizers(lib, tmp_path):
    """AddressSanitizer watches every access the host makes to memory, its own and the buffers the library hands
    it; with cc, UndefinedBehaviorSanitizer and LeakSanitizer too, the last over the whole process, the library's
    allocations included. Any finding ends the program with an error."""
    host, cl_dir = build_host(tmp_path, "sanitizers")
    ran = run_host([str(host)], cl_dir)                                   # MSVC's sanitizer runtime is beside cl
    assert ran.returncode == 0 and "Sanitizer" not in ran.stderr, ran.stdout + ran.stderr
    host_gets_the_golden_results(ran.stdout)


def test_the_c_host_passes_the_compilers_static_analysis(lib, tmp_path):
    """MSVC /analyze or GCC -fanalyzer: null dereferences, leaks on a path, buffers overrun, read before written."""
    build_host(tmp_path, "analysis")


@pytest.mark.skipif(shutil.which("valgrind") is None, reason="valgrind is not installed")
def test_the_c_host_runs_clean_under_valgrind(lib, tmp_path):
    """Valgrind checks the whole program, the library's own code too, which the sanitizers do not instrument: an
    invalid access or a byte lost anywhere is an error."""
    host, _ = build_host(tmp_path, "plain")
    ran = run_host(["valgrind", "--quiet", "--error-exitcode=99", "--leak-check=full",
                    "--errors-for-leak-kinds=definite,indirect",
                    f"--suppressions={CORE / 'tests' / 'valgrind.supp'}", str(host)])
    assert ran.returncode == 0, ran.stderr[-4000:]
    host_gets_the_golden_results(ran.stdout)


# ---- the data read back, against levels found independently (tests/test_udeep_readback.py has the oracle)

@pytest.mark.parametrize(("name", "cmap", "headroom"), readback.CASES)
def test_the_core_gives_every_value_as_the_middle_of_its_own_level(lib, name, cmap, headroom):
    """The codes are the reference's, as a file it wrote holds them; the core finds the levels with its own
    arithmetic, which can round a rare code differently, so it is held to the oracle, not to the reference."""
    from udeep import core
    from udeep.metadata import pq16_codes
    was = core.enabled()
    core.enable(False)
    try:
        m = readback.mapping(name, cmap, headroom)
        x = readback.values_to_check(m)
        start, end = readback.runs_around(m, x)
        codes = pq16_codes(m.colours(m.light(x)))
    finally:
        core.enable(was)
    c = CMapping(m.range_low, m.range_high, m.knee, m.reference_headroom, m.stretch_width, m.stretch_offset,
                 m.exit_slope, STRETCH[m.stretch], 0)
    table = None if m.table is None else np.ascontiguousarray(np.concatenate([m.table.below, m.table.above]), "<f4")
    pointer = None if table is None else table.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    given = np.ascontiguousarray(codes, dtype=np.uint16).reshape(-1)
    back = np.empty(x.size)
    assert lib.udeep_values_pq16(ctypes.byref(c), pointer, 0 if table is None else table.size // 6, 203.0, 1 << 20,
                                 given.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)), x.size,
                                 back.ctypes.data_as(ctypes.POINTER(ctypes.c_double))) == 0
    span = m.range_high - m.range_low
    wrong = np.isnan(back) | (np.abs(back - 0.5 * (start + end)) > 1e-5 * (end - start) + 1e-10 * span)
    assert not wrong.any(), f"{wrong.sum()} of {x.size} values misread, for example {np.sort(x[wrong])[:8]}"


# ---- the core's two builds on x86-64: for any processor, and for x86-64-v3, chosen where the processor has it

X86_64 = platform.machine().lower() in ("x86_64", "amd64")
DIGESTS = r"""
import hashlib, json, sys, tempfile
from pathlib import Path
import numpy as np
from udeep import Mapping, core, gainmap, write_png_pq
from udeep.view import hdr_view
stretch = {"kind": "asinh", "lo": 0.0, "hi": 100.0, "width": 5.0}
m = Mapping.from_stretch(stretch, 40.0, 4.0, "fire")
x = np.random.default_rng(5).exponential(20.0, 400_000)
x[::1001] = np.nan
light = core.light(m, x)
view = hdr_view(x[:250_000].reshape(500, 500), "extend", 40.0, stretch, 4.0, "fire")
folder = Path(tempfile.mkdtemp())
gainmap.write_gainmap_jpeg(view.sdr, view.hdr, folder / "f.jpg", 4.0)
write_png_pq(view.hdr, folder / "f.png")
d = lambda b: hashlib.sha256(b).hexdigest()
print(json.dumps({"build": core.build(), "file": core.loaded().name, "light": d(light.tobytes()),
                  "colours": d(core.colours(m, light).tobytes()), "jpeg": d((folder / "f.jpg").read_bytes()),
                  "png": d((folder / "f.png").read_bytes())}))
"""


@pytest.fixture(scope="session")
def both_builds(lib, tmp_path_factory):
    """The core's two builds side by side, as a wheel carries them: udeep_core and udeep_core_v3."""
    if not X86_64:
        pytest.skip("the second build is for x86-64")
    if lib.udeep_processor_x86_64_v3() != 1:
        pytest.skip("this processor cannot run the x86-64-v3 build")
    target = CORE / "target" / "x86-64-v3"
    built = subprocess.run([CARGO, "build", "--release", "--features", "jpeg,png", "--target-dir", str(target)], cwd=CORE,
                           capture_output=True, text=True, check=False,
                           env={**os.environ, "RUSTFLAGS": "-C target-cpu=x86-64-v3"})
    assert built.returncode == 0, built.stderr[-2000:]
    name = {"win32": "udeep_core.dll", "darwin": "libudeep_core.dylib"}.get(sys.platform, "libudeep_core.so")
    folder = tmp_path_factory.mktemp("both")
    shutil.copyfile(CORE / "target" / "release" / name, folder / name)
    shutil.copyfile(target / "release" / name, folder / name.replace("udeep_core", "udeep_core_v3", 1))
    return folder / name


def test_the_core_says_whether_the_processor_can_run_its_wider_build(lib):
    assert lib.udeep_built_for_x86_64_v3() == 0                          # the build every host loads first
    assert lib.udeep_processor_x86_64_v3() in ((0, 1) if X86_64 else (0,))


def test_python_takes_the_wider_build_where_it_can_and_both_give_the_same_bytes(both_builds):
    """The same light, colours, gain-map JPEG and 16-bit PNG, to the byte, from either build: a wider instruction set
    changes how fast the core is, not what it makes (Rust never fuses a multiply and an add unless asked)."""
    def run(**env) -> dict:
        ran = subprocess.run([sys.executable, "-c", DIGESTS], capture_output=True, text=True, check=False,
                             env={**os.environ, "UDEEP_CORE_LIB": str(both_builds), "UDEEP_USE_CORE": "1", **env})
        assert ran.returncode == 0, ran.stderr[-2000:]
        return json.loads(ran.stdout)
    wide, plain = run(), run(UDEEP_CORE_V3="0")
    assert wide["build"] == "x86-64-v3" and "udeep_core_v3" in wide["file"]
    assert plain["build"] == "any processor" and "udeep_core_v3" not in plain["file"]
    made = lambda run: {k: v for k, v in run.items() if k not in ("build", "file")}
    assert made(wide) == made(plain) and len(made(wide)) == 4


@pytest.mark.skipif(shutil.which("julia") is None or not (ROOT / "julia" / "UDeepCore" / "Manifest.toml").exists(),
                    reason="needs julia, with the package's environment instantiated")
def test_julia_takes_the_wider_build_where_it_can(both_builds):
    def build(**env) -> str:
        ran = subprocess.run([shutil.which("julia"), "--project=" + str(ROOT / "julia" / "UDeepCore"), "-e",
                              "using UDeepCore; print(core_build())"], capture_output=True, text=True, check=False,
                             env={**os.environ, "UDEEP_CORE_LIB": str(both_builds), **env})
        assert ran.returncode == 0, ran.stderr[-2000:]
        return ran.stdout.strip()
    assert build() == "x86-64-v3" and build(UDEEP_CORE_V3="0") == "any processor"
