"""Does udeep work on this machine?

    python -m udeep.selftest                 one line for each step, then a verdict; exit status 1 if a step failed
    python -m udeep.selftest --json FILE     the same, also written to FILE
    python -m udeep.selftest --expect-core   fail unless the Rust core is in use (a platform wheel should carry it)
    python -m udeep.selftest --keep DIR      keep the files it writes, in DIR

It runs the README's quick start on made-up data in a temporary folder: a figure with matplotlib, saved as the
ordinary PNG and as an HDR gain-map JPEG, checked against the profile by the validator, as a library call and as
the command line. Then a 16-bit PQ PNG data record, written and read back as data. Nothing is written outside the
temporary folder and nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np

import udeep
from udeep import core


def phantom(size: int = 160) -> np.ndarray:
    """A synthetic CT slice in HU: air, a soft-tissue ellipse with 6 HU of noise, two faint lesions, a bone ring."""
    rng = np.random.default_rng(7)
    y, x = np.mgrid[-1.0:1.0:size * 1j, -1.0:1.0:size * 1j]
    r = np.hypot(x / 0.9, y / 0.75)
    ct = np.where(r < 1.0, 40.0, -1000.0) + np.where(r < 1.0, rng.normal(0.0, 6.0, r.shape), 0.0)
    for cx, cy in ((-0.35, 0.2), (0.4, -0.15)):
        ct[np.hypot(x - cx, y - cy) < 0.08] += 25.0
    ring = np.hypot(x, y)
    ct[(ring > 0.35) & (ring < 0.45)] = 1200.0 + 600.0 * np.cos(8.0 * np.arctan2(y, x))[(ring > 0.35) & (ring < 0.45)] ** 2
    return ct


def step_figure(folder: Path) -> str:
    """The README's quick start, with the figure checked by the library call and by the command line."""
    try:
        import matplotlib
    except ImportError:
        raise Skipped('matplotlib is not installed: pip install "udeep[mpl]"') from None
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    import udeep.mpl as hp
    from udeep.validate import main
    ct = phantom()
    fig, ax = plt.subplots(figsize=(4.0, 3.2))
    img = hp.imshow(ax, ct, mode="extend", knee=240, stretch={"kind": "linear", "lo": -160, "hi": 1850}, cmap="grey",
                    headroom=4, unit="HU", knee_rule="preset; top of the soft-tissue window",
                    source="udeep.selftest: synthetic phantom")
    hp.colorbar(img, ax=ax, label="HU")
    png, jpg = folder / "figure.png", folder / "figure.jpg"
    fig.savefig(png, bbox_inches="tight", pad_inches=0.1)
    hp.savefig_hdr(fig, jpg)
    report = hp.validate(fig, jpg)
    plt.close(fig)
    if not report.ok:
        raise Failed(f"the figure's file does not pass the profile's checks:\n{report}")
    if udeep.read_metadata(jpg).data_unit != "HU":
        raise Failed("the file does not declare what was drawn")
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):                     # its full report; shown only if it fails
        status = main([str(jpg), "--sdr", str(png)])
    if status != 0:
        raise Failed("python -m udeep.validate does not accept the file with its SDR figure:\n" + printed.getvalue())
    return f"figure.png and figure.jpg written; the file passes the profile's checks ({jpg.stat().st_size:,} bytes)"


def step_record(folder: Path) -> str:
    """A 16-bit PQ PNG that holds the data, with the fire colour map, read back as data."""
    from udeep import Declaration, hdr_view, read_png_data, to_xmp, write_png_pq
    from udeep.metadata import data_steps_pq16, digest_data, digest_sdr, rounding_to_noise
    data = np.round(np.random.default_rng(4).uniform(0.0, 4095.0, (40, 52)))
    data[:5, :] = np.random.default_rng(5).uniform(0.0, 3.0, (5, 52))           # faint values, just above black
    view = hdr_view(data, "extend", 1000.0, {"kind": "linear", "lo": 0.0, "hi": 4095.0}, 4.0, "fire")
    steps = data_steps_pq16(view.mapping)
    against = rounding_to_noise(view.mapping, ("constant", 3.0), steps=steps)
    declared = Declaration(**dataclasses.asdict(view.mapping), data_unit="counts",
                           knee_rule="preset; white at 1,000 counts", noise_model="constant", noise_parameter=3.0,
                           rounding_to_noise=against["worst"], max_data_step=float(steps[1].max()),
                           record="data-record", data_digest=digest_data(data), sdr_digest=digest_sdr(view.sdr))
    path = folder / "record.png"
    write_png_pq(view.hdr, path, xmp=to_xmp(declared))
    back, _ = read_png_data(path)
    worst = float(np.max(np.abs(back - data)))
    if not worst <= declared.max_data_step / 2 * 1.01:
        raise Failed(f"the data comes back to within {worst:.3g}, not half the widest step "
                     f"({declared.max_data_step / 2:.3g})")
    return f"record.png read back as data to within {worst:.3g} counts (half its widest step is " \
           f"{declared.max_data_step / 2:.3g})"


