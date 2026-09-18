from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def cli_module():
    return importlib.import_module("zyenlang.cli.main")


def test_zy_forwards_to_the_current_compiler(monkeypatch) -> None:
    cli = cli_module()
    received: list[list[str]] = []
    monkeypatch.setattr(cli, "compiler_main", lambda argv: received.append(argv) or 17)

    assert cli.main(["run", "main.zy"]) == 17
    assert received == [["run", "main.zy"]]


def test_zy_version_alias_uses_version_flag(monkeypatch) -> None:
    cli = cli_module()
    received: list[list[str]] = []
    monkeypatch.setattr(cli, "compiler_main", lambda argv: received.append(argv) or 0)

    assert cli.main(["version"]) == 0
    assert received == [["--version"]]


def test_only_current_public_compiler_commands_are_packaged() -> None:
    project = Path(__file__).resolve().parents[1]
    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")

    assert '\nzy = "zyenlang.cli.main:main"' in pyproject
    assert '\nzyen = "zyenlang.cli.main:main"' in pyproject
    assert "\nzy1 =" not in pyproject
    assert "\nzy2 =" not in pyproject


def test_only_v03_standard_library_is_present() -> None:
    project = Path(__file__).resolve().parents[1]
    package = Path(importlib.import_module("zyenlang").__file__).resolve().parent

    assert (package / "v2" / "std" / "list.zy").is_file()
    assert not (package / "v1").exists()
    assert not (project / "std").exists()


def test_run_rejects_program_arguments_without_separator(tmp_path: Path) -> None:
    entry = importlib.import_module("zyenlang.v2.__main__")
    with pytest.raises(SystemExit):
        entry.main(["run", "app", "folder", "output.txt"])


def test_run_keeps_double_dash_program_arguments(monkeypatch, tmp_path: Path) -> None:
    entry = importlib.import_module("zyenlang.v2.__main__")
    received: list[tuple[str | None, list[str]]] = []

    class FakeBuilder:
        def run(self, target: str | None, *, program_args: list[str], out_dir=None) -> int:
            received.append((target, program_args))
            return 0

    monkeypatch.setattr(entry, "ArtifactBuilder", lambda _project, release=False: FakeBuilder())

    assert entry.main(["run", "app", "--project", str(tmp_path), "--", "-h"]) == 0
    assert received == [("app", ["-h"])]
