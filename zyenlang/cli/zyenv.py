"""ZEP-0008: `zyenv` — ZyenLang version manager.

Inspired by pyenv / rbenv / nvm. Each installed version lives in its own venv
under ~/.zyenv/versions/<name>/venv/. A shim at ~/.zyenv/shims/zy resolves
the active version (ZY_VERSION env > nearest .zy-version walking cwd
ancestors > ~/.zyenv/version) and execs that venv's zy binary.

All paths default under ~/.zyenv; override with $ZYENV_HOME.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
import zipfile
import stat
from pathlib import Path
from typing import List, Optional, Tuple


def _zyenv_home() -> Path:
    return Path(os.environ.get("ZYENV_HOME", str(Path.home() / ".zyenv")))


def _versions_dir() -> Path:
    return _zyenv_home() / "versions"


def _shims_dir() -> Path:
    return _zyenv_home() / "shims"


def _global_version_file() -> Path:
    return _zyenv_home() / "version"


LOCAL_VERSION_FILENAME = ".zy-version"
MAX_ZIP_ENTRIES = 20_000
MAX_ZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRY_BYTES = 256 * 1024 * 1024


def _ensure_dirs() -> None:
    _versions_dir().mkdir(parents=True, exist_ok=True)
    _shims_dir().mkdir(parents=True, exist_ok=True)


def list_versions() -> List[str]:
    vd = _versions_dir()
    if not vd.is_dir():
        return []
    return sorted(p.name for p in vd.iterdir() if p.is_dir())


def find_local_version_file(start: Optional[Path] = None) -> Optional[Path]:
    cur = (start or Path.cwd()).resolve()
    while True:
        candidate = cur / LOCAL_VERSION_FILENAME
        if candidate.is_file():
            return candidate
        if cur.parent == cur:
            return None
        cur = cur.parent


def resolve_version() -> Tuple[Optional[str], str]:
    env_ver = os.environ.get("ZY_VERSION", "").strip()
    if env_ver:
        return env_ver, "shell (ZY_VERSION)"
    loc = find_local_version_file()
    if loc:
        v = loc.read_text(encoding="utf-8").strip()
        if v:
            return v, f"local ({loc})"
    gf = _global_version_file()
    if gf.is_file():
        v = gf.read_text(encoding="utf-8").strip()
        if v:
            return v, "global"
    return None, "none"


def _version_bin_dir(version: str) -> Path:
    base = _versions_dir() / version / "venv"
    return base / ("Scripts" if sys.platform == "win32" else "bin")


def resolve_zy_path(version: str) -> Optional[Path]:
    bin_dir = _version_bin_dir(version)
    exe = bin_dir / ("zy.exe" if sys.platform == "win32" else "zy")
    return exe if exe.exists() else None


def _make_venv(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    venv.create(target, with_pip=True, symlinks=False, upgrade_deps=False)


def _venv_pip(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/pip.exe" if sys.platform == "win32" else "bin/pip")


def _pip_env() -> dict:
    """Pip env that quiets version self-check (helps on networks where the
    pypi cert chain can't be verified — local installs don't need pypi)."""
    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    return env


def _pip_install_editable(venv_dir: Path, source: Path) -> int:
    # --no-build-isolation: don't pull build-system.requires from pypi. We
    # rely on the setuptools that `venv` shipped in the new venv. Without
    # this, an offline / SSL-restricted machine can never install.
    return subprocess.call(
        [str(_venv_pip(venv_dir)), "install", "--no-build-isolation", "-e", str(source)],
        env=_pip_env(),
    )


def _pip_install_path(venv_dir: Path, source: Path) -> int:
    return subprocess.call(
        [str(_venv_pip(venv_dir)), "install", "--no-build-isolation", str(source)],
        env=_pip_env(),
    )


def _safe_extract_zip(zip_path: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as bundle:
        members = bundle.infolist()
        if len(members) > MAX_ZIP_ENTRIES:
            raise ValueError(f"archive contains too many entries ({len(members)})")
        total = sum(member.file_size for member in members)
        if total > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ValueError(f"archive expands beyond {MAX_ZIP_UNCOMPRESSED_BYTES} bytes")
        for member in members:
            if member.file_size > MAX_ZIP_ENTRY_BYTES:
                raise ValueError(f"archive entry is too large: {member.filename}")
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"archive entry escapes destination: {member.filename}")
            mode = (member.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValueError(f"archive symlinks are not supported: {member.filename}")
        bundle.extractall(destination)


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_init(_args: argparse.Namespace) -> int:
    _ensure_dirs()
    write_shim()
    shims = _shims_dir()
    print(f"zyenv initialized at {_zyenv_home()}")
    print(f"  versions  : {_versions_dir()}")
    print(f"  shims     : {shims}")
    print()
    if sys.platform == "win32":
        print("Add the shims directory to PATH (current PowerShell session):")
        print(f'  $env:Path = "{shims};" + $env:Path')
        print()
        print("Persist for future sessions (user-level):")
        print(
            "  [Environment]::SetEnvironmentVariable("
            f'"Path", "{shims};" + '
            '[Environment]::GetEnvironmentVariable("Path", "User"), "User")'
        )
    else:
        print("Add this to your shell rc (~/.bashrc, ~/.zshrc, etc.):")
        print(f'  export PATH="{shims}:$PATH"')
    print()
    print("Then verify with: zy version")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    versions = list_versions()
    if not versions:
        print("(no versions installed; use `zyenv install-local <path>` to add one)")
        return 0
    active, _src = resolve_version()
    for v in versions:
        marker = "*" if v == active else " "
        installed = "" if resolve_zy_path(v) else "  (incomplete — no zy executable)"
        print(f"{marker} {v}{installed}")
    return 0


def cmd_current(_args: argparse.Namespace) -> int:
    v, src = resolve_version()
    if v is None:
        print("(no version selected; run `zyenv use <version>` or set ZY_VERSION)")
        return 1
    print(f"{v}  [{src}]")
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    _ensure_dirs()
    if args.version not in list_versions():
        print(f"zyenv: version `{args.version}` is not installed", file=sys.stderr)
        print("zyenv: installed versions:", ", ".join(list_versions()) or "(none)", file=sys.stderr)
        return 1
    _global_version_file().write_text(args.version, encoding="utf-8")
    print(f"global version set to {args.version}")
    return 0


def cmd_local(args: argparse.Namespace) -> int:
    target = Path.cwd() / LOCAL_VERSION_FILENAME
    if args.version is None:
        if not target.exists():
            print("(no local version set in this directory)")
            return 1
        print(target.read_text(encoding="utf-8").strip())
        return 0
    if args.version not in list_versions():
        print(f"zyenv: version `{args.version}` is not installed", file=sys.stderr)
        return 1
    target.write_text(args.version, encoding="utf-8")
    print(f"local version set to {args.version} in {Path.cwd()}")
    return 0


def cmd_unset(_args: argparse.Namespace) -> int:
    target = Path.cwd() / LOCAL_VERSION_FILENAME
    if target.exists():
        target.unlink()
        print(f"removed {target}")
        return 0
    print(f"(no {LOCAL_VERSION_FILENAME} in {Path.cwd()})")
    return 1


def cmd_which(_args: argparse.Namespace) -> int:
    v, _src = resolve_version()
    if v is None:
        print("zyenv: no version selected", file=sys.stderr)
        return 1
    exe = resolve_zy_path(v)
    if exe is None:
        print(f"zyenv: version `{v}` is not installed (no executable found)", file=sys.stderr)
        return 1
    print(exe)
    return 0


def cmd_install_local(args: argparse.Namespace) -> int:
    _ensure_dirs()
    src = Path(args.path).resolve()
    if not src.is_dir():
        print(f"zyenv: source path `{src}` is not a directory", file=sys.stderr)
        return 1
    if not (src / "pyproject.toml").exists():
        print(f"zyenv: `{src / 'pyproject.toml'}` is missing — not a Python package", file=sys.stderr)
        return 1
    name = args.as_name or _infer_version_name(src)
    target = _versions_dir() / name
    if target.exists() and not args.force:
        print(f"zyenv: version `{name}` is already installed (use --force to reinstall)", file=sys.stderr)
        return 1
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    venv_dir = target / "venv"
    print(f"[1/2] creating venv at {venv_dir} ...")
    _make_venv(venv_dir)
    print(f"[2/2] pip install -e {src} ...")
    rc = _pip_install_editable(venv_dir, src)
    if rc != 0:
        print(f"zyenv: pip install failed (exit code {rc})", file=sys.stderr)
        shutil.rmtree(target, ignore_errors=True)
        return rc
    print(f"installed version `{name}` (editable, source: {src})")
    return 0


def cmd_install_zip(args: argparse.Namespace) -> int:
    _ensure_dirs()
    zip_path = Path(args.path).resolve()
    if not zip_path.is_file():
        print(f"zyenv: zip `{zip_path}` not found", file=sys.stderr)
        return 1
    name = args.as_name or zip_path.stem
    target = _versions_dir() / name
    if target.exists() and not args.force:
        print(f"zyenv: version `{name}` is already installed (use --force to reinstall)", file=sys.stderr)
        return 1
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    extract_dir = target / "src"
    extract_dir.mkdir()
    print(f"[1/3] extracting {zip_path} → {extract_dir} ...")
    try:
        _safe_extract_zip(zip_path, extract_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"zyenv: unsafe or invalid zip: {exc}", file=sys.stderr)
        shutil.rmtree(target, ignore_errors=True)
        return 1
    entries = [p for p in extract_dir.iterdir() if not p.name.startswith(".")]
    pkg_root = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_dir
    if not (pkg_root / "pyproject.toml").exists():
        print(f"zyenv: extracted contents at `{pkg_root}` have no pyproject.toml", file=sys.stderr)
        shutil.rmtree(target, ignore_errors=True)
        return 1
    venv_dir = target / "venv"
    print(f"[2/3] creating venv at {venv_dir} ...")
    _make_venv(venv_dir)
    print(f"[3/3] pip install {pkg_root} ...")
    rc = _pip_install_path(venv_dir, pkg_root)
    if rc != 0:
        print(f"zyenv: pip install failed (exit code {rc})", file=sys.stderr)
        shutil.rmtree(target, ignore_errors=True)
        return rc
    print(f"installed version `{name}` (from {zip_path})")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    target = _versions_dir() / args.version
    if not target.is_dir():
        print(f"zyenv: version `{args.version}` is not installed", file=sys.stderr)
        return 1
    shutil.rmtree(target)
    gf = _global_version_file()
    if gf.is_file() and gf.read_text(encoding="utf-8").strip() == args.version:
        gf.unlink()
        print("(cleared global version reference)")
    print(f"uninstalled version `{args.version}`")
    return 0


def _infer_version_name(src: Path) -> str:
    pyproj = src / "pyproject.toml"
    try:
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        data = tomllib.loads(pyproj.read_text(encoding="utf-8"))
        v = data.get("project", {}).get("version")
        if v:
            return f"v{v}"
    except Exception:
        pass
    return src.name


# ---------------------------------------------------------------------------
# shim
# ---------------------------------------------------------------------------

def write_shim() -> None:
    _ensure_dirs()
    shims = _shims_dir()
    py_path = shims / "zy_resolver.py"
    py_path.write_text(_SHIM_PY, encoding="utf-8")
    if sys.platform == "win32":
        (shims / "zy.bat").write_text(_WIN_SHIM_BAT, encoding="ascii")
        (shims / "zyen.bat").write_text(_WIN_SHIM_BAT, encoding="ascii")
    else:
        zy = shims / "zy"
        zy.write_text("#!/usr/bin/env sh\nexec python3 \"$(dirname \"$0\")/zy_resolver.py\" \"$@\"\n", encoding="ascii")
        os.chmod(zy, 0o755)
        zyen = shims / "zyen"
        zyen.write_text("#!/usr/bin/env sh\nexec python3 \"$(dirname \"$0\")/zy_resolver.py\" \"$@\"\n", encoding="ascii")
        os.chmod(zyen, 0o755)


_WIN_SHIM_BAT = r"""@echo off
python "%~dp0zy_resolver.py" %*
"""


# This Python script is the actual shim: it runs without zyenlang installed
# (uses only stdlib) so the shim survives any version churn.
_SHIM_PY = r'''"""zyenv shim — resolves the active ZyenLang version and execs its zy binary.

