from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zyenlang.transpiler import ZyenError, build_file, compile_c, load_source_with_imports, transpile


def test_owned_struct_pointer_runtime() -> None:
    source = ROOT / "tests" / "owned_struct_pointer_test.zy"
    generated = transpile(load_source_with_imports(source))
    assert "zl_owned_ptr_from_int" in generated
    assert "zl_owned_ptr_from_ptr_int_" in generated
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as tmp:
        temp = Path(tmp)
        c_path = temp / "owned_struct_pointer_test.c"
        exe_path = temp / ("owned_struct_pointer_test.exe" if sys.platform.startswith("win") else "owned_struct_pointer_test")
        build_file(source, c_path)
        compile_c(c_path, exe_path)
        result = subprocess.run([str(exe_path)], capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    assert "pass=6 fail=0" in result.stdout


def test_direct_pointer_field_diagnostic() -> None:
    source = ROOT / "tests" / "pointer_struct_direct_field_error.zy"
    try:
        transpile(load_source_with_imports(source))
    except ZyenError as exc:
        message = str(exc)
        assert "cannot access `value` directly through `ptr<CarDirectFieldError>`" in message
        assert "write `(*car_ptr).value`" in message
    else:
        raise AssertionError("expected direct pointer field access to fail")


if __name__ == "__main__":
    test_owned_struct_pointer_runtime()
    test_direct_pointer_field_diagnostic()
    print("ALL PASS")
