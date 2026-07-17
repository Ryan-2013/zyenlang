#!/usr/bin/env python3
"""Build a self-contained ZyenLang CLI archive for the host platform."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def platform_label() -> str:
    machine = platform.machine().lower()
    arch = "x64" if machine in {"amd64", "x86_64"} else "arm64" if machine in {"arm64", "aarch64"} else machine
    if sys.platform.startswith("win"):
        return f"windows-{arch}"
    if sys.platform == "darwin":
        return f"macos-{arch}"
    if sys.platform.startswith("linux"):
        return f"linux-{arch}"
    raise SystemExit(f"unsupported portable target: {sys.platform}/{machine}")


def raylib_runtime() -> Path:
    target = platform_label()
    mapping = {
        "windows-x64": ROOT / "zyenlang/vendor/raylib/windows-x64/raylib.dll",
        "linux-x64": ROOT / "zyenlang/vendor/raylib/linux-x64/libraylib.so",
        "linux-arm64": ROOT / "zyenlang/vendor/raylib/linux-arm64/libraylib.so",
        "macos-x64": ROOT / "zyenlang/vendor/raylib/macos-universal/libraylib.dylib",
        "macos-arm64": ROOT / "zyenlang/vendor/raylib/macos-universal/libraylib.dylib",
    }
    try:
        return mapping[target]
    except KeyError as exc:
        raise SystemExit(f"no bundled raylib runtime for {target}") from exc


def run(command: list[str], cwd: Path | None = None) -> None:
    print("+", subprocess.list2cmdline(command))
    subprocess.run(command, cwd=cwd or ROOT, check=True)


def copy_release_content(stage: Path) -> None:
    for name in ("README.md", "README.zh-TW.md", "LICENSE", "CHANGELOG.md", "SPEC_v0_1.md"):
        shutil.copy2(ROOT / name, stage / name)
    for name in ("docs", "examples", "apps"):
        shutil.copytree(ROOT / name, stage / name, dirs_exist_ok=True)
    shutil.copy2(ROOT / "docs/portable_release.md", stage / "PORTABLE.md")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.1.50")
    parser.add_argument("--zig-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "dist/portable-work")
    args = parser.parse_args()

    zig_dir = args.zig_dir.resolve()
    zig_exe = zig_dir / ("zig.exe" if sys.platform.startswith("win") else "zig")
    if not zig_exe.is_file():
        raise SystemExit(f"Zig executable not found: {zig_exe}")

    target = platform_label()
    work = args.work_dir.resolve() / target
    if work.exists():
        shutil.rmtree(work)
    pyinstaller_dist = work / "pyinstaller-dist"
    pyinstaller_build = work / "pyinstaller-build"
    pyinstaller_spec = work / "spec"
    stage = work / f"zyenlang-v{args.version}-{target}"
    work.mkdir(parents=True)
    pyinstaller_spec.mkdir(parents=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "zy",
        "--distpath",
        str(pyinstaller_dist),
        "--workpath",
        str(pyinstaller_build),
        "--specpath",
        str(pyinstaller_spec),
        "--collect-data",
        "zyenlang",
        "--hidden-import",
        "zyenlang.cli.doctor",
        "--hidden-import",
        "zyenlang.cli.doctor_checks",
        str(ROOT / "zyen.py"),
    ]
    run(command)
    shutil.copytree(pyinstaller_dist / "zy", stage)
    shutil.copytree(zig_dir, stage / "toolchain")
    runtime = raylib_runtime()
    shutil.copy2(runtime, stage / runtime.name)
    copy_release_content(stage)

    zy = stage / ("zy.exe" if sys.platform.startswith("win") else "zy")
    gui_demo = stage / ("zytk-demo.exe" if sys.platform.startswith("win") else "zytk-demo")
    run([str(zy), "version"], cwd=stage)
    run([str(zy), "check", "examples/hello.zy"], cwd=stage)
    run([str(zy), "run", "examples/hello.zy"], cwd=stage)
    run([str(zy), "build", "apps/zytk_demo.zy", "--exe", str(gui_demo)], cwd=stage)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive_base = args.output_dir.resolve() / stage.name
    if sys.platform.startswith("win"):
        archive = Path(shutil.make_archive(str(archive_base), "zip", stage.parent, stage.name))
    else:
        archive = Path(shutil.make_archive(str(archive_base), "gztar", stage.parent, stage.name))
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
