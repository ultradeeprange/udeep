"""Is the fast path worth it? The same work timed in each implementation, from 1 to 64 megapixels.

    python tools/stress.py [largest megapixels, default 64]      # prints tables; takes some minutes and some GB

What is compared:
  the mapping     data -> light, and light -> colour: NumPy (the reference), the Rust core on one thread and on all,
                  and the GPU shader (wgpu), which does position, light and colour in one pass
  a view          hdr_view with float64 and with float32 renderings: time, and memory held and at the peak; and
                  the same view with the Rust core turned on (udeep.core.enable())
  a file          the gain-map JPEG writer in Python and in Rust, on a figure-like image with the fire map
  a whole figure  matplotlib's part against udeep's, at two sizes

Each time is the best of a few runs. The machine does other things meanwhile and has shown slow stretches of up to
about 2.7 times, so read a single number as good to perhaps 30 %; the ratios between columns are steadier.
Needs the Rust core built (cargo build --release --features jpeg in core/); the GPU column needs wgpu.
"""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
import tracemalloc
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from udeep import Mapping, core
from udeep import mpl as hp
from udeep.gainmap import _gainmap_file
from udeep.view import hdr_view

ROOT = Path(__file__).resolve().parents[1]
STRETCH = {"kind": "asinh", "lo": 0.0, "hi": 100.0, "width": 5.0}
DOUBLES = ctypes.POINTER(ctypes.c_double)


class CMapping(ctypes.Structure):
    _fields_ = [("range_low", ctypes.c_double), ("range_high", ctypes.c_double), ("knee", ctypes.c_double),
                ("reference_headroom", ctypes.c_double), ("stretch_width", ctypes.c_double),
                ("stretch_offset", ctypes.c_double), ("exit_slope", ctypes.c_double), ("stretch", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32)]


def rust():
    name = {"win32": "udeep_core.dll", "darwin": "libudeep_core.dylib"}.get(sys.platform, "libudeep_core.so")
    path = ROOT / "core" / "target" / "release" / name
    if not path.exists():
        return None
    dll = ctypes.CDLL(str(path))
    record = ctypes.POINTER(CMapping)
    dll.udeep_light.argtypes = [record, DOUBLES, DOUBLES, ctypes.c_size_t]
    dll.udeep_light_parallel.argtypes = [record, DOUBLES, DOUBLES, ctypes.c_size_t, ctypes.c_size_t]
    dll.udeep_colours.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_double, DOUBLES, DOUBLES,
                                    ctypes.c_size_t]
    dll.udeep_gainmap_jpeg.argtypes = [DOUBLES, DOUBLES, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_double,
                                         ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
                                         ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)), ctypes.POINTER(ctypes.c_size_t)]
    dll.udeep_bytes_free.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]
    dll.udeep_bytes_free.restype = None
    return dll


def best(f, repeat: int) -> float:
    times = []
    for _ in range(repeat):
        t = time.perf_counter(); f(); times.append(time.perf_counter() - t)
    return min(times)


def ptr(a):
    return a.ctypes.data_as(DOUBLES)


def record(m: Mapping) -> CMapping:
    kinds = {"linear": 0, "log": 1, "asinh": 2, "proportional": 3}
    return CMapping(m.range_low, m.range_high, m.knee, m.reference_headroom, m.stretch_width, m.stretch_offset,
                    m.exit_slope, kinds[m.stretch], 0)


def image(megapixels: float, seed: int = 0) -> np.ndarray:
    side = round((megapixels * 1e6) ** 0.5)
    return np.random.default_rng(seed).exponential(10.0, (side, side))


def figure_like(megapixels: float) -> np.ndarray:
    """A smooth bright blob over noise: what the writer's correcting passes have to work on."""
    side = round((megapixels * 1e6) ** 0.5)
    yy, xx = np.mgrid[0:side, 0:side] / side
    blob = np.exp(-(((yy - 0.49) / 0.21) ** 2 + ((xx - 0.52) / 0.21) ** 2))
    noise = np.random.default_rng(1).normal(0.0, 0.10, (side, side))
    return 100.0 * np.clip(0.85 * np.clip(0.1 + 0.9 * blob, 0.0, 1.0) + noise, 0.0, 1.0)


