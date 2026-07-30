from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from zyenlang.v2 import CompileError, Compiler, CompilerOptions
from zyenlang.v2 import modules as v2_modules
from zyenlang.v2.__main__ import read_stdin_source


ROOT = Path(__file__).resolve().parents[1]


def test_v2_language_tour_checks_and_emits_typed_c() -> None:
    source = ROOT / "examples" / "v2_language_tour.zy"
    compiler = Compiler()
    program = compiler.check_file(source)
    generated = compiler.emit_file(source)

    assert any(function.name == "return_value" for function in program.functions)
    assert "zy2_result_i32" in generated
    assert "zy2_optional_i32" in generated
    assert "zy2_list_List_i16" in generated
    assert "zy2_method_Car_return_value" in generated
    assert "zy2_fn_list__length__List_i16" in generated
    assert "if ((actual == 7))" not in generated
    assert "if (actual == 7)" in generated


def test_v2_language_tour_runs() -> None:
    command = [sys.executable, "-m", "zyenlang.v2", "run", "examples/v2_language_tour.zy"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["car", "value must be positive", "tuple", "null", "some", "list"]


def test_v2_private_method_is_rejected_outside_its_struct() -> None:
    source = """
struct Secret {
    public value: i32
}

private fn (s: Secret) hidden() i32 {
    return s.value
}

fn main() i32 {
    let secret = Secret{value: 1}
    return secret.hidden()
}
"""
    with pytest.raises(CompileError, match="method `Secret.hidden` is private"):
        Compiler().check_source(source)


def test_v2_fixed_width_integer_range_is_checked() -> None:
    source = """
fn main() i32 {
    let too_large: i8 = 128
    return 0
}
"""
    with pytest.raises(CompileError, match="does not fit `i8`"):
        Compiler().check_source(source)


def test_v2_rejects_arbitrary_union_types() -> None:
    source = """
fn main() i32 {
    let value: i32 | str = 1
    return 0
}
"""
    with pytest.raises(CompileError, match="only optional unions"):
        Compiler().check_source(source)


def test_v2_catch_requires_a_complete_handler() -> None:
    source = """
fn fail() i32 throws Error {
    stop "failed"
}

fn main() i32 {
    let value = fail() catch err {
        let message: str = err.message
    }
    return value
}
"""
    with pytest.raises(CompileError, match="catch must end with recover"):
        Compiler().check_source(source)


def test_v2_generic_functions_are_monomorphized() -> None:
    source = """
fn identity<T>(value: T) T {
    return value
}

fn main() i32 {
    let number: i32 = identity(12)
    return number
}
"""
    generated = Compiler().emit_source(source)
    assert "zy2_fn_identity__i32" in generated


@pytest.mark.parametrize(
    "module_name",
    [
        "list.zy",
        "error.zy",
        "option.zy",
        "io.zy",
        "process.zy",
        "thread.zy",
        "request.zy",
        "server.zy",
        "gui.zy",
    ],
)
def test_v2_standard_library_modules_type_check(module_name: str) -> None:
    compiler = Compiler(CompilerOptions(require_main=False))
    compiler.check_file(ROOT / "zyenlang" / "v2" / "std" / module_name)


def test_v2_if_let_unwraps_optional_values() -> None:
    source = """
fn main() i32 {
    let value: i32 | null = 7
    if let number = value {
        return number
    } else {
        return 1
    }
}
"""
    generated = Compiler().emit_source(source)
    assert ".has_value" in generated
    assert ".value" in generated


def test_v2_semicolons_are_rejected() -> None:
    with pytest.raises(CompileError, match="semicolons were removed"):
        Compiler().check_source("fn main() i32 { return 0; }")


def test_v2_strong_list_rejects_mixed_element_types() -> None:
    source = """
fn main() i32 {
    let values: List<i32> = [1, "two"]
    return 0
}
"""
    with pytest.raises(CompileError, match="expected `i32`, got `str`"):
        Compiler().check_source(source)


def test_v2_unhandled_error_prints_and_exits_nonzero(tmp_path: Path) -> None:
    source = tmp_path / "unhandled.zy"
    source.write_text(
        """
fn fail() i32 throws Error {
    stop "unhandled test"
}

fn main() i32 {
    return fail()
}
""",
        encoding="utf-8",
    )
    compiler = Compiler()
    executable = tmp_path / ("unhandled.exe" if sys.platform.startswith("win") else "unhandled")
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert result.stderr.strip() == f"{source.resolve()}:3:5: unhandled test"


def test_v2_compile_error_reports_exact_source_line() -> None:
    source = "fn main() i32 {\n    let value: i8 = 128\n    return 0\n}\n"

    with pytest.raises(CompileError) as caught:
        Compiler().check_source(source, "seven-lines.zy")

    assert caught.value.span is not None
    assert caught.value.span.line == 2
    assert str(caught.value).startswith("seven-lines.zy:2:21:")


def test_v2_imported_module_error_reports_imported_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.zy"
    broken.write_text("public fn bad() i32 {\n    return missing\n}\n", encoding="utf-8")
    main = tmp_path / "main.zy"
    main.write_text(
        'import "broken.zy" as broken\n\nfn main() i32 {\n    return broken.bad()\n}\n',
        encoding="utf-8",
    )

    with pytest.raises(CompileError) as caught:
        Compiler().check_file(main)

    assert caught.value.source_name == str(broken.resolve())
    assert caught.value.span is not None and caught.value.span.line == 2


def test_v2_circular_import_reports_the_import_site(tmp_path: Path) -> None:
    first = tmp_path / "first.zy"
    second = tmp_path / "second.zy"
    first.write_text('import "second.zy" as second\n', encoding="utf-8")
    second.write_text('import "first.zy" as first\n', encoding="utf-8")

    with pytest.raises(CompileError, match="circular import") as caught:
        Compiler(CompilerOptions(require_main=False)).check_file(first)

    assert caught.value.source_name == str(second.resolve())
    assert caught.value.span is not None and caught.value.span.line == 1


def test_v2_rejects_recursive_by_value_structs() -> None:
    source = """struct First {
    second: Second
}

struct Second {
    first: First
}

fn main() i32 {
    return 0
}
"""
    with pytest.raises(CompileError, match="recursive by-value struct layout: First -> Second -> First"):
        Compiler().check_source(source, "recursive.zy")


def test_v2_bare_print_is_not_a_language_builtin() -> None:
    source = "fn main() i32 {\n    print(\"use std/io\")\n    return 0\n}\n"

    with pytest.raises(CompileError, match="unknown function `print`"):
        Compiler().check_source(source)


def test_v2_accepts_utf8_bom_without_shifting_lines() -> None:
    source = "\ufefffn main() i32 {\n    let value: i8 = 128\n    return 0\n}\n"

    with pytest.raises(CompileError) as caught:
        Compiler().check_source(source, "bom.zy")

    assert caught.value.span is not None and caught.value.span.line == 2


def test_v2_nested_module_references_keep_the_full_namespace(tmp_path: Path) -> None:
    leaf = tmp_path / "leaf.zy"
    leaf.write_text("public fn answer() i32 {\n    return 42\n}\n", encoding="utf-8")
    middle = tmp_path / "middle.zy"
    middle.write_text(
        'import "leaf.zy" as leaf\n\npublic fn answer() i32 {\n    return leaf.answer()\n}\n',
        encoding="utf-8",
    )
    main = tmp_path / "main.zy"
    main.write_text(
        'import "middle.zy" as middle\n\nfn main() i32 {\n    return middle.answer() - 42\n}\n',
        encoding="utf-8",
    )

    executable = tmp_path / ("nested.exe" if sys.platform.startswith("win") else "nested")
    Compiler().build_file(main, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_mutually_recursive_functions_are_supported() -> None:
    source = """fn is_even(value: i32) bool {
    if value == 0 {
        return true
    }
    return is_odd(value - 1)
}

fn is_odd(value: i32) bool {
    if value == 0 {
        return false
    }
    return is_even(value - 1)
}

fn main() i32 {
    if is_even(10) {
        return 0
    }
    return 1
}
"""
    generated = Compiler().emit_source(source)

    assert "zy2_fn_is_even" in generated
    assert "zy2_fn_is_odd" in generated


def test_v2_get_args_and_get_exe_use_process_arguments(tmp_path: Path) -> None:
    source = tmp_path / "args.zy"
    source.write_text(
        """import <std/list> as list

fn main() i32 {
    let args: List<str> = GET_ARGS
    let executable: str = GET_EXE
    if list.length(args) == 2 && executable != "" {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("args.exe" if sys.platform.startswith("win") else "args")
    Compiler().build_file(source, executable)

    result = subprocess.run(
        [str(executable), "alpha", "beta"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_list_get_is_typed_and_reads_process_arguments(tmp_path: Path) -> None:
    source = tmp_path / "list_get.zy"
    source.write_text(
        """fn main() i32 {
    let args: List<str> = GET_ARGS
    let first: str = args.get(0) catch err {
        recover "missing"
    }
    if first == "alpha" {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("list_get.exe" if sys.platform.startswith("win") else "list_get")
    Compiler().build_file(source, executable)

    result = subprocess.run([str(executable), "alpha"], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_list_get_reports_a_source_location_for_out_of_bounds(tmp_path: Path) -> None:
    source = tmp_path / "list_oob.zy"
    source.write_text(
        """fn main() i32 {
    let values: List<i32> = [10]
    let value: i32 = values.get(1)
    return value
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("list_oob.exe" if sys.platform.startswith("win") else "list_oob")
    Compiler().build_file(source, executable)

    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert "list_oob.zy:3:22: List index out of range" in result.stderr.replace("\\", "/")


def test_v2_list_get_rejects_invalid_arguments_during_check() -> None:
    wrong_arity = "fn main() i32 {\n    let xs: List<i32> = [1]\n    let x = xs.get()\n    return 0\n}\n"
    wrong_type = 'fn main() i32 {\n    let xs: List<i32> = [1]\n    let x = xs.get("0")\n    return 0\n}\n'

    with pytest.raises(CompileError, match="List.get expects exactly one i32 index"):
        Compiler().check_source(wrong_arity)
    with pytest.raises(CompileError, match="expected `i32`, got `str`"):
        Compiler().check_source(wrong_type)


def test_v2_editor_buffer_edits_utf8_text_and_saves(tmp_path: Path) -> None:
    source = ROOT / "tests" / "v2_editor_buffer_test.zy"
    executable = tmp_path / ("editor_buffer.exe" if sys.platform.startswith("win") else "editor_buffer")
    Compiler().build_file(source, executable)
    (tmp_path / "picked-input.zy").write_text("fn picked() i32 { return 9 }", encoding="utf-8")

    result = subprocess.run([str(executable)], cwd=tmp_path, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "editor-buffer-output.zy").read_text(encoding="utf-8") == "fn calculate() i32 {\n    return 42\n}\ncalculate"


def test_v2_process_special_values_are_typed_and_reserved() -> None:
    wrong_type = "fn main() i32 {\n    let args: i32 = GET_ARGS\n    return 0\n}\n"
    shadowed = "fn main() i32 {\n    let GET_ARGS: i32 = 1\n    return 0\n}\n"

    with pytest.raises(CompileError, match="expected `i32`, got `List<str>`"):
        Compiler().check_source(wrong_type)
    with pytest.raises(CompileError, match="reserved process value"):
        Compiler().check_source(shadowed)


def test_v2_check_file_source_uses_unsaved_root_text_and_disk_imports(tmp_path: Path) -> None:
    module = tmp_path / "math.zy"
    module.write_text("public fn answer() i32 {\n    return 42\n}\n", encoding="utf-8")
    root = tmp_path / "main.zy"
    root.write_text("fn main() i32 {\n    return 1\n}\n", encoding="utf-8")
    unsaved = 'import "math.zy" as math\n\nfn main() i32 {\n    return math.answer()\n}\n'

    program = Compiler().check_file_source(root, unsaved)

    assert any(function.name == "main" for function in program.functions)
    assert any(function.name == "math.answer" for function in program.functions)


def test_v2_check_cli_accepts_unsaved_source_from_stdin(tmp_path: Path) -> None:
    source = tmp_path / "stdin_check.zy"
    source.write_text("fn main() i32 {\n    return 0\n}\n", encoding="utf-8")
    unsaved = "fn helper() i32 {\n    return 7\n}\n"

    result = subprocess.run(
        [sys.executable, "-m", "zyenlang.v2", "check", str(source), "--library", "--stdin"],
        cwd=ROOT,
        input=unsaved,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_public_native_declarations_compile_and_link(tmp_path: Path) -> None:
    native = tmp_path / "math_native.c"
    native.write_text("int native_add(int left, int right) { return left + right; }\n", encoding="utf-8")
    source = tmp_path / "main.zy"
    source.write_text(
        """native source "math_native.c"
private native fn add(left: i32, right: i32) i32 = "native_add"

fn main() i32 {
    return add(20, 22) - 42
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("native.exe" if sys.platform.startswith("win") else "native")

    program = Compiler().check_file(source)
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert program.native_sources == (str(native.resolve()),)
    assert program.extern_functions[0].c_name == "native_add"
    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_native_declarations_reject_symbol_and_path_injection(tmp_path: Path) -> None:
    source = tmp_path / "bad.zy"
    source.write_text(
        'native source "missing.c"\nprivate native fn bad(value: i32) i32 = "bad); system"\n',
        encoding="utf-8",
    )

    with pytest.raises(CompileError, match="invalid native C symbol"):
        Compiler(CompilerOptions(require_main=False)).check_file(source)


def test_v2_thread_module_builds_and_runs(tmp_path: Path) -> None:
    executable = tmp_path / ("thread.exe" if sys.platform.startswith("win") else "thread")
    Compiler().build_file(ROOT / "examples" / "v2_thread_basic.zy", executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_request_module_uses_native_transport(tmp_path: Path) -> None:
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "request_fixture_server.py"), "0"],
        cwd=ROOT,
        creationflags=creationflags,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert server.stdout is not None and server.stderr is not None
        line = server.stdout.readline()
        if not line:
            error = server.stderr.read().strip()
            code = server.wait(timeout=3)
            raise RuntimeError(f"request fixture server exited with {code}: {error}")
        port = int(line.strip())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("request fixture server did not start")

        source = ROOT / "tests" / "v2_request_test.zy"
        test_source = tmp_path / "v2_request_test.zy"
        test_source.write_text(
            source.read_text(encoding="utf-8").replace("127.0.0.1:18765", f"127.0.0.1:{port}"),
            encoding="utf-8",
        )
        executable = tmp_path / ("request.exe" if sys.platform.startswith("win") else "request")
        Compiler().build_file(test_source, executable)
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=20, check=False)

        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=3)


def test_v2_server_module_serves_one_http_request(tmp_path: Path) -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    source = tmp_path / "server.zy"
    source.write_text(
        f"""import <std/server> as server
import <std/io> as io

fn main() i32 {{
    let handled: i32 = server.serve_once("127.0.0.1", {port}, "hello from zy2 server") catch err {{
        io.eprint(err.message)
        recover -1
    }}
    if handled == 1 {{
        return 0
    }}
    return 1
}}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("server.exe" if sys.platform.startswith("win") else "server")
    Compiler().build_file(source, executable)
    process = subprocess.Popen([str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/", timeout=0.3) as response:
                    body = response.read().decode("utf-8")
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        stdout, stderr = process.communicate(timeout=5)

        assert body == "hello from zy2 server"
        assert process.returncode == 0, stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_v2_gui_module_links_without_opening_a_window(tmp_path: Path) -> None:
    source = tmp_path / "gui.zy"
    source.write_text(
        """import <std/gui> as gui

fn main() i32 {
    let event: str = "mouse\\t12\\t34"
    if gui.event_kind(event) == 1 && gui.event_x(event) == 12 && gui.event_y(event) == 34 {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("gui.exe" if sys.platform.startswith("win") else "gui")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_spawn_runs_on_an_os_thread_and_awaits_once(tmp_path: Path) -> None:
    executable = tmp_path / ("task.exe" if sys.platform.startswith("win") else "task")
    Compiler().build_file(ROOT / "examples" / "v2_task_basic.zy", executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("let task = spawn work()\n    return 0", "must be awaited exactly once"),
        (
            "let task = spawn work()\n    let first = await task\n    let second = await task\n    return first + second",
            "has already been awaited",
        ),
        (
            "let task = spawn work()\n    let copied = task\n    let result = await task\n    return result",
            "can only be consumed with `await task`",
        ),
    ],
)
def test_v2_task_linearity_is_checked(body: str, message: str) -> None:
    source = f"""fn work() i32 {{
    return 1
}}

fn main() i32 {{
    {body}
}}
"""
    with pytest.raises(CompileError, match=message):
        Compiler().check_source(source)


def test_v2_while_assignment_break_and_continue_execute(tmp_path: Path) -> None:
    source = tmp_path / "loop.zy"
    source.write_text(
        """fn main() i32 {
    let value: i32 = 0
    let total: i32 = 0
    while value < 10 {
        value = value + 1
        if value == 3 {
            continue
        }
        total = total + value
        if value == 5 {
            break
        }
    }
    return total - 12
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("loop.exe" if sys.platform.startswith("win") else "loop")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("keyword", ["break", "continue"])
def test_v2_loop_control_is_rejected_outside_loop(keyword: str) -> None:
    source = f"fn main() i32 {{\n    {keyword}\n    return 0\n}}\n"

    with pytest.raises(CompileError, match="only valid inside a loop"):
        Compiler().check_source(source)


def test_v2_typeof_is_a_compile_time_boolean_expression(tmp_path: Path) -> None:
    source = tmp_path / "typeof.zy"
    source.write_text(
        """fn make_value() i32 {
    return 42
}

fn main() i32 {
    let value: i32 = 12
    if typeof value i32 && !(typeof value str) && typeof "hello" str && typeof [1, 2] List<i32> && typeof (make_value()) i32 {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("typeof.exe" if sys.platform.startswith("win") else "typeof")
    generated = Compiler().emit_file(source)
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert "true" in generated
    assert "false" in generated
    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_typeof_reports_unknown_target_type_at_its_line() -> None:
    source = "fn main() i32 {\n    let value = typeof 1 MissingType\n    return 0\n}\n"

    with pytest.raises(CompileError, match="unknown type `MissingType`") as caught:
        Compiler().check_source(source, "typeof_error.zy")

    assert caught.value.span is not None and caught.value.span.line == 2


def test_v2_rejects_oversized_source_overlays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v2_modules, "MAX_SOURCE_BYTES", 16)
    source = tmp_path / "large.zy"

    with pytest.raises(CompileError, match="source exceeds the 16-byte safety limit"):
        Compiler().check_file_source(source, "fn main() i32 {\n    return 0\n}\n")


def test_v2_rejects_excessive_import_depth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v2_modules, "MAX_MODULE_DEPTH", 2)
    for index in range(4):
        body = f'import "module{index + 1}.zy" as next\n' if index < 3 else "public fn value() i32 { return 1 }\n"
        (tmp_path / f"module{index}.zy").write_text(body, encoding="utf-8")

    with pytest.raises(CompileError, match="import depth exceeds the safety limit"):
        Compiler(CompilerOptions(require_main=False)).check_file(tmp_path / "module0.zy")


def test_v2_stdin_reader_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from io import StringIO

    monkeypatch.setattr(sys, "stdin", StringIO("0123456789"))
    with pytest.raises(CompileError, match="stdin source exceeds the 8-byte safety limit"):
        read_stdin_source("editor.zy", limit=8)


def test_v2_native_link_names_cannot_be_compiler_flags() -> None:
    source = 'native link "-Wl-attack"\npublic fn value() i32 { return 1 }\n'

    with pytest.raises(CompileError, match="may not contain compiler flags"):
        Compiler(CompilerOptions(require_main=False)).check_source(source)
