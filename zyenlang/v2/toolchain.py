from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import hashlib

from .ir import IRNativeLink


def find_c_compiler() -> list[str]:
    override = os.environ.get("ZY_CC", "").strip()
    if override:
        return shlex.split(override, posix=not sys.platform.startswith("win"))

    roots = [Path(__file__).resolve().parents[2]]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
    executable = "zig.exe" if sys.platform.startswith("win") else "zig"
    for root in roots:
        for candidate in (root / "toolchain" / executable, root / "toolchain" / "zig" / executable):
            if candidate.is_file():
                return [str(candidate), "cc"]

    for name in ("gcc", "clang", "cc"):
        found = shutil.which(name)
        if found:
            return [found]
    raise FileNotFoundError("no C compiler found; use a portable release or set ZY_CC")


def find_archiver() -> list[str]:
    override = os.environ.get("ZY_AR", "").strip()
    if override:
        return shlex.split(override, posix=not sys.platform.startswith("win"))
    compiler = find_c_compiler()
    executable = Path(compiler[0]).name.lower()
    if executable in {"zig", "zig.exe"}:
        return [compiler[0], "ar"]
    found = shutil.which("ar")
    if found:
        return [found]
    raise FileNotFoundError("no static-library archiver found; install ar, use Zig, or set ZY_AR")


def _platform_name() -> str:
    return "windows" if sys.platform.startswith("win") else "macos" if sys.platform == "darwin" else "linux"


def _compile_flags(
    *,
    release: bool,
    position_independent: bool = False,
    extra: tuple[str, ...] = (),
) -> list[str]:
    flags = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-O2" if release else "-O0"]
    if position_independent and not sys.platform.startswith("win"):
        flags.append("-fPIC")
    configured = os.environ.get("ZY_CFLAGS", "").strip()
    if configured:
        flags.extend(shlex.split(configured, posix=not sys.platform.startswith("win")))
    flags.extend(extra)
    return flags


def _link_flags(
    native_links: tuple[IRNativeLink, ...],
    *,
    lib_dirs: tuple[Path, ...] = (),
    extra: tuple[str, ...] = (),
) -> list[str]:
    platform = _platform_name()
    flags: list[str] = []
    for directory in lib_dirs:
        flags.extend(["-L", str(directory)])
    flags.extend(f"-l{link.library}" for link in native_links if link.platform in {"all", platform})
    configured = os.environ.get("ZY_LDFLAGS", "").strip()
    if configured:
        flags.extend(shlex.split(configured, posix=not sys.platform.startswith("win")))
    flags.extend(extra)
    return flags


def compile_objects(
    sources: tuple[Path, ...],
    object_dir: Path,
    *,
    release: bool = False,
    include_dirs: tuple[Path, ...] = (),
    position_independent: bool = False,
    cflags: tuple[str, ...] = (),
) -> tuple[Path, ...]:
    object_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = Path(__file__).resolve().parent / "runtime"
    includes = [runtime_dir, *include_dirs]
    objects: list[Path] = []
    for source in sources:
        identity = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:12]
        output = object_dir / f"{source.stem}-{identity}.o"
        command = find_c_compiler() + _compile_flags(
            release=release,
            position_independent=position_independent,
            extra=cflags,
        )
        for include in includes:
            command.extend(["-I", str(include)])
        command.extend(["-c", str(source), "-o", str(output)])
        subprocess.run(command, check=True)
        objects.append(output)
    return tuple(objects)


def create_static_library(objects: tuple[Path, ...], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(find_archiver() + ["rcs", str(output), *(str(item) for item in objects)], check=True)


def create_shared_library(
    sources: tuple[Path, ...],
    output: Path,
    *,
    release: bool = False,
    include_dirs: tuple[Path, ...] = (),
    native_links: tuple[IRNativeLink, ...] = (),
    lib_dirs: tuple[Path, ...] = (),
    cflags: tuple[str, ...] = (),
    ldflags: tuple[str, ...] = (),
    import_library: Path | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime_dir = Path(__file__).resolve().parent / "runtime"
    command = find_c_compiler() + _compile_flags(
        release=release,
        position_independent=True,
        extra=cflags,
    )
    command.extend(["-DZYENLANG_BUILD_SHARED=1", "-dynamiclib" if sys.platform == "darwin" else "-shared"])
    for include in (runtime_dir, *include_dirs):
        command.extend(["-I", str(include)])
    command.extend(str(source) for source in sources)
    command.extend(["-o", str(output)])
    if import_library is not None and sys.platform.startswith("win"):
        import_library.parent.mkdir(parents=True, exist_ok=True)
        command.append(f"-Wl,--out-implib,{import_library}")
    command.extend(_link_flags(native_links, lib_dirs=lib_dirs, extra=ldflags))
    subprocess.run(command, check=True)


def compile_c(
    source: Path,
    output: Path,
    *,
    release: bool = False,
    native_sources: tuple[str, ...] = (),
    native_links: tuple[IRNativeLink, ...] = (),
    include_dirs: tuple[Path, ...] = (),
    lib_dirs: tuple[Path, ...] = (),
    cflags: tuple[str, ...] = (),
    ldflags: tuple[str, ...] = (),
) -> None:
    runtime_dir = Path(__file__).resolve().parent / "runtime"
    command = find_c_compiler() + _compile_flags(release=release, extra=cflags) + ["-I", str(runtime_dir)]
    for include in include_dirs:
        command.extend(["-I", str(include)])
    command.append(str(source))
    command.extend(native_sources)
    command.extend(["-o", str(output)])
    command.extend(_link_flags(native_links, lib_dirs=lib_dirs, extra=ldflags))
    subprocess.run(command, check=True)
