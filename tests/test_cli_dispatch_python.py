from __future__ import annotations

import importlib


def cli_module():
    return importlib.import_module("zyenlang.cli.main")


def test_zy_defaults_to_the_current_v2_compiler(monkeypatch) -> None:
    cli = cli_module()
    received: list[list[str]] = []
    monkeypatch.setattr(cli, "v2_main", lambda argv: received.append(argv) or 17)
    monkeypatch.setattr(cli, "legacy_main", lambda argv: 99)

    assert cli.main(["run", "main.zy"]) == 17
    assert received == [["run", "main.zy"]]


def test_zy_legacy_subcommand_uses_the_v1_compiler(monkeypatch) -> None:
    cli = cli_module()
    received: list[list[str]] = []
    monkeypatch.setattr(cli, "legacy_main", lambda argv: received.append(argv) or 23)
    monkeypatch.setattr(cli, "v2_main", lambda argv: 99)

    assert cli.main(["legacy", "run", "old.zy"]) == 23
    assert received == [["run", "old.zy"]]


def test_zy1_entry_always_uses_the_v1_compiler(monkeypatch) -> None:
    cli = cli_module()
    received: list[list[str]] = []
    monkeypatch.setattr(cli, "legacy_main", lambda argv: received.append(argv) or 31)

    assert cli.legacy_entry(["check", "old.zy"]) == 31
    assert received == [["check", "old.zy"]]


def test_zy_version_alias_uses_v2_version_flag(monkeypatch) -> None:
    cli = cli_module()
    received: list[list[str]] = []
    monkeypatch.setattr(cli, "v2_main", lambda argv: received.append(argv) or 0)

    assert cli.main(["version"]) == 0
    assert received == [["--version"]]
