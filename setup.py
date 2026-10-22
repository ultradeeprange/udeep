"""Build step for the wheel: the Rust core, built and put inside the package (udeep/_lib).

Everything else about the package is in pyproject.toml. The core is loaded with ctypes, so a wheel that carries it
is for one platform and for any Python 3: its tag is py3-none-<platform>.

UDEEP_BUILD_CORE chooses what happens:
    auto (the default)  build the core if cargo is installed; otherwise make a pure-Python wheel, which uses the
                        NumPy and Pillow implementation
    1                   the core is required: fail if it cannot be built (what the release build sets)
    0                   never build it

On x86-64 the core is built twice: for any 64-bit processor, and for x86-64-v3 (AVX2, FMA and the rest: Intel Core
since 2013, AMD since 2015), which the package loads instead where the processor has it (udeep.core). The two give
the same bytes; the second is faster (project/performance-scope.md, 4h), and would stop on an older processor at its
first wider instruction, which is why the first is always there and is asked first.

An editable install (pip install -e .) does not build the core here: the package then finds the library in
core/target/release, where `cargo build --release --features jpeg,png` puts it.
"""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py
from setuptools.dist import Distribution

ROOT = Path(__file__).resolve().parent
CORE = ROOT / "core"
LIBRARY = {"win32": "udeep_core.dll", "darwin": "libudeep_core.dylib"}.get(sys.platform, "libudeep_core.so")
CHOICE = os.environ.get("UDEEP_BUILD_CORE", "auto")
if CHOICE not in ("auto", "0", "1"):
    raise SystemExit(f"UDEEP_BUILD_CORE must be auto, 0 or 1, got {CHOICE!r}")
CARGO = shutil.which("cargo")
if CHOICE == "1" and (CARGO is None or not (CORE / "Cargo.toml").is_file()):
    raise SystemExit("UDEEP_BUILD_CORE=1, but cargo or the core's source (core/Cargo.toml) was not found")
WITH_CORE = CHOICE != "0" and CARGO is not None and (CORE / "Cargo.toml").is_file()
TARGET = os.environ.get("CARGO_BUILD_TARGET")                     # a cross build puts its output one level down
X86_64 = (TARGET or platform.machine()).lower().startswith(("x86_64", "amd64"))
WIDER = LIBRARY.replace("udeep_core", "udeep_core_v3", 1)        # the x86-64-v3 build, as the package carries it


def build_core(cpu: str | None = None) -> Path:
    """Build the core for release and return the library's path: for any processor, or with `cpu` (a target-cpu
    such as "x86-64-v3") into a target directory of its own."""
    command = [CARGO, "build", "--release", "--locked", "--features", "jpeg,png", "--manifest-path", str(CORE / "Cargo.toml")]
    env, target_dir = dict(os.environ), CORE / "target"
    if cpu:
        target_dir = CORE / "target" / cpu
        command += ["--target-dir", str(target_dir)]
        env["RUSTFLAGS"] = f"{env.get('RUSTFLAGS', '')} -C target-cpu={cpu}".strip()
    subprocess.run(command, check=True, env=env)
    built = target_dir / (TARGET or "") / "release" / LIBRARY
    if not built.is_file():
        raise SystemExit(f"cargo ran, but {built} is not there")
    return built


class BuildPy(build_py):
    def run(self):
        super().run()
        if WITH_CORE and not self.editable_mode:
            folder = Path(self.build_lib) / "udeep" / "_lib"
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(build_core(), folder / LIBRARY)
            if X86_64:
                shutil.copy2(build_core("x86-64-v3"), folder / WIDER)


class Wheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        if WITH_CORE:
            self.root_is_pure = False

    def get_tag(self):
        python, abi, platform = super().get_tag()
        return ("py3", "none", platform) if WITH_CORE else (python, abi, platform)


class WithLibrary(Distribution):
    def has_ext_modules(self):                                    # so that the files go to the platform's directory
        return WITH_CORE


setup(cmdclass={"build_py": BuildPy, "bdist_wheel": Wheel}, distclass=WithLibrary)