class Skipped(Exception):
    """A step that cannot run here, which is not a fault (matplotlib not installed)."""


class Failed(Exception):
    """A step that ran and gave the wrong result."""


def describe_core() -> str:
    """Which implementation is in use, in words: the Rust core library (and which of its builds), or the NumPy and
    Pillow code."""
    if core.enabled():
        return f"the Rust core library, {core.loaded().name}, built for {core.build()}"
    return "the NumPy and Pillow code: no core library for this platform, which works, more slowly"


def run(keep: Path | None = None, expect_core: bool = False) -> dict:
    """Run every step and return the result: the versions and platform, which implementation was used, and each
    step's name, status ("pass", "fail" or "skipped"), detail and time. `keep` keeps the files written there;
    `expect_core` makes it a failure that the Rust core is not in use."""
    result = {"udeep": getattr(udeep, "__version__", "unknown"), "python": platform.python_version(),
              "free_threaded": core.free_threaded(), "threads": core.threads(),
              "platform": platform.platform(), "machine": platform.machine(), "package": str(Path(udeep.__file__).parent),
              "core": core.enabled(), "core_build": core.build() if core.enabled() else None,
              "implementation": describe_core(), "steps": []}
    if expect_core and not core.enabled():
        result["steps"].append({"name": "core", "status": "fail", "detail": "the Rust core was expected but is not in use"})
    folder_context = tempfile.TemporaryDirectory() if keep is None else None
    folder = Path(folder_context.name) if folder_context else keep
    folder.mkdir(parents=True, exist_ok=True)
    try:
        for name, step in (("figure", step_figure), ("data record", step_record)):
            started = time.perf_counter()
            try:
                detail, status = step(folder), "pass"
            except Skipped as why:
                detail, status = str(why), "skipped"
            except Failed as why:
                detail, status = str(why), "fail"
            except Exception:                                                     # noqa: BLE001  report, then fail
                detail, status = traceback.format_exc(), "fail"
            result["steps"].append({"name": name, "status": status, "detail": detail,
                                    "seconds": round(time.perf_counter() - started, 2)})
    finally:
        if folder_context:
            folder_context.cleanup()
    result["ok"] = all(s["status"] != "fail" for s in result["steps"])
    return result


def main(argv: list[str] | None = None) -> int:
    """The command line, `python -m udeep.selftest`: prints the result and returns the exit status, 0 if every step
    passed and 1 if one failed."""
    parser = argparse.ArgumentParser(prog="python -m udeep.selftest", description="Does udeep work on this machine?")
    parser.add_argument("--json", type=Path, help="also write the result to this file")
    parser.add_argument("--keep", type=Path, help="keep the files written, in this folder")
    parser.add_argument("--expect-core", action="store_true", help="fail unless the Rust core is in use")
    args = parser.parse_args(argv)
    result = run(args.keep, args.expect_core)
    python = result["python"] + (" free-threaded" if result["free_threaded"] else "")
    print(f"udeep {result['udeep']} on Python {python}, {result['platform']} ({result['machine']})")
    print(f"  threads     {result['threads']} for the NumPy code" + (", and every core for the Rust core"
                                                                       if result["core"] else ""))
    print(f"  using       {result['implementation']}")
    for s in result["steps"]:
        seconds = f" ({s['seconds']} s)" if "seconds" in s else ""
        print(f"  {s['name']:<12}{s['status']}: {s['detail']}{seconds}")
    print("every step passed" if result["ok"] else "a step FAILED: please report it, with this output, at "
          "https://github.com/ultradeeprange/udeep/issues")
    if args.json:
        args.json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
