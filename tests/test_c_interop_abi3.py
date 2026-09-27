from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from zyenlang.compiler import CompileError, Compiler
from zyenlang.compiler.artifacts import ArtifactBuilder
from zyenlang.compiler.bindgen import BindgenError, generate_bindings


def run_source(tmp_path: Path, source: str, *, name: str = "program") -> subprocess.CompletedProcess[str]:
    path = tmp_path / f"{name}.zy"
    path.write_text(source, encoding="utf-8")
    executable = tmp_path / (f"{name}.exe" if sys.platform.startswith("win") else name)
    Compiler().build_executable(path, executable)
    return subprocess.run([str(executable)], capture_output=True, text=True, check=False)


def write_graphics_fixture(tmp_path: Path) -> None:
    (tmp_path / "graphics_zy.c").write_text(
        r'''#include <stdint.h>
#include <stdlib.h>
#include "zyenlang_c_abi.h"

typedef struct TestWindow { int32_t total; } TestWindow;
static int32_t drops;

static void test_window_drop(void* raw) {
    drops += 1;
    free(raw);
}

ZL_Handle test_window_open(ZL_String title, int32_t width, int32_t height) {
    (void)title;
    if (width < 0 || height < 0) return (ZL_Handle){0};
    TestWindow* value = (TestWindow*)calloc(1, sizeof(TestWindow));
    value->total = width + height;
    return zl_handle_adopt(value, "test-window", test_window_drop);
}

ZL_Handle test_window_maybe(bool create) {
    if (!create) return (ZL_Handle){0};
    TestWindow* value = (TestWindow*)calloc(1, sizeof(TestWindow));
    value->total = 7;
    return zl_handle_adopt(value, "test-window", test_window_drop);
}

void test_window_upload(ZL_Handle window, ZL_Slice bytes) {
    TestWindow* value = (TestWindow*)zl_handle_data(window, "test-window");
    const uint8_t* data = (const uint8_t*)bytes.data;
    for (size_t index = 0; index < bytes.len; ++index) value->total += data[index];
}

void test_fill(ZL_MutSlice bytes) {
    if (bytes.len > 0) ((uint8_t*)bytes.data)[0] = 99;
}

ZL_Handle test_window_view(ZL_Handle window) {
    TestWindow* value = (TestWindow*)zl_handle_data(window, "test-window");
    return zl_handle_borrow_from(value, "test-view", window);
}

int32_t test_view_total(ZL_Handle view) {
    return ((TestWindow*)zl_handle_data(view, "test-view"))->total;
}

int32_t test_window_total(ZL_Handle window) {
    return ((TestWindow*)zl_handle_data(window, "test-window"))->total;
}

void test_window_consume(ZL_Handle window) {
    test_window_drop(zl_handle_take(window, "test-window"));
}

ZL_Handle test_window_wrong_type(void) {
    TestWindow* value = (TestWindow*)calloc(1, sizeof(TestWindow));
    return zl_handle_adopt(value, "wrong-window-type", test_window_drop);
}

int32_t test_drop_count(void) { return drops; }
int32_t test_split(int32_t value, int32_t* remainder) {
    *remainder = value % 10;
    return value / 10;
}
void test_bump(int32_t* value) { *value += 1; }
ZL_String test_last_error(void) { return zl_string_borrow("cannot open test window"); }
''',
        encoding="utf-8",
    )
    (tmp_path / "graphics.zlcm.h").write_text(
        '''ZLC_ABI(3)
ZLC_MODULE(graphics)
ZLC_SOURCE("graphics_zy.c")
ZLC_HANDLE(Window, ZLC_DROP(test_window_drop))
ZLC_HANDLE(View)
ZLC_ENUM(WindowMode, i32, ZLC_CASE(windowed, 0), ZLC_CASE(fullscreen, 1))
ZLC_FLAGS(WindowFlags, u32, ZLC_CASE(resizable, 4), ZLC_CASE(high_dpi, 8))
ZLC_CONST(DEFAULT_WIDTH, i32, 800)
ZLC_FN(open, test_window_open, optional<owned<Window>>, ZLC_PARAM(title, str), ZLC_PARAM(width, i32), ZLC_PARAM(height, i32), ZLC_FAIL(null, test_last_error))
ZLC_FN(maybe, test_window_maybe, optional<owned<Window>>, ZLC_PARAM(create, bool))
ZLC_FN(upload, test_window_upload, void, ZLC_PARAM(window, borrowed<Window>), ZLC_PARAM(data, Slice<u8>))
ZLC_FN(fill, test_fill, void, ZLC_PARAM(data, MutSlice<u8>))
ZLC_FN(view, test_window_view, borrowed<View, parent>, ZLC_PARAM(parent, borrowed<Window>))
ZLC_FN(view_total, test_view_total, i32, ZLC_PARAM(view, borrowed<View>))
ZLC_FN(total, test_window_total, i32, ZLC_PARAM(window, borrowed<Window>))
ZLC_FN(consume, test_window_consume, void, ZLC_PARAM(window, consumed<Window>))
ZLC_FN(wrong_type, test_window_wrong_type, owned<Window>)
ZLC_FN(drop_count, test_drop_count, i32)
ZLC_FN(split, test_split, i32, ZLC_PARAM(value, i32), ZLC_PARAM(remainder, out<i32>))
ZLC_FN(bump, test_bump, void, ZLC_PARAM(value, inout<i32>))
''',
        encoding="utf-8",
    )


