"""The speed rig: each faster way of doing the work, timed against the plain way on the same input, with its result
checked to be the same.

    python tools/speed_rig.py all [--python CMD ...] [--quick] [--json FILE]     every section, then a summary
    python tools/speed_rig.py numpy [--sizes 1,4,16]          the NumPy code (no core) on 1 thread and on every core
    python tools/speed_rig.py core                            the Rust core as the wheels carry it, built for
                                                              x86-64-v3, and with the JPEG encoder's own SIMD
    python tools/speed_rig.py wasm                            the core as WebAssembly, without and with SIMD
                                                              (simd128), and the JavaScript port, in node

The sections:

  numpy   The mapping (data to light), the colours (light to RGB through the fire map) and a whole view (hdr_view),
          with the core turned off, so on NumPy alone: udeep.core.THREADS = 1 against every core. Run under several
          Pythons with --python (a command each, such as "py -3.14t" for the free-threaded build): each gets an
          environment of its own with NumPy and Pillow, and runs this checkout's source. The result must be the
          same, value for value, on any number of threads.
  core    The core three ways (section_core), each loaded in a process of its own: light and colours for 16 megapixels on
          1 thread and on all, a 4-megapixel gain-map JPEG and a 16-bit PNG written. The builds must give the same
          bytes: Rust does not fuse a multiply and an add into one rounding unless asked, so a wider instruction
          set changes how fast, not what.
  wasm    The browser's numbers: position, light and colours for each pixel, and the live view's held pixels
          (the position of every pixel, then the isolated-3x3 rule), in the WebAssembly core built without and
          with simd128, and in the JavaScript port as the live view uses it, one value at a time. The two
          WebAssembly builds must give the same bytes; the port must agree to 1e-12.

Every time is the median of several runs, with the fastest and the spread beside it. Other work on the machine
makes single runs vary, by up to a few times on the development machine; read differences under about 10 % as
none. Needs cargo (with the wasm32-unknown-unknown target for the wasm section) and node.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
RIG = Path(tempfile.gettempdir()) / "udeep-speed-rig"                # environments and builds, kept between runs
STRETCH = {"kind": "asinh", "lo": 0.0, "hi": 100.0, "width": 5.0}
LIBRARY = {"win32": "udeep_core.dll", "darwin": "libudeep_core.dylib"}.get(sys.platform, "libudeep_core.so")
ROUNDS = 3                                    # core section: each build's process run this often, in turn


def stats(times: list[float]) -> dict:
    """The median, the fastest, and the spread between fastest and slowest, as a fraction of the median."""
    median = statistics.median(times)
    return {"median_s": round(median, 4), "best_s": round(min(times), 4),
            "spread": round((max(times) - min(times)) / median, 2) if median else 0.0}


def timed(function, repeat: int) -> dict:
    """Run `function` `repeat` times, and give stats() of the times."""
    times = []
    for _ in range(repeat):
        started = time.perf_counter()
        function()
        times.append(time.perf_counter() - started)
    return stats(times)


def interleaved(variants: dict, repeat: int) -> dict:
    """Time each of `variants` ({name: (set up, function)}) `repeat` times, taking them in turn within each round,
    so that a slow stretch of the machine falls on all of them alike. Returns {name: stats()}."""
    times = {name: [] for name in variants}
    for _ in range(repeat):
        for name, (set_up, function) in variants.items():
            set_up()
            started = time.perf_counter()
            function()
            times[name].append(time.perf_counter() - started)
    return {name: stats(t) for name, t in times.items()}


def machine() -> dict:
    return {"platform": platform.platform(), "processor": platform.processor() or platform.machine(),
            "cores": os.process_cpu_count() if hasattr(os, "process_cpu_count") else os.cpu_count()}


# ---- numpy: the package's NumPy code, on one thread and on every core

def section_numpy(sizes: list[float], repeat: int) -> list[dict]:
    import tracemalloc

    import numpy as np

    from udeep import Mapping, core
    from udeep.view import hdr_view
    core.enable(False)
    m = Mapping.from_stretch(STRETCH, 40.0, 4.0, "fire")
    gil = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
    where = {"python": platform.python_version(), "free_threaded": not gil, "numpy": np.__version__}
    rows = []
    for mp in sizes:
        n = int(mp * 1_000_000)
        side = round(n ** 0.5)
        x = np.random.default_rng(1).exponential(20.0, n)
        image = x[:side * side].reshape(side, side)
        y = m.light(x)
        work = {"light": lambda x=x: m.light(x), "colours": lambda y=y: m.colours(y),
                "view": lambda image=image: hdr_view(image, "extend", 40.0, STRETCH, 4.0, "fire").hdr}
        cores = os.process_cpu_count() or 1
        settings = {"1 thread": 1, **{f"{k} threads": k for k in (4, 8) if k < cores}, f"{cores} threads": cores,
                    "default": None}
        for name, function in work.items():
            results, peaks, counts = {}, {}, {}
            for label, setting in settings.items():
                core.THREADS = setting
                counts[label] = core.threads()
                results[label] = function()                                       # once, also to warm up
                tracemalloc.start()
                function()
                peaks[label] = tracemalloc.get_traced_memory()[1]
                tracemalloc.stop()
            times = interleaved({label: (lambda setting=setting: setattr(core, "THREADS", setting), function)
                                 for label, setting in settings.items()}, repeat)
            same = all(np.array_equal(results["1 thread"], r, equal_nan=True) for r in results.values())
            for label in settings:
                rows.append({"section": "numpy", **where, "work": name, "megapixels": mp, "threads": label,
                             "thread_count": counts[label], **times[label], "peak_MB": round(peaks[label] / 2**20, 1),
                             "same_result": same})
            core.THREADS = None
    return rows


def environment(command: str) -> Path:
    """A virtual environment of its own for a Python, with NumPy and Pillow: made once, kept in RIG."""
    slug = "".join(c if c.isalnum() else "-" for c in command)
    env = RIG / f"env-{slug}"
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        subprocess.run([*shlex.split(command), "-m", "venv", str(env)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check", "numpy", "pillow"],
                       check=True)
    return python


def numpy_under(command: str, sizes: list[float], repeat: int) -> list[dict]:
    python = environment(command)
    out = RIG / f"numpy-{os.getpid()}.json"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "UDEEP_USE_CORE": "0"}
    subprocess.run([str(python), __file__, "numpy", "--sizes", ",".join(map(str, sizes)), "--repeat", str(repeat),
                    "--json", str(out)], check=True, env=env, cwd=RIG)
    return json.loads(out.read_text(encoding="utf-8"))


# ---- core: the Rust core built for three instruction sets

def cpu_has(feature: str) -> bool:
    """Whether this processor has an x86 feature: avx2 or avx512f."""
    if platform.machine().lower() not in ("amd64", "x86_64"):
        return False
    if sys.platform == "win32":
        code = {"avx2": 40, "avx512f": 41}[feature]          # PF_AVX2_INSTRUCTIONS_AVAILABLE, PF_AVX512F_...
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(code))
    if sys.platform.startswith("linux"):
        return f" {feature} " in Path("/proc/cpuinfo").read_text().replace("\n", " ")
    flags = subprocess.run(["sysctl", "-n", "machdep.cpu.leaf7_features"], capture_output=True, text=True,
                           check=False).stdout
    return feature.upper() in flags.upper()


def build(name: str, rustflags: str, wasm: bool = False, features: str = "jpeg,png") -> Path:
    target = RIG / "target" / name
    env = {**os.environ, "RUSTFLAGS": rustflags}
    command = ["cargo", "build", "--release", "--features", features, "--manifest-path", str(CORE / "Cargo.toml"),
               "--target-dir", str(target)]
    if wasm:
        command += ["--target", "wasm32-unknown-unknown"]
    subprocess.run(command, check=True, env=env, capture_output=True)
    return target / ("wasm32-unknown-unknown/release/udeep_core.wasm" if wasm else f"release/{LIBRARY}")


def section_core_one(library: Path, megapixels: float, repeat: int) -> list[dict]:
    """In a process of its own: the core at `library`, timed and its outputs digested."""
    os.environ["UDEEP_CORE_LIB"] = str(library)
    import numpy as np

    from udeep import Mapping, core, gainmap, write_png_pq
    from udeep.view import hdr_view
    assert core.enabled() and core.library_path() == library, core.library_path()
    m = Mapping.from_stretch(STRETCH, 40.0, 4.0, "fire")
    n = int(megapixels * 1_000_000)
    x = np.random.default_rng(1).exponential(20.0, n)
    y = core.light(m, x)
    side = 2000
    view = hdr_view(x[:side * side].reshape(side, side), "extend", 40.0, STRETCH, 4.0, "fire")
    digest = lambda a: hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]
    rows = []
    folder = Path(tempfile.mkdtemp(dir=RIG))
    work = {"light, 1 thread": (lambda: core.light(m, x, threads=1), lambda r: digest(r)),
            "light, every core": (lambda: core.light(m, x, threads=0), lambda r: digest(r)),
            "colours, 1 thread": (lambda: core.colours(m, y, threads=1), lambda r: digest(r)),
            "colours, every core": (lambda: core.colours(m, y, threads=0), lambda r: digest(r)),
            "gain-map JPEG, 4 MP": (lambda: gainmap.write_gainmap_jpeg(view.sdr, view.hdr, folder / "f.jpg", 4.0),
                                    lambda r: hashlib.sha256((folder / "f.jpg").read_bytes()).hexdigest()[:16]),
            "16-bit PNG, 4 MP": (lambda: write_png_pq(view.hdr, folder / "f.png"),
                                 lambda r: hashlib.sha256((folder / "f.png").read_bytes()).hexdigest()[:16])}
    for name, (function, digest_of) in work.items():
        result = function()
        rows.append({"section": "core", "work": name, "megapixels": megapixels if "MP" not in name else 4.0,
                     **timed(function, repeat), "digest": digest_of(result)})
    return rows


def section_core(megapixels: float, repeat: int) -> list[dict]:
    """The core three ways: as the wheels carry it, for any 64-bit processor; the whole core built for x86-64-v3
    (AVX2 and FMA: the most that instruction set gives, for comparison, since a wheel built so would not run on an
    older processor); and with the JPEG encoder's own SIMD (its optional `simd` feature), which chooses its
    instructions when it runs. On 22 October 2026 choosing the core's own loops at run time was tried as well
    (core/src/fast.rs, since removed): it gained at most 5 %, within the noise, against up to 16 % for the whole
    build, so it was not kept."""
    variants = [("as the wheels carry it", "baseline", "", "jpeg,png", {})]
    if cpu_has("avx2"):
        variants.append(("built for x86-64-v3", "native-x86-64-v3", "-C target-cpu=x86-64-v3", "jpeg,png", {}))
    if cpu_has("avx512f"):
        variants.append(("built for x86-64-v4", "native-x86-64-v4", "-C target-cpu=x86-64-v4", "jpeg,png", {}))
    variants.append(("+ JPEG encoder SIMD", "baseline-jpeg-simd", "", "jpeg,png,jpeg-encoder/simd", {}))
    libraries = {name: build(target, flags, features=features) for name, target, flags, features, _ in variants}
    runs = {name: [] for name in libraries}
    for round_ in range(ROUNDS):            # the variants in turn, round after round, so a slow stretch falls on all
        for name, _, _, _, run_env in variants:
            out = RIG / f"core-{round_}.json"
            env = {**{k: v for k, v in os.environ.items() if k != "UDEEP_CORE_LIB"}, **run_env}
            subprocess.run([sys.executable, __file__, "core-one", "--library", str(libraries[name]), "--sizes",
                            str(megapixels), "--repeat", str(repeat), "--json", str(out)], check=True, env=env, cwd=RIG)
            runs[name].append(json.loads(out.read_text(encoding="utf-8")))
    rows = []
    for name, rounds in runs.items():
        for i, first in enumerate(rounds[0]):
            medians = [r[i]["median_s"] for r in rounds]
            rows.append({**first, "build": name, "median_s": round(statistics.median(medians), 4),
                         "best_s": min(r[i]["best_s"] for r in rounds),
                         "spread": round((max(medians) - min(medians)) / statistics.median(medians), 2),
                         "same_result": all(r[i]["digest"] == first["digest"] for r in rounds)})
    base = {r["work"]: r["digest"] for r in rows if r["build"] == "as the wheels carry it"}
    for r in rows:
        r["same_result"] = r["same_result"] and r["digest"] == base[r["work"]]
    return rows


# ---- wasm: the browser's numbers

NODE = r"""
import { readFileSync } from 'node:fs';
const [ , , bindings, port, baseline, simd, tableFile, sizesText, repeatText ] = process.argv;
await import(bindings); await import(port);
const W = globalThis.UDeepWasm, P = globalThis.UDeepCore;
const sizes = sizesText.split(',').map(Number), repeat = Number(repeatText);
const m = { stretch: 'asinh', range_low: 0, range_high: 100, knee: 40, reference_headroom: 4, stretch_width: 5,
  stretch_offset: 0, above_knee: 'hermite; exit=1', colour_map: 'fire/3' };
