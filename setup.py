"""Build step for the wheel: the Rust core, built and put inside the package (udeep/_lib).

Everything else about the package is in pyproject.toml. The core is loaded with ctypes, so a wheel that carries it
is for one platform and for any Python 3: its tag is py3-none-<platform>.

UDEEP_BUILD_CORE chooses what happens:
    auto (the default)  build the core if cargo is installed; otherwise make a pure-Python wheel, which uses the
                        NumPy and Pillow implementation
    1                   the core is required: fail if it cannot be built (what the release build sets)
    0                   never build it

An editable install (pip install -e .) does not build the core here: the package then finds the library in
core/target/release, where `cargo build --release --features jpeg` puts it.
"""
import os
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


def build_core() -> Path:
    """Build the core for release and return the library's path."""
    command = [CARGO, "build", "--release", "--locked", "--features", "jpeg", "--manifest-path", str(CORE / "Cargo.toml")]
    subprocess.run(command, check=True)
    target = os.environ.get("CARGO_BUILD_TARGET")                 # a cross build puts its output one level down
    built = CORE / "target" / (target or "") / "release" / LIBRARY
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
