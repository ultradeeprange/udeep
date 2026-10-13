"""tools/core_tarball.py: the core library taken out of a wheel and packed alone for the Julia package."""
import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("core_tarball", ROOT / "tools" / "core_tarball.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def wheel(folder: Path, tag: str, library: str | None, content: bytes = b"\x7fELF the library") -> Path:
    path = folder / f"udeep-0.3.0.dev0-py3-none-{tag}.whl"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("udeep/__init__.py", "")
        if library:
            z.writestr(f"udeep/_lib/{library}", content)
    return path


@pytest.mark.parametrize(("tag", "library", "platform"), [
    ("manylinux_2_28_x86_64", "libudeep_core.so", "x86_64-linux-gnu"),
    ("win_amd64", "udeep_core.dll", "x86_64-w64-mingw32"),
    ("macosx_11_0_arm64", "libudeep_core.dylib", "aarch64-apple-darwin"),
    ("macosx_10_13_x86_64", "libudeep_core.dylib", "x86_64-apple-darwin"),
])
def test_each_platforms_wheel_gives_an_archive_named_as_julia_names_the_platform(tag, library, platform, tmp_path):
    made = tool.pack(wheel(tmp_path, tag, library), tmp_path / "out")
    assert made.name == f"udeep_core-0.3.0.dev0-{platform}.tar.gz"
    with tarfile.open(made) as tar:
        assert tar.getnames() == ["lib", f"lib/{library}"]
        member = tar.getmember(f"lib/{library}")
        assert tar.extractfile(member).read() == b"\x7fELF the library" and member.mode == 0o755 and member.mtime == 0


def test_the_archive_depends_on_the_library_alone(tmp_path):
    a = tool.pack(wheel(tmp_path, "win_amd64", "udeep_core.dll"), tmp_path / "a").read_bytes()
    b = tool.pack(wheel(tmp_path, "win_amd64", "udeep_core.dll"), tmp_path / "b").read_bytes()
    other = tool.pack(wheel(tmp_path, "win_amd64", "udeep_core.dll", b"another build"), tmp_path / "c").read_bytes()
    assert a == b != other
    with tarfile.open(fileobj=io.BytesIO(a)) as tar:
        assert tar.getmember("lib").isdir()


def test_a_wheel_without_the_core_or_for_an_unknown_platform_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="expected one core library"):
        tool.pack(wheel(tmp_path, "win_amd64", None), tmp_path / "out")
    with pytest.raises(SystemExit, match="no Julia platform"):
        tool.pack(wheel(tmp_path, "linux_riscv64", "libudeep_core.so"), tmp_path / "out")