const builds = { baseline: await W.load(readFileSync(baseline)), simd128: await W.load(readFileSync(simd)) };
const tableBytes = readFileSync(tableFile);
const wasmTable = W.makeTable(tableBytes, 4), portTable = P.makeTable(tableBytes, 4);
const same = (a, b) => a.length === b.length && Buffer.from(a.buffer, a.byteOffset, a.byteLength).equals(Buffer.from(b.buffer, b.byteOffset, b.byteLength));
const close = (a, b) => { let worst = 0; for (let i = 0; i < a.length; i++) { const d = Math.abs(a[i] - b[i]) / Math.max(1, Math.abs(b[i])); if (d > worst || Number.isNaN(d)) worst = Number.isNaN(d) ? (Number.isNaN(a[i]) && Number.isNaN(b[i]) ? worst : Infinity) : d; } return worst; };
const rows = [];
let seed = 1; const rand = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
for (const mp of sizes) {
  const n = Math.round(mp * 1e6), side = Math.round(Math.sqrt(n));
  const x = new Float64Array(n); for (let i = 0; i < n; i++) x[i] = -20 * Math.log(1 - rand());
  const kneeT = P.kneePosition(m);
  const portWork = {
    position: () => Float64Array.from(x, v => P.position(m, v)),
    light: () => Float64Array.from(x, v => P.light(m, v)),
    colours: () => { const out = new Float64Array(3 * n); for (let i = 0; i < n; i++) { const c = P.cmap(x[i] / 25, portTable); out[3 * i] = c[0]; out[3 * i + 1] = c[1]; out[3 * i + 2] = c[2]; } return out; },
    'held pixels': () => { const rows_ = []; for (let j = 0; j < side; j++) { const r = new Array(side); for (let i = 0; i < side; i++) r[i] = P.position(m, x[j * side + i]) > kneeT; rows_.push(r); }
      const lone = P.isolated(rows_), out = new Uint8Array(side * side); lone.forEach((r, j) => r.forEach((v, i) => { out[j * side + i] = v ? 1 : 0; })); return out; },
  };
  const wasmWork = core => ({
    position: () => core.position(m, x),
    light: () => core.light(m, x),
    colours: () => core.colours(x.map(v => v / 25), wasmTable),
    'held pixels': () => { const t = core.position(m, x.subarray(0, side * side)), above = new Uint8Array(side * side);
      for (let i = 0; i < above.length; i++) above[i] = t[i] > kneeT ? 1 : 0; return core.isolated(above, side, side); },
  });
  // every way in turn within each round, so that a slow stretch of the machine falls on all of them alike
  const ways = { 'JavaScript port': portWork, ...Object.fromEntries(Object.entries(builds).map(([b, c]) => [b, wasmWork(c)])) };
  const results = {}, times = {};
  for (let round = 0; round < repeat; round++) for (const [build, work] of Object.entries(ways)) for (const [name, f] of Object.entries(work)) {
    const s = performance.now(); const r = f(); const took = performance.now() - s;
    (times[build + '|' + name] ??= []).push(took); results[(build === 'JavaScript port' ? 'port' : build) + ' ' + name] = r; }
  for (const [key, t] of Object.entries(times)) { const [build, name] = key.split('|'); t.sort((a, b) => a - b);
    const med = t[Math.floor(t.length / 2)];
    rows.push({ section: 'wasm', build, work: name, megapixels: mp, median_s: +(med / 1000).toFixed(4), best_s: +(t[0] / 1000).toFixed(4),
      spread: med ? +((t.at(-1) - t[0]) / med).toFixed(2) : 0 }); }
  for (const row of rows.filter(r => r.megapixels === mp)) {
    if (row.build === 'simd128') row.same_result = same(results['simd128 ' + row.work], results['baseline ' + row.work]);
    if (row.build === 'baseline') row.same_result = true;
    if (row.build === 'JavaScript port') { const d = close(results['port ' + row.work], results['baseline ' + row.work]);
      row.largest_difference = d; row.same_result = d <= 1e-12; }
  }
}
console.log(JSON.stringify(rows));
"""


def section_wasm(sizes: list[float], repeat: int) -> list[dict]:
    baseline = build("wasm-baseline", "", wasm=True)
    simd = build("wasm-simd128", "-C target-feature=+simd128", wasm=True)
    script = RIG / "wasm_rig.mjs"
    script.write_text(NODE, encoding="utf-8")
    js = ROOT / "src" / "udeep" / "js"
    ran = subprocess.run(["node", str(script), (js / "udeepwasm.js").as_uri(), (js / "udeepcore.js").as_uri(), str(baseline),
                          str(simd), str(ROOT / "src" / "udeep" / "tables" / "fire-3-h4.f32"),
                          ",".join(map(str, sizes)), str(repeat)], capture_output=True, text=True, check=True)
    rows = json.loads(ran.stdout)
    for row in rows:
        row["node"] = subprocess.run(["node", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    return rows


# ---- the summary

def summary(rows: list[dict]) -> str:
    lines = []
    numpy = [r for r in rows if r["section"] == "numpy"]
    if numpy:
        labels = list(dict.fromkeys(r["threads"] for r in numpy))
        lines += ["## NumPy code, by number of threads (the core turned off)", "",
                  "| Python | work | MP | " + " | ".join(labels) + " | peak memory, 1 thread / default | same result |",
                  "|---|---|---|" + "---|" * (len(labels) + 2)]
        for key in dict.fromkeys((r["python"], r["free_threaded"], r["work"], r["megapixels"]) for r in numpy):
            cells = {r["threads"]: r for r in numpy if (r["python"], r["free_threaded"], r["work"], r["megapixels"]) == key}
            one = cells["1 thread"]
            text = [f"{one['median_s']} s"] + [f"{c['median_s']} s ({one['median_s'] / c['median_s']:.1f}x)"
                                               for label, c in cells.items() if label != "1 thread"]
            py = key[0] + ("t, free-threaded" if key[1] else "")
            lines.append(f"| {py} | {key[2]} | {key[3]} | " + " | ".join(text) + f" | {one['peak_MB']} / "
                         f"{cells['default']['peak_MB']} MB | {'yes' if one['same_result'] else 'NO'} |")
    core = [r for r in rows if r["section"] == "core"]
    if core:
        builds = list(dict.fromkeys(r["build"] for r in core))
        lines += ["", ("## Rust core, by instruction set (the fastest of every run, which other work on the "
                       "machine disturbs least)"), "",
                  "| work | " + " | ".join(builds) + " | same bytes |", "|---|" + "---|" * (len(builds) + 1)]
        for work in dict.fromkeys(r["work"] for r in core):
            cells = [r for r in core if r["work"] == work]
            base = next(r for r in cells if r["build"] == "as the wheels carry it")["best_s"]
            text = [f"{r['best_s']} s" + ("" if r["build"] == "as the wheels carry it"
                                           else f" ({base / r['best_s']:.2f}x)") for r in cells]
            lines.append(f"| {work} | " + " | ".join(text) + f" | {'yes' if all(r['same_result'] for r in cells) else 'NO'} |")
    wasm = [r for r in rows if r["section"] == "wasm"]
    if wasm:
        lines += ["", "## Browser: the JavaScript port and the WebAssembly core", "",
                  "| work | MP | JavaScript port | WebAssembly | with simd128 | same result |", "|---|---|---|---|---|---|"]
        for work in dict.fromkeys(r["work"] for r in wasm):
            for mp in dict.fromkeys(r["megapixels"] for r in wasm):
                cell = {r["build"]: r for r in wasm if r["work"] == work and r["megapixels"] == mp}
                port, base, simd = cell["JavaScript port"], cell["baseline"], cell["simd128"]
                ok = port["same_result"] and simd["same_result"]
                lines.append(f"| {work} | {mp} | {port['median_s']} s | {base['median_s']} s "
                             f"({port['median_s'] / base['median_s']:.1f}x) | {simd['median_s']} s "
                             f"({base['median_s'] / simd['median_s']:.2f}x) | {'yes' if ok else 'NO'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("section", choices=["all", "numpy", "core", "core-one", "wasm"])
    parser.add_argument("--python", action="append", default=[], help="another Python for the numpy section")
    parser.add_argument("--sizes", help="megapixels, comma-separated")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--quick", action="store_true", help="smaller sizes and fewer runs")
    parser.add_argument("--library", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    RIG.mkdir(parents=True, exist_ok=True)
    sizes = [float(s) for s in args.sizes.split(",")] if args.sizes else None
    repeat = 3 if args.quick else args.repeat

    if args.section == "numpy":
        rows = section_numpy(sizes or ([1, 4] if args.quick else [1, 4, 16]), repeat)
    elif args.section == "core-one":
        rows = section_core_one(args.library, (sizes or [16])[0], repeat)
    elif args.section == "core":
        rows = section_core((sizes or [4 if args.quick else 16])[0], repeat)
    elif args.section == "wasm":
        rows = section_wasm(sizes or ([0.26, 1] if args.quick else [0.26, 1, 4]), repeat)
    else:
        rows = []
        for command in [shlex.join([sys.executable])] + args.python:
            print(f"numpy section under {command} ...", flush=True)
            rows += numpy_under(command, sizes or ([1, 4] if args.quick else [1, 4, 16]), repeat)
        print("core section ...", flush=True)
        rows += section_core(4 if args.quick else 16, repeat)
        print("wasm section ...", flush=True)
        rows += section_wasm([0.26, 1] if args.quick else [0.26, 1, 4], repeat)
    result = {"machine": machine(), "date": time.strftime("%Y-%m-%d"), "rows": rows}
    if args.json:
        args.json.write_text(json.dumps(rows if args.section in ("numpy", "core-one") else result, indent=1),
                             encoding="utf-8")
    if args.section not in ("numpy", "core-one"):
        print(summary(rows))
    bad = [r for r in rows if r.get("same_result") is False]
    if bad:
        print(f"{len(bad)} result(s) differ from the plain way's: {[(r.get('build'), r['work']) for r in bad]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
