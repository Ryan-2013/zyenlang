from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zyenlang.transpiler import (
    ZyenError,
    collect_native_c_metadata,
    load_source_with_imports,
    transpile,
)
from zyenlang.c_module import (
    c_compiler_command,
    empty_native_metadata,
    gcc_command,
    native_metadata_from_zy,
    native_platform_name,
)


ERROR_CASES = {
    "missing_import.zy": "requires `import <std/c_module> as c_module;`",
    "nonliteral_path.zy": "requires a string literal",
    "forbidden_parameter.zy": "only valid in a field or local declaration",
    "alias_mismatch.zy": "type alias `cm` and load alias `c_module` must match",
    "different_module_assignment.zy": "type mismatch in assignment",
    "different_module_field.zy": "type mismatch in struct field",
    "legacy_import.zy": "was removed",
    "missing_template.zy": "c_module template not found",
    "invalid_template.zy": "manifest must contain non-empty `functions` list",
    "load_expression.zy": "must directly initialize",
    "empty_struct.zy": "must contain at least one ZLC_FIELD",
    "lowercase_struct.zy": "must be a capitalized C/ZyenLang identifier",
    "unsupported_abi_type.zy": "unsupported c_module type `matrix`",
    "recursive_struct.zy": "cannot contain itself by value",
}


def compile_in_memory(path: Path) -> str:
    return transpile(load_source_with_imports(path))


def test_diagnostics() -> None:
    error_dir = ROOT / "tests" / "c_module_errors"
    for name, expected in ERROR_CASES.items():
        try:
            compile_in_memory(error_dir / name)
        except ZyenError as exc:
            message = str(exc)
            assert expected in message, f"{name}: expected {expected!r}, got {message!r}"
            assert "Zlcm_" not in message, f"{name}: leaked hidden type: {message}"
        else:
            raise AssertionError(f"{name}: expected a compiler error")


def test_hidden_types_and_metadata_are_deduplicated() -> None:
    source_path = ROOT / "tests" / "c_module_multi_test.zy"
    expanded = load_source_with_imports(source_path)
    generated_c = transpile(expanded)
    assert expanded.count("struct Zlcm_Shared_") == 2
    assert "Shared_Module" not in expanded
    assert "python -m" not in generated_c

    metadata = collect_native_c_metadata(source_path)
    source_names = [path.name for path in metadata["sources"]]
    assert source_names.count("shared.c") == 2
    assert len(metadata["sources"]) == 2


def test_platform_native_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        module = Path(tmp) / "platform.zy"
        module.write_text(
            "// c_libs: common\n"
            "// c_libs_windows: winonly\n"
            "// c_libs_linux: dl\n"
            "// c_libs_macos: cocoa\n",
            encoding="utf-8",
        )
        metadata = native_metadata_from_zy(module)
    expected = {"windows": "winonly", "linux": "dl", "macos": "cocoa"}[native_platform_name()]
    assert metadata["libs"] == ["common", expected]


def test_compiler_override() -> None:
    with patch.dict("os.environ", {"ZY_CC": "custom-cc --portable"}):
        assert c_compiler_command() == ["custom-cc", "--portable"]


def test_linux_compiler_enables_posix_api_before_headers() -> None:
    with patch("zyenlang.c_module.sys.platform", "linux"):
        with patch("zyenlang.c_module.c_compiler_command", return_value=["cc"]):
            command = gcc_command(empty_native_metadata(), Path("main.c"), Path("main"))
    define_index = command.index("-D_POSIX_C_SOURCE=200809L")
    include_index = command.index("-include")
    assert define_index < include_index


def main() -> int:
    tests = [
        test_diagnostics,
        test_hidden_types_and_metadata_are_deduplicated,
        test_platform_native_metadata,
        test_compiler_override,
        test_linux_compiler_enables_posix_api_before_headers,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
