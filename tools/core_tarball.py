"""Take the core library out of a built wheel and pack it alone, for the Julia package.

    python tools/core_tarball.py WHEEL [WHEEL ...] --out DIRECTORY

A wheel that carries the core holds it at udeep/_lib/. The Julia package needs the same library and nothing else,
as an archive named for its platform in the form Julia uses (x86_64-linux-gnu, x86_64-w64-mingw32,
aarch64-apple-darwin, x86_64-apple-darwin): udeep_core-<version>-<platform>.tar.gz, holding lib/<library>, and on
x86-64 its x86-64-v3 build beside it (lib/<library with _v3>), which the package loads where the processor has it.
tools/julia_artifacts.jl turns a set of these, once they are at a public address, into the package's
Artifacts.toml.

The archive is the same for the same library: fixed times, owners and order, and no name or time in the gzip
header.
"""
import argparse
import gzip
import io
import re
import tarfile
import zipfile
from pathlib import Path

PLATFORMS = [(r"manylinux.*_x86_64$", "x86_64-linux-gnu"), (r"win_amd64$", "x86_64-w64-mingw32"),
             (r"macosx_.*_arm64$", "aarch64-apple-darwin"), (r"macosx_.*_x86_64$", "x86_64-apple-darwin")]
LIBRARY = re.compile(r"^udeep/_lib/((?:lib)?udeep_core(?:_v3)?\.(?:so|dylib|dll))$")


def platform_of(wheel: Path) -> str:
    tag = wheel.stem.split("-")[-1]
    for pattern, triple in PLATFORMS:
        if re.search(pattern, tag):
            return triple
    raise SystemExit(f"{wheel.name}: no Julia platform is known for the wheel tag {tag!r}")


def pack(wheel: Path, out: Path) -> Path:
    version = wheel.name.split("-")[1]
    with zipfile.ZipFile(wheel) as z:
        found = sorted((m.group(1), z.read(name)) for name in z.namelist() if (m := LIBRARY.match(name)))
    wider = sum("_v3" in name for name, _ in found)
    if len(found) - wider != 1 or wider > 1:
        raise SystemExit(f"{wheel.name}: expected one core library in udeep/_lib/ and at most one x86-64-v3 build "
                         f"of it, found {[name for name, _ in found]}")
    buffer = io.BytesIO()
    entries = [("lib", b"", 0o755, tarfile.DIRTYPE)] + [(f"lib/{name}", data, 0o755, tarfile.REGTYPE) for name, data in found]
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for path, data, mode, kind in entries:
            info = tarfile.TarInfo(path)
            info.type, info.mode, info.size, info.mtime = kind, mode, len(data), 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"udeep_core-{version}-{platform_of(wheel)}.tar.gz"
    with target.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as packed:
        packed.write(buffer.getvalue())
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("wheels", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    for wheel in args.wheels:
        made = pack(wheel, args.out)
        print(made.name, made.stat().st_size, "bytes")
