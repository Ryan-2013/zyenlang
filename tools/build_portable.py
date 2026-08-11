#!/usr/bin/env python3
"""Build a self-contained ZyenLang CLI archive for the host platform."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
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


def run(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", subprocess.list2cmdline(command))
    subprocess.run(command, cwd=cwd or ROOT, env=env, check=True)


def project_version() -> str:
    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', source, re.MULTILINE)
    if not match:
        raise SystemExit("project version is missing from pyproject.toml")
    return match.group(1)


def portable_name(version: str) -> str:
    parts = version.split(".")
    if len(parts) == 3 and parts[0] == "0" and all(part.isdigit() for part in parts):
        return f"zyv{int(parts[1])}{int(parts[2]):02d}"
    compact = "".join(re.sub(r"[^0-9A-Za-z]+", "", part) for part in parts)
    if not compact:
        raise SystemExit(f"cannot derive portable name from version: {version}")
    return f"zyv{compact}"


def copy_release_content(stage: Path) -> None:
    for name in ("README.md", "README.zh-TW.md", "LICENSE", "CHANGELOG.md"):
        shutil.copy2(ROOT / name, stage / name)
    for name in ("docs", "examples"):
        shutil.copytree(ROOT / name, stage / name, dirs_exist_ok=True)
    shutil.copy2(ROOT / "docs/portable_release.md", stage / "PORTABLE.md")


def write_path_helpers(stage: Path) -> None:
    windows_ps1 = r'''param([switch]$DryRun)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$Entries = @($UserPath -split ";" | Where-Object { $_ })
if ($Entries -notcontains $Root) {
    if ($DryRun) {
        Write-Host "Would add $Root to the user PATH."
    } else {
        $NewPath = if ($UserPath) { "$Root;$UserPath" } else { $Root }
        [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
        Write-Host "Added $Root to the user PATH."
    }
} else {
    Write-Host "$Root is already in the user PATH."
}
$env:Path = "$Root;$env:Path"
& (Join-Path $Root "zy.exe") version
Write-Host "Open a new terminal, then run: zy doctor"
'''
    windows_cmd = r'''@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0add-to-user-path.ps1"
'''
    unix_sh = r'''#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "${SHELL:-}" in
  */zsh) RC="$HOME/.zshrc" ;;
  *) RC="$HOME/.bashrc" ;;
esac
LINE="export PATH=\"$ROOT:\$PATH\""
if [ "${ZY_PATH_DRY_RUN:-0}" = "1" ]; then
  printf 'Would add %s to PATH in %s.\n' "$ROOT" "$RC"
else
  touch "$RC"
  if ! grep -Fqx "$LINE" "$RC"; then
    printf '\n%s\n' "$LINE" >> "$RC"
    printf 'Added %s to PATH in %s.\n' "$ROOT" "$RC"
  else
    printf '%s is already configured in %s.\n' "$ROOT" "$RC"
  fi
fi
PATH="$ROOT:$PATH"
export PATH
"$ROOT/zy" version
printf 'Open a new terminal, then run: zy doctor\n'
'''
    (stage / "add-to-user-path.ps1").write_text(windows_ps1, encoding="utf-8")
    (stage / "add-to-user-path.cmd").write_text(windows_cmd, encoding="ascii")
    shell_helper = stage / "add-to-user-path.sh"
    shell_helper.write_text(unix_sh, encoding="utf-8")
    shell_helper.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version")
    parser.add_argument("--zig-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "dist/portable-work")
    args = parser.parse_args()

    version = args.version or project_version()
    bundle = portable_name(version)

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
    stage = work / bundle
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
    write_path_helpers(stage)

    zy = stage / ("zy.exe" if sys.platform.startswith("win") else "zy")
    gui_demo = stage / ("zyenlang-gui-demo.exe" if sys.platform.startswith("win") else "zyenlang-gui-demo")
    language_demo = stage / ("zyenlang-tour.exe" if sys.platform.startswith("win") else "zyenlang-tour")
    run([str(zy), "version"], cwd=stage)
    run([str(zy), "check", "examples/v2_language_tour.zy"], cwd=stage)
    run([str(zy), "run", "examples/v2_language_tour.zy"], cwd=stage)
    run([str(zy), "build", "examples/v2_gui_basic.zy", "-o", str(gui_demo), "--release"], cwd=stage)
    with tempfile.TemporaryDirectory() as package_smoke:
        run([str(zy), "pkg", "--project", package_smoke, "init", "--name", "portable-smoke"], cwd=stage)
        run([str(zy), "pkg", "--project", package_smoke, "install", "--locked"], cwd=stage)
    run([str(zy), "build", "examples/v2_language_tour.zy", "-o", str(language_demo), "--release"], cwd=stage)
    for generated in (
        gui_demo.with_suffix(".pdb"),
        language_demo.with_suffix(".pdb"),
    ):
        generated.unlink(missing_ok=True)
    if sys.platform.startswith("win"):
        run([
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(stage / "add-to-user-path.ps1"),
            "-DryRun",
        ], cwd=stage)
    else:
        helper_env = dict(os.environ)
        helper_env["ZY_PATH_DRY_RUN"] = "1"
        run(["sh", str(stage / "add-to-user-path.sh")], cwd=stage, env=helper_env)
    path_env = dict(os.environ)
    path_env["PATH"] = str(stage) + os.pathsep + os.defpath
    path_command = "zy.exe" if sys.platform.startswith("win") else "zy"
    run([path_command, "version"], cwd=stage.parent, env=path_env)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive_base = args.output_dir.resolve() / f"{bundle}-{target}"
    if sys.platform.startswith("win"):
        archive = Path(shutil.make_archive(str(archive_base), "zip", stage.parent, stage.name))
    else:
        archive = Path(shutil.make_archive(str(archive_base), "gztar", stage.parent, stage.name))
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
