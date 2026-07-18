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


def test_function_pointer_codegen_and_runtime() -> None:
    generated = compile_in_memory("fn_pointer_test.zy")
    assert 'zl_ptr(&add3_zlfnval, "fn(int,int)->int")' in generated
    assert 'zl_mem_alloc_cell_drop(sizeof(ZL_Function)' in generated
    assert 'zl_fn_ptr_load' in generated

    result = build_and_run("fn_pointer_test.zy")
    assert result.returncode == 0, result.stderr
    assert "pass=11 fail=0" in result.stdout


def test_function_pointer_diagnostics() -> None:
    expect_compile_error("fn_pointer_signature_error.zy", "function pointer signature mismatch")
    expect_compile_error("fn_pointer_cast_error.zy", "function pointer signatures must match exactly")
    expect_compile_error("fn_pointer_set_decl_error.zy", "`set` only assigns an existing variable")


def test_function_pointer_runtime_guards() -> None:
    none_result = build_and_run("fn_pointer_none_call_error.zy")
    assert none_result.returncode != 0
    assert "None pointer dereference: missing" in none_result.stderr

    tag_result = build_and_run("fn_pointer_runtime_type_error.zy")
    assert tag_result.returncode != 0
    assert "function pointer signature mismatch" in tag_result.stderr
    assert "stores int, requested fn(int)->int" in tag_result.stderr


def main() -> int:
    test_function_pointer_codegen_and_runtime()
    test_function_pointer_diagnostics()
    test_function_pointer_runtime_guards()
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