def gpu_run(m: Mapping, x: np.ndarray):
    from udeep import gpu
    flat = x.reshape(-1)
    for start in range(0, flat.size, gpu.MAX_VALUES):
        gpu.run(m, flat[start:start + gpu.MAX_VALUES])


def mapping_table(sizes, dll, threads):
    fire = Mapping.from_stretch(STRETCH, 30.0, 4.0, "fire")
    c = record(fire)
    nodes = np.frombuffer(fire.table.to_bytes(), dtype="<f4").copy()
    table = nodes.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    try:
        gpu_run(fire, image(0.25))                       # builds the pipeline once, outside the timing
        have_gpu = True
    except Exception as err:                             # no adapter, no wgpu: say so, carry on
        print(f"  (no GPU column: {type(err).__name__}: {str(err)[:80]})")
        have_gpu = False
    print(f"\nTHE MAPPING, asinh stretch, fire map, headroom 4. Seconds; megapixels a second in brackets. {threads} threads.\n")
    head = f"  {'MP':>4} | {'NumPy light':>14} {'Rust 1 thr':>14} {'Rust all':>14} | {'NumPy colour':>14} {'Rust colour':>14} | {'GPU, all three':>16}"
    print(head); print("  " + "-" * (len(head) - 2))
    rows = []
    for mp in sizes:
        d = image(mp); n = d.size; repeat = 3 if mp <= 16 else 2
        out = np.empty_like(d); rgb = np.empty((n, 3))
        t_np = best(lambda: fire.light(d), repeat)
        light = fire.light(d)
        t_npc = best(lambda: fire.colours(light), repeat)
        cell = lambda t: f"{t:6.2f} ({n / 1e6 / t:5.0f})" if t else f"{'-':>14}"
        t_r1 = t_ra = t_rc = t_g = 0.0
        if dll is not None:
            t_r1 = best(lambda: dll.udeep_light(ctypes.byref(c), ptr(d), ptr(out), n), repeat)
            single = out.copy()
            t_ra = best(lambda: dll.udeep_light_parallel(ctypes.byref(c), ptr(d), ptr(out), n, 0), repeat)
            assert np.array_equal(out, single), "the threads changed a value"               # the same bits on any count
            assert np.allclose(out, light, rtol=1e-12, atol=1e-12), "the Rust core and NumPy disagree"
            del single
            t_rc = best(lambda: dll.udeep_colours(table, nodes.size // 6, 4.0, ptr(light), ptr(rgb), n), repeat)
        if have_gpu:
            t_g = best(lambda: gpu_run(fire, d), repeat)
        print(f"  {mp:>4g} | {cell(t_np)} {cell(t_r1)} {cell(t_ra)} | {cell(t_npc)} {cell(t_rc)} | {cell(t_g):>16}", flush=True)
        rows.append((mp, t_np, t_r1, t_ra, t_npc, t_rc, t_g))
        del d, out, rgb, light
    return rows


def view_table(sizes):
    print("\nA VIEW (hdr_view, highlight, fire): the mapping, the mask and both renderings. Bytes a pixel from tracemalloc.\n")
    print(f"  {'MP':>4} | {'float64 s':>10} {'peak':>6} {'held':>6} | {'float32 s':>10} {'peak':>6} {'held':>6} | {'with the core, s':>16} {'times faster':>12}")
    rows = []
    for mp in sizes:
        d = image(mp); cells = []
        for float32 in (False, True):
            make = lambda: hdr_view(d, "highlight", 30.0, STRETCH, 4.0, "fire", float32=float32)
            t = best(make, 2 if mp <= 16 else 1)
            tracemalloc.start(); v = make(); held, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
            del v
            cells += [t, peak / d.size, held / d.size]
        fast = 0.0
        if core.available():
            core.enable()
            try:
                fast = best(lambda: hdr_view(d, "highlight", 30.0, STRETCH, 4.0, "fire"), 2 if mp <= 16 else 1)
            finally:
                core.enable(False)
        tail = f"{fast:16.2f} {cells[0] / fast:12.1f}" if fast else f"{'-':>16} {'-':>12}"
        print(f"  {mp:>4g} | {cells[0]:10.2f} {cells[1]:6.0f} {cells[2]:6.0f} | {cells[3]:10.2f} {cells[4]:6.0f} {cells[5]:6.0f} | {tail}", flush=True)
        rows.append((mp, *cells))
    return rows


def writer_table(sizes, dll):
    print("\nA FILE: the gain-map JPEG writer on a figure-like image, fire map, headroom 4 (seconds; correcting passes).\n")
    print(f"  {'MP':>4} | {'Python s':>9} {'passes':>6} | {'Rust s':>8} | {'Python / Rust':>13}")
    rows = []
    for mp in sizes:
        v = hdr_view(figure_like(mp), "extend", 50.0, {"kind": "linear", "lo": 0.0, "hi": 100.0}, 4.0, "fire")
        sdr, hdr = np.ascontiguousarray(v.sdr), np.ascontiguousarray(v.hdr)
        passes = _gainmap_file(sdr, hdr, 4.0)[1]
        t_py = best(lambda: _gainmap_file(sdr, hdr, 4.0), 2 if mp <= 4 else 1)
        t_rs = 0.0
        if dll is not None:
            def write():
                data, length = ctypes.POINTER(ctypes.c_uint8)(), ctypes.c_size_t()
                status = dll.udeep_gainmap_jpeg(ptr(sdr), ptr(hdr), sdr.shape[1], sdr.shape[0], 4.0, 95, 100, b"", 0,
                                                  ctypes.byref(data), ctypes.byref(length))
                assert status == 0, status
                dll.udeep_bytes_free(data, length.value)
            t_rs = best(write, 2 if mp <= 4 else 1)
        ratio = f"{t_py / t_rs:13.1f}" if t_rs else f"{'-':>13}"
        print(f"  {mp:>4g} | {t_py:9.2f} {passes:6d} | {t_rs:8.2f} | {ratio}", flush=True)
        rows.append((mp, t_py, passes, t_rs))
    return rows


def figure_table():
    print("\nA WHOLE FIGURE with matplotlib (1 MP of data, fire): where a save's time goes, in seconds.\n")
    print(f"  {'figure':>12} | {'imshow':>7} {'plain savefig':>13} {'savefig_hdr':>11} | {'of which: 4 renders':>19} {'declaration':>11} {'writer':>7}")
    out = os.path.join(tempfile.mkdtemp(), "stress.jpg")
    d = image(1.0)
    rows = []
    for inches, dpi in ((8, 150), (8, 300), (12, 400)):
        fig, ax = plt.subplots(figsize=(inches, inches), dpi=dpi)
        t0 = time.perf_counter()
        im = hp.imshow(ax, d, mode="highlight", knee=30.0, stretch=STRETCH, headroom=4.0, cmap="fire", unit="counts",
                       knee_rule="preset; x")
        hp.colorbar(im, ax=ax)
        build = time.perf_counter() - t0
        plain = best(lambda: fig.savefig(out[:-4] + ".png", bbox_inches="tight"), 2)
        save = best(lambda: hp.savefig_hdr(fig, out), 2)
        compose = best(lambda: hp._compose(fig, 4.0, True, 0.1), 2)
        declare = best(lambda: hp.declaration(fig), 2)
        base, hdr = hp._compose(fig, 4.0, True, 0.1)
        writer = best(lambda: _gainmap_file(base, hdr, 4.0), 2)
        pixels = base.shape[0] * base.shape[1] / 1e6
        print(f"  {pixels:9.1f} MP | {build:7.2f} {plain:13.2f} {save:11.2f} | {compose:19.2f} {declare:11.2f} {writer:7.2f}", flush=True)
        rows.append((pixels, build, plain, save, compose, declare, writer))
        plt.close(fig)
    return rows


def main() -> None:
    warnings.simplefilter("ignore")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    largest = float(sys.argv[1]) if len(sys.argv) > 1 else 64.0
    sizes = [s for s in (1, 4, 16, 64) if s <= largest]
    dll = rust()
    threads = os.cpu_count() or 1
    print(f"udeep stress run. Rust core: {'found' if dll else 'NOT BUILT, its columns are empty'}.")
    mapping_table(sizes, dll, threads)
    view_table([s for s in sizes if s <= 16] + ([64] if largest >= 64 else []))
    writer_table([s for s in sizes if s <= 16], dll)
    figure_table()


if __name__ == "__main__":
    main()
