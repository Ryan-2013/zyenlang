from __future__ import annotations

import io
from pathlib import Path
import stat
import tarfile
import zipfile

import pytest

from tools.fetch_zig import ZIG_RELEASES, safe_extract_tar, safe_extract_zip
from zyenlang.cli.zyenv import _safe_extract_zip as zyenv_safe_extract_zip


def write_zip(path: Path, name: str, data: bytes = b"bad") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, data)


@pytest.mark.parametrize("extractor", [safe_extract_zip, zyenv_safe_extract_zip])
def test_zip_extractors_reject_path_traversal(tmp_path: Path, extractor) -> None:
    archive = tmp_path / "unsafe.zip"
    write_zip(archive, "../outside.txt")

    with pytest.raises((SystemExit, ValueError), match="escape|unsafe"):
        extractor(archive, tmp_path / "output")

    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("extractor", [safe_extract_zip, zyenv_safe_extract_zip])
def test_zip_extractors_reject_symlinks(tmp_path: Path, extractor) -> None:
    archive = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(info, "target")

    with pytest.raises((SystemExit, ValueError), match="symlink"):
        extractor(archive, tmp_path / "output")


def test_tar_extractor_rejects_links(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as bundle:
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        bundle.addfile(link, io.BytesIO())

    with pytest.raises(SystemExit, match="unsupported entry"):
        safe_extract_tar(archive, tmp_path / "output")


def test_zig_release_checksums_are_pinned() -> None:
    targets = ZIG_RELEASES["0.16.0"]
    assert set(targets) == {
        "x86_64-windows",
        "x86_64-linux",
        "aarch64-linux",
        "x86_64-macos",
        "aarch64-macos",
    }
    assert all(len(checksum) == 64 for _, checksum in targets.values())
