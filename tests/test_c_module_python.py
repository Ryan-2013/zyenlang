from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zyenlang.transpiler import (
    ZyenError,
    collect_native_c_metadata,
    load_source_with_imports,
    transpile,
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


def main() -> int:
    tests = [test_diagnostics, test_hidden_types_and_metadata_are_deduplicated]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
