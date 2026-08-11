from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

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


def compile_c(
    source: Path,
    output: Path,
    *,
    release: bool = False,
    native_sources: tuple[str, ...] = (),
    native_links: tuple[IRNativeLink, ...] = (),
) -> None:
    runtime_dir = Path(__file__).resolve().parent / "runtime"
    command = find_c_compiler() + [
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-O2" if release else "-O0",
        "-I",
        str(runtime_dir),
    ]
    command.append(str(source))
    command.extend(native_sources)
    command.extend(["-o", str(output)])
    platform = "windows" if sys.platform.startswith("win") else "macos" if sys.platform == "darwin" else "linux"
    for link in native_links:
        if link.platform in {"all", platform}:
            command.append(f"-l{link.library}")
    subprocess.run(command, check=True)
