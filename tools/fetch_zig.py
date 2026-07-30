#!/usr/bin/env python3
"""Download and verify the Zig toolchain used by portable releases."""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


ZIG_RELEASES = {
    "0.16.0": {
        "x86_64-windows": (
            "https://ziglang.org/download/0.16.0/zig-x86_64-windows-0.16.0.zip",
            "68659eb5f1e4eb1437a722f1dd889c5a322c9954607f5edcf337bc3684a75a7e",
        ),
        "x86_64-linux": (
            "https://ziglang.org/download/0.16.0/zig-x86_64-linux-0.16.0.tar.xz",
            "70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00",
        ),
        "aarch64-linux": (
            "https://ziglang.org/download/0.16.0/zig-aarch64-linux-0.16.0.tar.xz",
            "ea4b09bfb22ec6f6c6ceac57ab63efb6b46e17ab08d21f69f3a48b38e1534f17",
        ),
        "x86_64-macos": (
            "https://ziglang.org/download/0.16.0/zig-x86_64-macos-0.16.0.tar.xz",
            "0387557ed1877bc6a2e1802c8391953baddba76081876301c522f52977b52ba7",
        ),
        "aarch64-macos": (
            "https://ziglang.org/download/0.16.0/zig-aarch64-macos-0.16.0.tar.xz",
            "b23d70deaa879b5c2d486ed3316f7eaa53e84acf6fc9cc747de152450d401489",
        ),
    }
}


def target_key() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        arch = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        arch = "aarch64"
    else:
        raise SystemExit(f"unsupported Zig architecture: {machine}")
    if sys.platform.startswith("win"):
        system = "windows"
    elif sys.platform == "darwin":
        system = "macos"
    elif sys.platform.startswith("linux"):
        system = "linux"
    else:
        raise SystemExit(f"unsupported Zig platform: {sys.platform}")
    return f"{arch}-{system}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive) as bundle:
        root = destination.resolve()
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if root not in target.parents and target != root:
                raise SystemExit(f"unsafe path in Zig archive: {member.name}")
            if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise SystemExit(f"unsupported entry in Zig archive: {member.name}")
        bundle.extractall(destination)


def safe_extract_zip(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if root not in target.parents and target != root:
                raise SystemExit(f"unsafe path in Zig archive: {member.filename}")
            mode = (member.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise SystemExit(f"unsupported symlink in Zig archive: {member.filename}")
        bundle.extractall(destination)


def extract_archive(archive: Path, destination: Path) -> None:
    if archive.suffix.lower() == ".zip":
        safe_extract_zip(archive, destination)
    else:
        safe_extract_tar(archive, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.16.0")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("dist/toolchain-cache"))
    args = parser.parse_args()

    release = ZIG_RELEASES.get(args.version)
    if not release:
        raise SystemExit(f"Zig {args.version} is not pinned in tools/fetch_zig.py")
    key = target_key()
    artifact = release.get(key)
    if not artifact:
        raise SystemExit(f"Zig {args.version} has no {key} artifact")
    url, expected = artifact

    args.cache.mkdir(parents=True, exist_ok=True)
    archive = args.cache / Path(url).name
    if not archive.exists() or sha256(archive) != expected:
        print(f"downloading {url}")
        with urllib.request.urlopen(url, timeout=300) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
    actual = sha256(archive)
    if actual != expected:
        raise SystemExit(f"Zig checksum mismatch: expected {expected}, got {actual}")

    with tempfile.TemporaryDirectory() as tmp:
        extracted = Path(tmp)
        extract_archive(archive, extracted)
        roots = [path for path in extracted.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise SystemExit("unexpected Zig archive layout")
        if args.output.exists():
            shutil.rmtree(args.output)
        shutil.copytree(roots[0], args.output)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
