#!/usr/bin/env python3
"""Build a per-user Windows MSI from an already verified portable directory."""

from __future__ import annotations

import argparse
import gc
import hashlib
from pathlib import Path
import re
import sys
import uuid


MANUFACTURER = "ZyenLang"
UPGRADE_CODE = "{ED0DBD2F-C5F5-59BD-932E-446C3C5CA331}"
GUID_NAMESPACE = uuid.UUID("ed0dbd2f-c5f5-59bd-932e-446c3c5ca331")


def msi_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def stable_guid(value: str) -> str:
    return "{" + str(uuid.uuid5(GUID_NAMESPACE, value)).upper() + "}"


def validate_version(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("MSI version must contain exactly three numeric fields")
    if any(int(part) > 255 for part in version.split(".")):
        raise SystemExit("MSI version fields must be between 0 and 255")


def build_msi(stage: Path, output: Path, version: str) -> Path:
    if not sys.platform.startswith("win"):
        raise SystemExit("MSI packages can only be built on Windows")
    try:
        import msilib
        from msilib import schema, sequence, text
    except ImportError as exc:
        raise SystemExit("MSI builds require Python 3.10 or older on Windows (msilib)") from exc

    validate_version(version)
    stage = stage.resolve()
    output = output.resolve()
    if not (stage / "zy.exe").is_file():
        raise SystemExit("portable stage must contain zy.exe")
    output.parent.mkdir(parents=True, exist_ok=True)

    product_code = stable_guid(f"product:{version}:windows-x64")
    database = msilib.init_database(
        str(output),
        schema,
        f"ZyenLang {version}",
        product_code,
        version,
        MANUFACTURER,
    )
    for table_name in ("AdminExecuteSequence", "AdvtExecuteSequence", "InstallExecuteSequence"):
        msilib.add_data(database, table_name, getattr(sequence, table_name))
    msilib.add_tables(database, text)
    msilib.add_data(
        database,
        "Property",
        [
            ("UpgradeCode", UPGRADE_CODE),
            ("INSTALLLEVEL", "1"),
            ("ARPNOMODIFY", "1"),
            ("ARPNOREPAIR", "1"),
            ("ARPURLINFOABOUT", "https://github.com/Ryan-2013/zyenlang"),
            ("LIMITUI", "1"),
            ("SecureCustomProperties", "ZYENLANG_UPGRADE_FOUND;ZYENLANG_NEWER_FOUND"),
        ],
    )
    msilib.add_data(
        database,
        "Upgrade",
        [
            (UPGRADE_CODE, None, version, None, 0, None, "ZYENLANG_UPGRADE_FOUND"),
            (UPGRADE_CODE, version, None, None, 258, None, "ZYENLANG_NEWER_FOUND"),
        ],
    )
    msilib.add_data(
        database,
        "LaunchCondition",
        [("NOT ZYENLANG_NEWER_FOUND", "A newer ZyenLang version is already installed.")],
    )

    cabinet = msilib.CAB("zyenlang.cab")
    source_root = msilib.Directory(database, cabinet, None, str(stage.parent), "TARGETDIR", "SourceDir")
    local_app_data = msilib.Directory(database, cabinet, source_root, ".", "LocalAppDataFolder", ".")
    programs = msilib.Directory(database, cabinet, local_app_data, ".", "ProgramsFolder", "Programs")
    install_dir = msilib.Directory(database, cabinet, programs, stage.name, "INSTALLDIR", "ZyenLang")
    feature = msilib.Feature(
        database,
        "ZyenLangCore",
        "ZyenLang",
        "ZyenLang compiler, standard library, bundled C toolchain, examples, and documentation",
        1,
        directory=install_dir.logical,
    )
    feature.set_current()

    path_component = None

    def add_directory(parent, source: Path, relative: Path) -> None:
        nonlocal path_component
        files = sorted((item for item in source.iterdir() if item.is_file()), key=lambda item: item.name.lower())
        for file_path in files:
            relative_file = (relative / file_path.name).as_posix()
            component = msi_id("cmp", relative_file)
            parent.start_component(
                component=component,
                feature=feature,
                flags=0,
                keyfile=file_path.name,
                uuid=stable_guid(f"component:{relative_file}"),
            )
            parent.add_file(file_path.name)
            if relative_file == "zy.exe":
                path_component = component
        directories = sorted((item for item in source.iterdir() if item.is_dir()), key=lambda item: item.name.lower())
        for child_path in directories:
            child = msilib.Directory(
                database,
                cabinet,
                parent,
                child_path.name,
                msi_id("dir", (relative / child_path.name).as_posix()),
                child_path.name,
            )
            add_directory(child, child_path, relative / child_path.name)

    add_directory(install_dir, stage, Path())
    if path_component is None:
        raise SystemExit("zy.exe component was not added to the MSI")
    msilib.add_data(database, "Environment", [("ZyenLangPath", "=-Path", "[INSTALLDIR];[~]", path_component)])
    cabinet.commit(database)
    database.Commit()
    del install_dir, programs, local_app_data, source_root, feature, cabinet, database
    gc.collect()
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(build_msi(args.stage, args.output, args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