def test_native_module_accepts_cpp_adapter_source(tmp_path: Path) -> None:
    (tmp_path / "math.cpp").write_text(
        '#include <stdint.h>\nextern "C" int32_t zy_cpp_sum(int32_t a, int32_t b) { return a + b; }\n',
        encoding="utf-8",
    )
    (tmp_path / "math.zlcm.h").write_text(
        '''ZLC_ABI(3)
ZLC_MODULE(math)
ZLC_SOURCE("math.cpp")
ZLC_FN(add, zy_cpp_sum, i32, ZLC_PARAM(a, i32), ZLC_PARAM(b, i32))
''',
        encoding="utf-8",
    )

    result = run_source(
        tmp_path,
        '''import std::c_module as c
native module math = c::load("math.zlcm.h")
fn main() i32 { return math::add(20, 22) - 42 }
''',
        name="native_cpp_module",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_native_module_handle_slice_enum_constant_and_failure(tmp_path: Path) -> None:
    write_graphics_fixture(tmp_path)
    result = run_source(
        tmp_path,
        '''import std::c_module as c

native module graphics = c::load("graphics.zlcm.h")

fn main() i32 {
    if graphics::DEFAULT_WIDTH != 800 || graphics::WindowMode::fullscreen != graphics::WindowMode::fullscreen {
        return 1
    }
    let flags = graphics::WindowFlags::resizable | graphics::WindowFlags::high_dpi
    if (flags & graphics::WindowFlags::high_dpi) != graphics::WindowFlags::high_dpi {
        return 10
    }
    let window: graphics::Window = graphics::open("Demo", 20, 22) catch err {
        return 2
    }
    let pixels: List<u8> = [1, 2, 3]
    let pixels_alias = pixels
    let empty: List<u8> = []
    graphics::upload(window, c::slice(&empty))
    graphics::upload(window, c::slice(&pixels))
    graphics::fill(c::mut_slice(&mut pixels))
    if LIST_GET__(pixels, 0) != 99 || LIST_GET__(pixels_alias, 0) != 1 {
        return 9
    }
    if graphics::total(window) != 48 {
        return 3
    }
    DROP__(window)
    if graphics::drop_count() != 1 {
        return 4
    }
    let (quotient, remainder) = graphics::split(427)
    if quotient != 42 || remainder != 7 {
        return 7
    }
    let changed = 41
    graphics::bump(&mut changed)
    if changed != 42 {
        return 8
    }
    graphics::open("bad", -1, 1) catch err {
        if err.message != "cannot open test window" {
            return 5
        }
        return 0
    }
    return 6
}
''',
        name="native_module",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_optional_native_handle_maps_null_and_owned_value(tmp_path: Path) -> None:
    write_graphics_fixture(tmp_path)
    result = run_source(
        tmp_path,
        '''import std::c_module as c
native module graphics = c::load("graphics.zlcm.h")
fn main() i32 {
    let missing: graphics::Window | null = graphics::maybe(false)
    if missing != null { return 2 }
    let present: graphics::Window | null = graphics::maybe(true)
    if present != null {
        if graphics::total(present) != 7 { return 4 }
    } else {
        return 3
    }
    return 0
}
''',
        name="optional_handle",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_module_requires_abi3_and_package_relative_path(tmp_path: Path) -> None:
    (tmp_path / "old.zlcm.h").write_text(
        "ZLC_MODULE(old)\nZLC_FN(value, old_value, i32)\n",
        encoding="utf-8",
    )
    source = tmp_path / "main.zy"
    source.write_text(
        'import std::c_module as c\nnative module old = c::load("old.zlcm.h")\nfn main() i32 { return 0 }\n',
        encoding="utf-8",
    )
    with pytest.raises(CompileError, match="ZLC_ABI\\(3\\)"):
        Compiler().check_file(source)


def test_consumed_handle_invalidates_every_alias(tmp_path: Path) -> None:
    write_graphics_fixture(tmp_path)
    result = run_source(
        tmp_path,
        '''import std::c_module as c
native module graphics = c::load("graphics.zlcm.h")
fn main() i32 {
    let window = graphics::open("Demo", 1, 2) catch err {
        return 2
    }
    let alias = window
    graphics::consume(window)
    return graphics::total(alias)
}
''',
        name="consumed_handle",
    )
    assert result.returncode != 0
    assert "native handle was consumed" in result.stderr


def test_consumed_payload_is_destroyed_once(tmp_path: Path) -> None:
    write_graphics_fixture(tmp_path)
    result = run_source(
        tmp_path,
        '''import std::c_module as c
native module graphics = c::load("graphics.zlcm.h")
fn main() i32 {
    let window = graphics::open("Demo", 1, 2) catch err { return 2 }
    let alias = window
    graphics::consume(window)
    DROP__(alias)
    DROP__(window)
    return graphics::drop_count() - 1
}
''',
        name="consumed_once",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_wrong_native_handle_type_tag_fails_before_adapter_use(tmp_path: Path) -> None:
    write_graphics_fixture(tmp_path)
    result = run_source(
        tmp_path,
        '''import std::c_module as c
native module graphics = c::load("graphics.zlcm.h")
fn main() i32 {
    let window = graphics::wrong_type()
    return graphics::total(window)
}
''',
        name="wrong_handle_tag",
    )
    assert result.returncode != 0
    assert "native handle type mismatch" in result.stderr


def test_borrowed_handle_retains_parent_until_view_is_dropped(tmp_path: Path) -> None:
    write_graphics_fixture(tmp_path)
    result = run_source(
        tmp_path,
        '''import std::c_module as c
native module graphics = c::load("graphics.zlcm.h")
fn main() i32 {
    let window = graphics::open("Demo", 20, 22) catch err {
        return 2
    }
    let view = graphics::view(window)
    DROP__(window)
    if graphics::drop_count() != 0 || graphics::view_total(view) != 42 {
        return 3
    }
    DROP__(view)
    if graphics::drop_count() != 1 {
        return 4
    }
    return 0
}
''',
        name="borrowed_parent",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_handle_fields_and_list_elements_retain_arc(tmp_path: Path) -> None:
    write_graphics_fixture(tmp_path)
    result = run_source(
        tmp_path,
        '''import std::c_module as c
native module graphics = c::load("graphics.zlcm.h")
struct Holder {
    public window: graphics::Window
}
fn main() i32 {
    let window = graphics::open("Demo", 1, 2) catch err {
        return 2
    }
    let holder = Holder{window: window}
    let windows: List<graphics::Window> = [window]
    DROP__(window)
    DROP__(holder)
    if graphics::drop_count() != 0 {
        return 3
    }
    LIST_CLEAR__(windows)
    if graphics::drop_count() != 1 {
        return 4
    }
    return 0
}
''',
        name="handle_containers",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_slice_cannot_escape_native_call(tmp_path: Path) -> None:
    source = tmp_path / "main.zy"
    source.write_text(
        'import std::c_module as c\nfn main() i32 {\n    let values: List<u8> = []\n    let escaped = c::slice(&values)\n    return 0\n}\n',
        encoding="utf-8",
    )
    with pytest.raises(CompileError, match="cannot be stored or returned"):
        Compiler().check_file(source)


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        ("ZLC_FN(bad, bad, Window)", "explicit ownership wrapper"),
        ("ZLC_FN(bad, bad, optional<i32>)", "requires a declared ZLC_HANDLE"),
        ("ZLC_FN(bad, bad, Slice<u8>)", "cannot be returned"),
        ("ZLC_CONST(BAD, i8, 999)", "does not fit"),
    ],
)
def test_abi3_rejects_ambiguous_or_invalid_public_types(tmp_path: Path, declaration: str, message: str) -> None:
    (tmp_path / "bad.zlcm.h").write_text(
        f"ZLC_ABI(3)\nZLC_MODULE(bad)\nZLC_HANDLE(Window)\n{declaration}\n",
        encoding="utf-8",
    )
    source = tmp_path / "main.zy"
    source.write_text(
        'import std::c_module as c\nnative module bad = c::load("bad.zlcm.h")\nfn main() i32 { return 0 }\n',
        encoding="utf-8",
    )
    with pytest.raises(CompileError, match=message):
        Compiler().check_file(source)


def test_bindgen_uses_clang_ast_marks_pointer_todo_and_protects_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zig = Path(r"D:\python_project\N_code\zyenlang_v0_1_49_windows_flat_direct\.portable\zig-release\zig.exe")
    if not zig.is_file():
        pytest.skip("bindgen parser fixture is unavailable")
    monkeypatch.setenv("ZY_BINDGEN_CLANG", f"{zig} cc")
    header = tmp_path / "tiny.h"
    header.write_text(
        "#include <stdint.h>\nint32_t tiny_add(int32_t left, int32_t right);\n"
        "void tiny_read(const uint8_t* data, size_t len);\n",
        encoding="utf-8",
    )
    output = tmp_path / "native"
    result = generate_bindings(header, "tiny", output)
    assert result.callable_count == 1
    assert result.todo_count == 1
    assert "ZLC_ABI(3)" in result.template.read_text(encoding="utf-8")
    assert "ZLC_FN(tiny_add" in result.template.read_text(encoding="utf-8")
    assert "TODO bindgen skipped" in result.template.read_text(encoding="utf-8")
    first = result.adapter_c.read_text(encoding="utf-8")
    again = generate_bindings(header, "tiny", output)
    assert again.adapter_c.read_text(encoding="utf-8") == first

    result.adapter_c.write_text(first + "// reviewed edit\n", encoding="utf-8")
    with pytest.raises(BindgenError, match="refusing to overwrite"):
        generate_bindings(header, "tiny", output)
    generate_bindings(header, "tiny", output, force=True)
    assert "reviewed edit" not in result.adapter_c.read_text(encoding="utf-8")


def test_bindgen_rejects_unsafe_define(tmp_path: Path) -> None:
    header = tmp_path / "tiny.h"
    header.write_text("int tiny(void);\n", encoding="utf-8")
    with pytest.raises(BindgenError, match="unsafe preprocessor define"):
        generate_bindings(header, "tiny", tmp_path / "out", defines=("X=1\nBAD=1",))


@pytest.mark.parametrize("kind", ["c-source", "staticlib", "sharedlib"])
def test_abi3_native_module_builds_library_artifacts(tmp_path: Path, kind: str) -> None:
    write_graphics_fixture(tmp_path)
    (tmp_path / "lib.zy").write_text(
        '''import std::c_module as c
native module graphics = c::load("graphics.zlcm.h")
public fn native_width() i32 { return graphics::DEFAULT_WIDTH }
''',
        encoding="utf-8",
    )
    (tmp_path / "zyproject.toml").write_text(
        f'''[package]
name = "abi3-artifact"
version = "0.1.0"
zyen = ">=0.3.1"

[build]
default-target = "ffi"
target-dir = "target"

[targets.ffi]
kind = "{kind}"
entry = "lib.zy"
output-name = "abi3_fixture"

[dependencies]
''',
        encoding="utf-8",
    )
    result = ArtifactBuilder(tmp_path).build("ffi")
    assert result.primary.is_file()
    if kind == "c-source":
        assert (result.output_dir / "graphics_zy.c").is_file()
        assert (result.output_dir / "abi3_fixture.metadata.json").is_file()


def test_legacy_module_is_supported_with_one_deprecation_warning(tmp_path: Path) -> None:
    (tmp_path / "math.zlcm.h").write_text(
        "ZLC_MODULE(math)\nZLC_FN(add, math_add, i32, ZLC_PARAM(a, i32), ZLC_PARAM(b, i32))\n",
        encoding="utf-8",
    )
    source = tmp_path / "main.zy"
    source.write_text(
        '''import std::c_module as c
fn main() i32 {
    let first: c::Module = c::load("math.zlcm.h")
    let second: c::Module = c::load("math.zlcm.h")
    return 0
}
''',
        encoding="utf-8",
    )
    program = Compiler().check_file(source)
    assert len(program.warnings) == 1
    assert "deprecated" in program.warnings[0]


def test_bindgen_review_config_generates_handle_slice_length_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zig = Path(r"D:\python_project\N_code\zyenlang_v0_1_49_windows_flat_direct\.portable\zig-release\zig.exe")
    if not zig.is_file():
        pytest.skip("bindgen parser fixture is unavailable")
    monkeypatch.setenv("ZY_BINDGEN_CLANG", f"{zig} cc")
    header = tmp_path / "window.h"
    header.write_text(
        "#include <stddef.h>\n#include <stdint.h>\n"
        "typedef struct TinyWindow TinyWindow;\n"
        "TinyWindow* tiny_open(int32_t width);\n"
        "void tiny_drop(TinyWindow* value);\n"
        "void tiny_upload(TinyWindow* value, const uint8_t* data, size_t len);\n",
        encoding="utf-8",
    )
    output = tmp_path / "native"
    generate_bindings(header, "tiny", output)
    config = output / "tiny.zybind.toml"
    config.write_text(
        '''[module]
name = "tiny"

[handles.Window]
c_type = "TinyWindow *"
drop = "tiny_drop"

[functions.tiny_open]
enabled = true
return_type = "optional<owned<Window>>"
failure = "null"
[functions.tiny_open.params]
width = "i32"

[functions.tiny_upload]
enabled = true
return_type = "void"
[functions.tiny_upload.params]
value = "borrowed<Window>"
data = "Slice<u8>"
len = "usize"
[functions.tiny_upload.lengths]
data = "len"
''',
        encoding="utf-8",
    )
    result = generate_bindings(header, "tiny", output, config_path=config, force=True)
    template = result.template.read_text(encoding="utf-8")
    adapter = result.adapter_c.read_text(encoding="utf-8")
    assert "ZLC_HANDLE(Window, ZLC_DROP(zy_bindgen_tiny_drop_Window))" in template
    assert "ZLC_FAIL(null)" in template
    assert "ZLC_PARAM(data, Slice<u8>)" in template
    assert "ZLC_PARAM(len" not in template
    assert "data.len" in adapter
    assert "zlcm:" in adapter
    assert "zl_handle_adopt" in adapter
