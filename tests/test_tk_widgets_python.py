from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zyenlang.transpiler import build_file, collect_native_c_metadata, compile_c, load_source_with_imports, transpile


def compile_in_memory(name: str) -> str:
    return transpile(load_source_with_imports(ROOT / name))


def build_and_run(name: str) -> subprocess.CompletedProcess[str]:
    source = ROOT / name
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as tmp:
        tmp_path = Path(tmp)
        c_path = tmp_path / f"{source.stem}.c"
        exe_path = tmp_path / (source.stem + (".exe" if sys.platform.startswith("win") else ""))
        build_file(source, c_path)
        compile_c(c_path, exe_path, collect_native_c_metadata(source))
        return subprocess.run([str(exe_path)], capture_output=True, text=True, check=False, timeout=20)


def test_widget_codegen() -> None:
    generated = compile_in_memory("examples/tk_widgets.zy")
    assert "int Renderer_open(Renderer* this)" in generated
    assert "Renderer_zlmod_tk_open" not in generated
    assert ".children = zl_list_from_array" in generated
    assert "zl_any_struct_Text_copy" in generated
    assert "zl_any_struct_Button_copy" in generated
    assert "zl_any_method_draw_" in generated
    assert "zl_any_method_handle_event_" in generated


def test_widget_events_and_callback_runtime() -> None:
    result = build_and_run("tests/tk_widget_test.zy")
    assert result.returncode == 0, result.stderr
    assert "pass=17 fail=0" in result.stdout
    assert "warning:" not in result.stderr


def test_widget_session_is_in_memory() -> None:
    native = (ROOT / "std" / "tk_native.c").read_text(encoding="utf-8")
    session_start = native.index("int zl_tk_session_open")
    session_end = native.index("int zl_tk_session_pickdir")
    active_session = native[session_start:session_end]
    assert "fopen(" not in active_session
    assert "zyen_tk_scene.ztk" not in active_session
    assert "zltk_frame_clear" in active_session
    assert "zltk_collect_events" in active_session
    assert (ROOT / "std" / "tk.zy").read_bytes() == (ROOT / "zyenlang" / "std" / "tk.zy").read_bytes()
    assert (ROOT / "std" / "tk_native.c").read_bytes() == (ROOT / "zyenlang" / "std" / "tk_native.c").read_bytes()


def test_tk_uses_public_c_module_contract() -> None:
    tk_source = (ROOT / "std" / "tk.zy").read_text(encoding="utf-8")
    compiler = (ROOT / "zyenlang" / "transpiler.py").read_text(encoding="utf-8")
    metadata = collect_native_c_metadata(ROOT / "examples" / "tk_widgets.zy")
    assert "import <std/c_module> as c_module;" in tk_source
    assert 'c_module.load("tk.zlcm.h")' in tk_source
    assert "// c_headers:" not in tk_source
    assert "// c_sources:" not in tk_source
    assert '"int zl_tk_begin(const char* path);"' not in compiler
    assert [path.name for path in metadata["headers"]] == ["tk_native.h"]
    assert [path.name for path in metadata["sources"]] == ["tk_native.c"]
    assert (ROOT / "std" / "tk.zlcm.h").read_bytes() == (ROOT / "zyenlang" / "std" / "tk.zlcm.h").read_bytes()


def main() -> int:
    test_widget_codegen()
    test_widget_events_and_callback_runtime()
    test_widget_session_is_in_memory()
    test_tk_uses_public_c_module_contract()
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