Resolution order:
    1. $ZY_VERSION env var
    2. .zy-version in cwd, walking up to filesystem root
    3. ~/.zyenv/version (global default)
"""

import os
import sys
import subprocess
from pathlib import Path


def main() -> int:
    home = Path(os.environ.get("ZYENV_HOME", str(Path.home() / ".zyenv")))
    ver = os.environ.get("ZY_VERSION", "").strip()
    if not ver:
        cur = Path.cwd().resolve()
        while True:
            f = cur / ".zy-version"
            if f.is_file():
                try:
                    ver = f.read_text(encoding="utf-8").strip()
                except OSError:
                    ver = ""
                if ver:
                    break
            if cur.parent == cur:
                break
            cur = cur.parent
    if not ver:
        gf = home / "version"
        if gf.is_file():
            try:
                ver = gf.read_text(encoding="utf-8").strip()
            except OSError:
                ver = ""
    if not ver:
        sys.stderr.write(
            'zyenv: no ZyenLang version selected. '
            'Run "zyenv use <version>" or set ZY_VERSION.\n'
        )
        return 1
    bin_dir = home / "versions" / ver / "venv" / (
        "Scripts" if sys.platform == "win32" else "bin"
    )
    exe = bin_dir / ("zy.exe" if sys.platform == "win32" else "zy")
    if not exe.exists():
        sys.stderr.write(
            f'zyenv: version "{ver}" is not installed at {exe}\n'
        )
        return 1
    try:
        result = subprocess.run([str(exe)] + sys.argv[1:])
        return result.returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="zyenv", description="ZyenLang version manager (ZEP-0008)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create ~/.zyenv layout and write the shim")

    sub.add_parser("list", help="list installed ZyenLang versions")
    sub.add_parser("versions", help="alias for `list`")

    sub.add_parser("current", help="show the currently active version and its source")
    sub.add_parser("version", help="alias for `current`")

    p_use = sub.add_parser("use", help="set the global active version")
    p_use.add_argument("version")

    p_local = sub.add_parser("local", help="set or print the local .zy-version in cwd")
    p_local.add_argument("version", nargs="?", default=None)

    sub.add_parser("unset", help="remove the local .zy-version in cwd")

    sub.add_parser("which", help="print the path of the resolved zy binary")
    sub.add_parser("path", help="alias for `which`")

    p_il = sub.add_parser("install-local", help="install a local source tree as a named version")
    p_il.add_argument("path")
    p_il.add_argument("--as", dest="as_name", default=None, help="version name (default: vX.Y.Z from pyproject.toml)")
    p_il.add_argument("--force", action="store_true", help="overwrite if already installed")

    p_iz = sub.add_parser("install-zip", help="install a .zip archive of a Python package")
    p_iz.add_argument("path")
    p_iz.add_argument("--as", dest="as_name", default=None)
    p_iz.add_argument("--force", action="store_true")

    p_un = sub.add_parser("uninstall", help="remove an installed version")
    p_un.add_argument("version")

    args = parser.parse_args(argv)

    handlers = {
        "init": cmd_init,
        "list": cmd_list,
        "versions": cmd_list,
        "current": cmd_current,
        "version": cmd_current,
        "use": cmd_use,
        "local": cmd_local,
        "unset": cmd_unset,
        "which": cmd_which,
        "path": cmd_which,
        "install-local": cmd_install_local,
        "install-zip": cmd_install_zip,
        "uninstall": cmd_uninstall,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
