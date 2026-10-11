"""The Rust core, used from Python when it is asked for.

    import udeep.core
    udeep.core.enabled()           # True when the core was found and has not been turned off
    udeep.core.enable(False)       # use the NumPy and Pillow code instead, from here on

The core (core/, a Rust library with a C interface) is used wherever it is found: it maps and colours an image on
every processor core at once (60 to 250 megapixels a second on the development machine against NumPy's 8 to 12,
project/performance-scope.md 4d), and it writes and reads gain-map JPEGs. Where it is not found, the NumPy and
Pillow code of this package does the same work; udeep.core.enable(False), or ENV["UDEEP_USE_CORE"] = "0",
chooses that code even when the core is there.

The two are held to the same golden vectors, and that is the sense in which they are the same: they agree to about
one part in 10^12 in the mapping (their logarithms and hyperbolic sines come from different libraries), which is
far inside any file's precision but can turn an 8-bit code the other way at a few pixels, and so give a figure
another SDR digest. The two JPEG decoders also differ by a code or two at some pixels: a file is held within its
headroom exactly on the decoder its writer used, so write and read with the same one.

The library is looked for at ENV["UDEEP_CORE_LIB"], then beside this module (udeep/_lib, where a packaged
build would put it), then in core/target/release of a source checkout (cargo build --release in core/).
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import numpy as np

SLAB = 1 << 18                       # pixels coloured at a time when the result is kept as float32: the float64
                                     # colours of one slab are the only extra memory, 6 MB
_STRETCH = {"linear": 0, "log": 1, "asinh": 2, "proportional": 3}
_ERRORS = {1: "a required pointer was null", 2: "unknown stretch", 3: "the declared range is not valid",
           4: "the knee is not inside the declared range", 5: "the headroom or the exit slope is out of range",
           6: "the stretch width, or a proportional mapping's range, is not valid",
           7: "the colour-map table is not two segments of nodes x 3", 10: "a number in the mapping is not finite"}
_state = {"library": None, "looked": False, "on": None}      # on: None until asked, then True or False
_DOUBLES = ctypes.POINTER(ctypes.c_double)


class _Record(ctypes.Structure):
    """udeep_mapping, field for field as in core/include/udeep_core.h."""
    _fields_ = [("range_low", ctypes.c_double), ("range_high", ctypes.c_double), ("knee", ctypes.c_double),
                ("reference_headroom", ctypes.c_double), ("stretch_width", ctypes.c_double),
                ("stretch_offset", ctypes.c_double), ("exit_slope", ctypes.c_double), ("stretch", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32)]


def library_path() -> Path | None:
    """Where the core library is, or None if it is in none of the places looked in."""
    name = {"win32": "udeep_core.dll", "darwin": "libudeep_core.dylib"}.get(sys.platform, "libudeep_core.so")
    here = Path(__file__).resolve()
    places = [Path(os.environ["UDEEP_CORE_LIB"])] if os.environ.get("UDEEP_CORE_LIB") else []
    places += [here.with_name("_lib") / name, here.parents[2] / "core" / "target" / "release" / name]
    return next((p for p in places if p.is_file()), None)


def _load():
    if not _state["looked"]:
        _state["looked"] = True
        path = library_path()
        if path is not None:
            try:
                dll = ctypes.CDLL(str(path))
                record = ctypes.POINTER(_Record)
                for function in (dll.udeep_light_parallel, dll.udeep_sdr_light_parallel):
                    function.argtypes = [record, _DOUBLES, _DOUBLES, ctypes.c_size_t, ctypes.c_size_t]
                    function.restype = ctypes.c_int32
                dll.udeep_colours_parallel.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_double,
                                                         _DOUBLES, _DOUBLES, ctypes.c_size_t, ctypes.c_size_t]
                dll.udeep_colours_parallel.restype = ctypes.c_int32
                dll.udeep_version.restype = ctypes.c_uint32
                _state["library"] = dll
                if all(hasattr(dll, f) for f in ("udeep_gainmap_jpeg", "udeep_gainmap_read",
                                                 "udeep_gainmap_from_renders")):                 # the jpeg feature
                    bytes_ = ctypes.POINTER(ctypes.c_uint8)
                    dll.udeep_gainmap_jpeg.argtypes = [
                        _DOUBLES, _DOUBLES, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_double, ctypes.c_uint32,
                        ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(bytes_),
                        ctypes.POINTER(ctypes.c_size_t)]
                    dll.udeep_gainmap_jpeg.restype = ctypes.c_int32
                    dll.udeep_bytes_free.argtypes = [bytes_, ctypes.c_size_t]
                    dll.udeep_bytes_free.restype = None
                    dll.udeep_gainmap_size.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
                                                         ctypes.POINTER(ctypes.c_size_t)]
                    dll.udeep_gainmap_size.restype = ctypes.c_int32
                    dll.udeep_gainmap_read.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_double,
                                                         ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_size_t]
                    dll.udeep_gainmap_read.restype = ctypes.c_int32
                    dll.udeep_gainmap_from_renders.argtypes = [
                        bytes_, bytes_, bytes_, bytes_, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_double,
                        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(bytes_),
                        ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
                    dll.udeep_gainmap_from_renders.restype = ctypes.c_int32
                    _state["files"] = True
            except (OSError, AttributeError):        # not loadable here, or a build older than these functions
                _state["library"] = None
    return _state["library"]


def available() -> bool:
    """Whether a core library with the functions this module needs was found and loaded."""
    return _load() is not None


def files() -> bool:
    """Whether the core in use can write and read gain-map JPEGs (it was built with its `jpeg` feature)."""
    return enabled() and bool(_state.get("files"))


def enable(on: bool = True) -> None:
    """Map with the core from now on (or, with on=False, with NumPy again). Raises RuntimeError if the core is
    asked for and is not there."""
    if on and not available():
        raise RuntimeError("the udeep core library was not found: build it with `cargo build --release` in core/, "
                           "or set UDEEP_CORE_LIB to its path")
    _state["on"] = bool(on)


def enabled() -> bool:
    """Whether the core is in use: yes whenever it is there, unless it was turned off (enable(False), or
    ENV["UDEEP_USE_CORE"] = "0")."""
    if _state["on"] is None:
        _state["on"] = os.environ.get("UDEEP_USE_CORE") != "0" and available()
    return _state["on"]


def _record(mapping) -> _Record:
    return _Record(mapping.range_low, mapping.range_high, mapping.knee, mapping.reference_headroom,
                   mapping.stretch_width, mapping.stretch_offset, mapping.exit_slope, _STRETCH[mapping.stretch], 0)


def _check(status: int) -> None:
    if status != 0:
        raise ValueError(f"the core refused the call: {_ERRORS.get(status, f'error {status}')}")


def _pointer(a: np.ndarray):
    return a.ctypes.data_as(_DOUBLES)


def light(mapping, x, sdr: bool = False, threads: int = 0) -> np.ndarray:
    """Data values -> light, as Mapping.light(x); with sdr=True the fair SDR light, as Mapping.light(x, 1.0).
    `threads` = 0 uses every processor core; the result is the same on any number. Float64, the shape of `x`."""
    dll = _load()
    if dll is None:
        raise RuntimeError("the udeep core library is not available")
    values = np.ascontiguousarray(x, dtype=np.float64)
    out = np.empty_like(values)
    function = dll.udeep_sdr_light_parallel if sdr else dll.udeep_light_parallel
    _check(function(ctypes.byref(_record(mapping)), _pointer(values), _pointer(out), values.size, threads))
    return out.reshape(np.shape(x))                  # a single number comes back as one, not as a list of one


def colours(mapping, y, dtype=np.float64, threads: int = 0) -> np.ndarray:
    """Light -> linear RGB through the mapping's colour map, as Mapping.colours(y, dtype): shape y.shape + (3,).
    With dtype=np.float32 the colours are worked out in float64 a slab at a time and rounded once as they are kept."""
    dll = _load()
    if dll is None:
        raise RuntimeError("the udeep core library is not available")
    values = np.ascontiguousarray(y, dtype=np.float64).reshape(-1)
    table = mapping.table
    if table is None:
        nodes, count = None, 0
    else:
        kept = np.frombuffer(table.to_bytes(), dtype="<f4")
        nodes, count = kept.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), table.nodes
    out = np.empty((values.size, 3), dtype=dtype)
    call = lambda part, into: _check(dll.udeep_colours_parallel(nodes, count, mapping.reference_headroom,
                                                                  _pointer(part), _pointer(into), part.size, threads))
    if dtype is np.float64:
        call(values, out)                                # straight into the result
    else:
        slab = np.empty((min(SLAB, values.size), 3), dtype=np.float64)       # one buffer, used again for each slab
        for start in range(0, values.size, SLAB):
            part = values[start:start + SLAB]
            call(part, slab)
            out[start:start + SLAB] = slab[:part.size]
    return out.reshape(*np.shape(y), 3)


def write_gainmap(sdr: np.ndarray, hdr: np.ndarray, headroom: float, quality: int = 95, gain_quality: int = 100,
                  description: bytes = b"") -> bytes:
    """The bytes of a gain-map JPEG, as udeep.gainmap.write_gainmap_jpeg writes one: `sdr` and `hdr` are float64,
    height x width x 3, already checked for shape and range by the caller. The core holds the file within the
    brightest value of `hdr` on its own decoder, which is also the one read_gainmap uses."""
    dll = _load()
    sdr, hdr = (np.ascontiguousarray(a, dtype=np.float64) for a in (sdr, hdr))
    data, length = ctypes.POINTER(ctypes.c_uint8)(), ctypes.c_size_t()
    status = dll.udeep_gainmap_jpeg(_pointer(sdr), _pointer(hdr), sdr.shape[1], sdr.shape[0], headroom, quality,
                                      gain_quality, description, len(description), ctypes.byref(data),
                                      ctypes.byref(length))
    if status == 9:
        raise ValueError(f"the gain map could not be kept within the headroom at this gain quality ({gain_quality}), "
                         "or a metadata segment is too large for a JPEG. Use gain_quality=100")
    if status == 8:
        raise ValueError("the HDR figure holds a value too large to store as a gain (infinite, or near the largest "
                         "number there is), or the image is too large for a JPEG (65,535 pixels a side)")
    _check(status)
    try:
        return ctypes.string_at(data, length.value)
    finally:
        dll.udeep_bytes_free(data, length.value)


def read_gainmap(data: bytes, display_boost: float, threads: int = 0) -> np.ndarray | None:
    """A gain-map JPEG as a display with `display_boost` of headroom shows it: float32, height x width x 3. None
    when the core's reader does not take the file (not one of this layout, or a gain map of another size than its
    base), for the caller to read it another way."""
    dll = _load()
    width, height = ctypes.c_size_t(), ctypes.c_size_t()
    if dll.udeep_gainmap_size(data, len(data), ctypes.byref(width), ctypes.byref(height)) != 0:
        return None
    out = np.empty((height.value, width.value, 3), dtype=np.float32)
    status = dll.udeep_gainmap_read(data, len(data), display_boost, out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                                      out.size, threads)
    return out if status == 0 else None


def write_from_renders(base: np.ndarray, scaled: np.ndarray, black: np.ndarray, white: np.ndarray, headroom: float,
                       quality: int = 95, gain_quality: int = 100, description: bytes = b"") -> tuple[bytes, int]:
    """A figure's gain-map JPEG straight from four 8-bit renders of it (height x width x 3, uint8): the figure as
    it is; with each HDR image divided by the headroom; and with the HDR images black, and white. Returns the
    bytes and the number of pixels lifted above the ordinary figure. The rule is that of udeep.mpl._compose,
    without the two float images that takes."""
    dll = _load()
    renders = [np.ascontiguousarray(r, dtype=np.uint8) for r in (base, scaled, black, white)]
    if any(r.shape != renders[0].shape for r in renders) or renders[0].ndim != 3 or renders[0].shape[2] != 3:
        raise ValueError("the four renders must all be H x W x 3")
    if not headroom > 1.0:
        raise ValueError("headroom must be > 1")
    pointers = [r.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)) for r in renders]
    data, length, lifted = ctypes.POINTER(ctypes.c_uint8)(), ctypes.c_size_t(), ctypes.c_size_t()
    status = dll.udeep_gainmap_from_renders(*pointers, renders[0].shape[1], renders[0].shape[0], headroom, quality,
                                              gain_quality, description, len(description), ctypes.byref(data),
                                              ctypes.byref(length), ctypes.byref(lifted))
    if status == 9:
        raise ValueError(f"the gain map could not be kept within the headroom at this gain quality ({gain_quality}), "
                         "or a metadata segment is too large for a JPEG. Use gain_quality=100")
    if status == 8:
        raise ValueError("the figure cannot be written as a JPEG (it is too large: 65,535 pixels a side at most)")
    _check(status)
    try:
        return ctypes.string_at(data, length.value), lifted.value
    finally:
        dll.udeep_bytes_free(data, length.value)

