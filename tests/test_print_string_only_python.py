from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zyenlang.transpiler import ZyenError, build_file, compile_c, load_source_with_imports, transpile


def compile_in_memory(name: str) -> str:
    return transpile(load_source_with_imports(ROOT / "tests" / name))


def test_print_rejects_non_string_values() -> None:
    try:
        compile_in_memory("print_non_string_error.zy")
    except ZyenError as exc:
        message = str(exc)
        assert "line 2: print expects `str`, got `int`" in message
        assert "`print((str)value);`" in message
        assert 'print(f"{value}")' in message
    else:
        raise AssertionError("print(42) should fail during `zy check`")


def test_print_requires_exactly_one_argument() -> None:
    try:
        compile_in_memory("print_arity_error.zy")
    except ZyenError as exc:
        assert "line 2: print expects exactly 1 `str` argument, got 2" in str(exc)
    else:
        raise AssertionError("print with two arguments should fail during `zy check`")


def test_print_accepts_strings_casts_and_fstrings() -> None:
    source = ROOT / "tests" / "print_string_test.zy"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as tmp:
        tmp_path = Path(tmp)
        c_path = tmp_path / "print_string_test.c"
        exe_path = tmp_path / ("print_string_test.exe" if sys.platform.startswith("win") else "print_string_test")
        build_file(source, c_path)
        compile_c(c_path, exe_path)
        result = subprocess.run([str(exe_path)], capture_output=True, text=True, check=False, timeout=20)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "plain",
        "value=42",
        "42",
        "None",
        "pointer=None",
        "7",
    ]
