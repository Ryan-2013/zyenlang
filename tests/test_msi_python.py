from __future__ import annotations

from pathlib import Path
import sys

import pytest

from tools.build_msi import UPGRADE_CODE, build_msi, msi_id, stable_guid, validate_version


def test_msi_identifiers_are_stable_and_safe() -> None:
    assert msi_id("cmp", "toolchain/lib/file.h") == msi_id("cmp", "toolchain/lib/file.h")
    assert msi_id("cmp", "a") != msi_id("cmp", "b")
    assert stable_guid("component:zy.exe") == stable_guid("component:zy.exe")
    assert stable_guid("component:zy.exe") != stable_guid("component:toolchain/zig.exe")
    assert UPGRADE_CODE.startswith("{") and UPGRADE_CODE.endswith("}")


@pytest.mark.parametrize("version", ["1", "1.2", "1.2.3.4", "1.2.dev", "1.256.0"])
def test_msi_rejects_invalid_versions(version: str) -> None:
    with pytest.raises(SystemExit):
        validate_version(version)


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="msilib is Windows-only")
def test_msi_contains_product_files_and_safe_user_path_update(tmp_path: Path) -> None:
    import msilib

    stage = tmp_path / "zyv200"
    (stage / "examples").mkdir(parents=True)
    (stage / "zy.exe").write_bytes(b"current")
    (stage / "examples" / "hello.zy").write_text("fn main() i32 { return 0 }", encoding="utf-8")
    output = tmp_path / "zyenlang.msi"

    build_msi(stage, output, "0.2.0")

    assert output.is_file() and output.stat().st_size > 0
    database = msilib.OpenDatabase(str(output), msilib.MSIDBOPEN_READONLY)

    def scalar(query: str) -> str:
        view = database.OpenView(query)
        view.Execute(None)
        record = view.Fetch()
        assert record is not None
        return record.GetString(1)

    assert scalar("SELECT `Value` FROM `Property` WHERE `Property`='ProductVersion'") == "0.2.0"
    assert scalar("SELECT `Value` FROM `Property` WHERE `Property`='UpgradeCode'") == UPGRADE_CODE
    assert scalar("SELECT `Name` FROM `Environment` WHERE `Environment`='ZyenLangPath'") == "=-Path"
    assert scalar("SELECT `Value` FROM `Environment` WHERE `Environment`='ZyenLangPath'") == "[INSTALLDIR];[~]"
