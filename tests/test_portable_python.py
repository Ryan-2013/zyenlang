from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.build_portable import portable_name, project_version, write_path_helpers


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS {message}")


def main() -> int:
    check(project_version() == "0.2.1", "release version")
    check(portable_name("0.1.53") == "zyv153", "compact portable name")
    check(portable_name("0.2.0") == "zyv200", "zero-patch portable name")
    check(portable_name("1.2.3") == "zyv123", "nonzero major portable name")

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "zyv153"
        stage.mkdir()
        write_path_helpers(stage)
        powershell = (stage / "add-to-user-path.ps1").read_text(encoding="utf-8")
        batch = (stage / "add-to-user-path.cmd").read_text(encoding="ascii")
        shell = (stage / "add-to-user-path.sh").read_text(encoding="utf-8")
        check("$DryRun" in powershell, "Windows helper supports dry run")
        check("add-to-user-path.ps1" in batch, "Windows command wrapper")
        check("ZY_PATH_DRY_RUN" in shell, "Unix helper supports dry run")
        if os.name == "nt":
            check(shell.startswith("#!/usr/bin/env sh"), "Unix helper has a shell shebang")
        else:
            check(bool((stage / "add-to-user-path.sh").stat().st_mode & stat.S_IXUSR), "Unix helper is executable")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
