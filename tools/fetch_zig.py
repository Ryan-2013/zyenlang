#!/usr/bin/env python3
"""Download and verify the Zig toolchain used by portable releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


INDEX_URL = "https://ziglang.org/download/index.json"


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
        bundle.extractall(destination)


def extract_archive(archive: Path, destination: Path) -> None:
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(destination)
    else:
        safe_extract_tar(archive, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.16.0")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("dist/toolchain-cache"))
    args = parser.parse_args()

    with urllib.request.urlopen(INDEX_URL, timeout=60) as response:
        index = json.load(response)
    release = index.get(args.version)
    if not release:
        raise SystemExit(f"Zig {args.version} is not in {INDEX_URL}")
    key = target_key()
    artifact = release.get(key)
    if not artifact:
        raise SystemExit(f"Zig {args.version} has no {key} artifact")

    args.cache.mkdir(parents=True, exist_ok=True)
    archive = args.cache / Path(artifact["tarball"]).name
    expected = artifact["shasum"].lower()
    if not archive.exists() or sha256(archive) != expected:
        print(f"downloading {artifact['tarball']}")
        with urllib.request.urlopen(artifact["tarball"], timeout=300) as response, archive.open("wb") as out:
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
