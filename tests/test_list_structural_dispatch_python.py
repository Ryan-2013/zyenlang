from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zyenlang.transpiler import ZyenError, build_file, compile_c, load_source_with_imports, transpile


def compile_in_memory(name: str) -> str:
    path = ROOT / "tests" / name
    return transpile(load_source_with_imports(path))


def expect_compile_error(name: str, expected: str) -> None:
    try:
        compile_in_memory(name)
    except ZyenError as exc:
        assert expected in str(exc), f"{name}: expected {expected!r}, got {str(exc)!r}"
    else:
        raise AssertionError(f"{name}: expected a compiler error")


def build_and_run(name: str) -> subprocess.CompletedProcess[str]:
    source = ROOT / "tests" / name
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as tmp:
        tmp_path = Path(tmp)
        c_path = tmp_path / f"{source.stem}.c"
        exe_path = tmp_path / (source.stem + (".exe" if sys.platform.startswith("win") else ""))
        build_file(source, c_path)
        compile_c(c_path, exe_path)
        return subprocess.run([str(exe_path)], capture_output=True, text=True, check=False, timeout=20)


def test_structural_dispatch_codegen_and_runtime() -> None:
    generated = compile_in_memory("list_structural_dispatch_test.zy")
    assert "ZL_VALUE_STRUCT = 7" in generated
    assert "zl_any_struct_DispatchDog_copy" in generated
    assert "zl_any_method_bark_" in generated
    assert "zl_any_method_score_" in generated

    result = build_and_run("list_structural_dispatch_test.zy")
    assert result.returncode == 0, result.stderr
    assert "pass=6 fail=0" in result.stdout


def test_structural_dispatch_diagnostics() -> None:
    expect_compile_error("list_struct_missing_method_error.zy", "does not provide the common method `bark()`")
    expect_compile_error("list_struct_signature_error.zy", "is not structurally compatible")
    expect_compile_error("list_struct_defaults_error.zy", "is not structurally compatible")
    expect_compile_error("list_struct_nonstruct_error.zy", "requires struct elements")
    expect_compile_error("list_struct_empty_error.zy", "empty List with no inferred element types")


def test_erased_list_runtime_guard() -> None:
    result = build_and_run("list_struct_runtime_guard_error.zy")
    assert result.returncode != 0
    assert "List value does not provide bark()" in result.stderr


def main() -> int:
    test_structural_dispatch_codegen_and_runtime()
    test_structural_dispatch_diagnostics()
    test_erased_list_runtime_guard()
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
