from __future__ import annotations

import os
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
    assert "zy2_list_List_i16_len(matrix)" in generated
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


def test_v2_explicit_casts_emit_and_run(tmp_path: Path) -> None:
    source = tmp_path / "casts.zy"
    source.write_text(
        """import <std/io> as io

fn fail() void throws Error {
    stop "cast error"
}

fn main() i32 {
    let wide: i64 = (i64)42
    let floating: f64 = (f64)wide
    let narrowed: i32 = (i32)floating
    let negative: i8 = (i8)-1
    let truth: bool = (bool)narrowed
    let one: i32 = (i32)truth
    let values: List<i32> = [1, 2]
    let same_values: List<i32> = (List<i32>)values
    io.print((str)truth)
    io.print((str)negative)
    io.print((str)wide)
    io.print((str)floating)
    fail() catch err {
        io.print((str)err.message)
        recover
    }
    if narrowed == 42 && negative == -1 && one == 1 && same_values.len() == 2 && (narrowed) - 40 == 2 {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )
    compiler = Compiler()
    generated = compiler.emit_file(source)
    executable = tmp_path / ("casts.exe" if sys.platform.startswith("win") else "casts")
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["true", "-1", "42", "42", "cast error"]
    assert "snprintf" in generated
    assert ' ? "true" : "false"' in generated
    assert "!= 0" in generated


def test_v2_optional_str_cast_is_safe_and_evaluates_once(tmp_path: Path) -> None:
    source = tmp_path / "optional_str_cast.zy"
    source.write_text(
        """fn main() i32 {
    let some: str | null = "hello"
    let none: str | null = null
    let some_text: str = (str)some
    let none_text: str = (str)none
    if some_text == "hello" && none_text == "null" {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )

    generated = Compiler().emit_file(source)
    executable = tmp_path / ("optional-str-cast.exe" if sys.platform.startswith("win") else "optional-str-cast")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert generated.count(".has_value ?") == 2


def test_v2_blocks_use_lexical_scope_without_erasing_outer_variables(tmp_path: Path) -> None:
    valid = tmp_path / "lexical_scope.zy"
    valid.write_text("""fn main() i32 {
    let outer: i32 = 1
    if true {
        outer = 2
        let inside: i32 = 3
    }
    return outer
}
""", encoding="utf-8")
    executable = tmp_path / ("lexical-scope.exe" if sys.platform.startswith("win") else "lexical-scope")
    Compiler().build_file(valid, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)
    assert result.returncode == 2

    invalid = """fn main() i32 {
    if true {
        let inside: i32 = 3
    }
    return inside
}
"""
    with pytest.raises(CompileError, match="unknown name `inside`"):
        Compiler().check_source(invalid)


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ('(i32)"12"', "cannot cast `str` to `i32`"),
    ],
)
def test_v2_explicit_casts_reject_undefined_conversions(expression: str, message: str) -> None:
    source = f"fn main() i32 {{\n    let value = {expression}\n    return 0\n}}\n"
    with pytest.raises(CompileError, match=message):
        Compiler().check_source(source)


def test_v2_error_requires_explicit_message_field_for_string_conversion() -> None:
    source = """fn fail() void throws Error {
    stop "failed"
}

fn main() i32 {
    fail() catch err {
        let invalid: str = (str)err
        recover
    }
    return 0
}
"""

    with pytest.raises(CompileError, match="cannot cast `Error` to `str`"):
        Compiler().check_source(source)


def test_v2_mixed_numeric_comparisons_are_safe_and_automatic(tmp_path: Path) -> None:
    source = tmp_path / "mixed_comparisons.zy"
    source.write_text(
        """fn main() i32 {
    let negative: i32 = -1
    let zero: usize = 0
    let three: i8 = 3
    let three_unsigned: u64 = 3
    let signed_positive: i64 = 4
    let small_unsigned: u8 = 3
    let unsigned_max: u64 = 18_446_744_073_709_551_615
    let signed_max: i64 = 9_223_372_036_854_775_807
    let floating: f64 = (f64)3

    if negative < zero && zero > negative && three == three_unsigned && signed_positive > small_unsigned && unsigned_max > signed_max && floating == three {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )

    compiler = Compiler()
    generated = compiler.emit_file(source)
    executable = tmp_path / ("mixed-comparisons.exe" if sys.platform.startswith("win") else "mixed-comparisons")
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "zy2_compare_i64_u64_lt" in generated
    assert "zy2_compare_i64_u64_gt" in generated


def test_v2_mixed_integer_and_float_arithmetic_promotes_and_runs(tmp_path: Path) -> None:
    source = tmp_path / "mixed_arithmetic.zy"
    source.write_text(
        """fn main() i32 {
    let integer: i32 = 7
    let floating: f64 = (f64)2
    let sum: f64 = integer + floating
    let reverse: f64 = floating + integer
    let difference: f64 = integer - floating
    let product: f64 = integer * floating
    let quotient: f64 = integer / floating

    if sum == (f64)9 && reverse == (f64)9 && difference == (f64)5 && product == (f64)14 && quotient > (f64)3 && quotient < (f64)4 {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )

    compiler = Compiler()
    generated = compiler.emit_file(source)
    executable = tmp_path / ("mixed-arithmetic.exe" if sys.platform.startswith("win") else "mixed-arithmetic")
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "((double)(integer))" in generated


def test_v2_mixed_integer_arithmetic_uses_a_lossless_common_type(tmp_path: Path) -> None:
    source = tmp_path / "integer_promotion.zy"
    source.write_text(
        """fn main() i32 {
    let signed_small: i8 = -1
    let unsigned_small: u8 = 2
    let small_result: i16 = signed_small + unsigned_small
    let signed_wide: i32 = -1
    let unsigned_wide: u32 = 2
    let wide_result: i64 = signed_wide + unsigned_wide

    if small_result == 1 && wide_result == 1 {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("integer-promotion.exe" if sys.platform.startswith("win") else "integer-promotion")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_mixed_integer_arithmetic_rejects_no_lossless_common_type() -> None:
    source = """fn main() i32 {
    let signed: i64 = -1
    let unsigned: u64 = 1
    let invalid = signed + unsigned
    return 0
}
"""

    with pytest.raises(CompileError, match="no lossless common numeric type for `i64` and `u64`"):
        Compiler().check_source(source)


def test_v2_modulo_rejects_float_operands_during_check() -> None:
    source = """fn main() i32 {
    let floating: f64 = (f64)5
    let invalid = floating % (f64)2
    return 0
}
"""

    with pytest.raises(CompileError, match="modulo operands must be integers"):
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


def test_v2_generic_struct_equality_is_structural_and_runs(tmp_path: Path) -> None:
    source = tmp_path / "generic_struct_equality.zy"
    source.write_text(
        """import <std/io>

fn is_same<T>(a: T, b: T) bool {
    return a == b
}

struct Engine {
    public serial: str
    public cylinders: i32
}

struct Car {
    public value: i32
    public name: str
    public engine: Engine
}

fn main() i32 {
    let first = Car{value: 12, name: "ember", engine: Engine{serial: "A-1", cylinders: 4}}
    let different = Car{value: 10, name: "ember", engine: Engine{serial: "A-1", cylinders: 4}}
    let copy = Car{value: 12, name: "ember", engine: Engine{serial: "A-1", cylinders: 4}}
    io.print((str)is_same(first, different))
    io.print((str)is_same(first, copy))
    if is_same(first, different) || !is_same(first, copy) {
        return 1
    }
    return 0
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / (
        "generic-struct-equality.exe" if sys.platform.startswith("win") else "generic-struct-equality"
    )
    compiler = Compiler()
    generated = compiler.emit_file(source)
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["false", "true"]
    assert "strcmp" in generated
    assert "zy2_fn_is_same__Car" in generated


def test_v2_tuple_and_optional_equality_are_structural_and_run(tmp_path: Path) -> None:
    source = tmp_path / "aggregate_equality.zy"
    source.write_text(
        """fn main() i32 {
    let first: (i32, str) = (12, "same")
    let second: (i32, str) = (12, "same")
    let third: (i32, str) = (10, "different")
    let some: i32 | null = 12
    let same_some: i32 | null = 12
    let none: i32 | null = null

    if first == second && first != third && some == same_some && some != none && none == null {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("aggregate-equality.exe" if sys.platform.startswith("win") else "aggregate-equality")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_generic_equality_rejects_noncomparable_concrete_type_during_check() -> None:
    source = """fn is_same<T>(a: T, b: T) bool {
    return a == b
}

fn main() i32 {
    let first: List<i32> = [1]
    let second: List<i32> = [1]
    if is_same(first, second) {
        return 1
    }
    return 0
}
"""

    with pytest.raises(CompileError, match="equality is not defined for `List<i32>`"):
        Compiler().check_source(source)


def test_v2_default_parameters_fill_missing_arguments_and_run(tmp_path: Path) -> None:
    source = tmp_path / "default_parameters.zy"
    source.write_text(
        """fn all_defaults(a: i32 = 10, b: i32 = 20) i32 {
    return a + b
}

fn compose(a: i32 = 1, b: i32, c: i32 = 3) i32 {
    return a * 100 + b * 10 + c
}

fn main() i32 {
    if all_defaults() != 30 {
        return 1
    }
    if compose(2) != 123 {
        return 2
    }
    if compose(4, 5) != 453 {
        return 3
    }
    if compose(4, 5, 6) != 456 {
        return 4
    }
    return 0
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("default_parameters.exe" if sys.platform.startswith("win") else "default_parameters")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_imported_function_defaults_are_namespaced_and_run(tmp_path: Path) -> None:
    module = tmp_path / "math_defaults.zy"
    module.write_text(
        """public fn add(a: i32 = 20, b: i32 = 22) i32 {
    return a + b
}
""",
        encoding="utf-8",
    )
    source = tmp_path / "main.zy"
    source.write_text(
        """import "math_defaults.zy" as math

fn main() i32 {
    return math.add() - 42
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("imported_defaults.exe" if sys.platform.startswith("win") else "imported_defaults")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_default_parameter_type_is_checked_even_when_unused() -> None:
    source = """fn invalid(value: i32 = "wrong") i32 {
    return value
}

fn main() i32 {
    return 0
}
"""
    with pytest.raises(CompileError, match="expected `i32`, got `str`"):
        Compiler().check_source(source)


def test_v2_generic_template_requires_explicit_mixed_type_cast() -> None:
    source = """fn add<T>(a: i32 = 10, b: T) T {
    return a + b
}

fn main() i32 {
    return add(10, 10)
}
"""
    with pytest.raises(CompileError) as caught:
        Compiler().check_source(source)

    assert "generic value `b: T` must be explicitly cast to `i32`" in str(caught.value)


def test_v2_generic_default_parameter_with_explicit_cast_runs(tmp_path: Path) -> None:
    source = tmp_path / "generic_default.zy"
    source.write_text(
        """fn add<T>(a: i32 = 10, b: T) T {
    return (T)(a + (i32)b)
}

fn main() i32 {
    if add(5) != 15 {
        return 1
    }
    return add(7, 8) - 15
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("generic_default.exe" if sys.platform.startswith("win") else "generic_default")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "module_name",
    [
        "list.zy",
        "error.zy",
        "option.zy",
        "io.zy",
        "process.zy",
        "path.zy",
        "thread.zy",
        "request.zy",
        "server.zy",
        "gui.zy",
        "editor.zy",
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


def test_v2_optional_integer_literal_and_unused_if_let_build(tmp_path: Path) -> None:
    source = tmp_path / "optional_literal.zy"
    source.write_text(
        """fn find(found: bool) u8 | null {
    if found {
        return 255
    }
    return null
}

fn main() i32 {
    if let unused = find(false) {
        return 1
    }
    if let value = find(true) {
        return (i32)value - 255
    }
    return 2
}
""",
        encoding="utf-8",
    )
    compiler = Compiler()
    generated = compiler.emit_file(source)
    executable = tmp_path / ("optional_literal.exe" if sys.platform.startswith("win") else "optional_literal")
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert ".has_value = true" in generated
    assert "(void)unused;" in generated


def test_v2_optional_integer_literal_respects_inner_range() -> None:
    source = "fn bad() u8 | null {\n    return 256\n}\nfn main() i32 {\n    return 0\n}\n"
    with pytest.raises(CompileError, match="does not fit `u8`"):
        Compiler().check_source(source)


@pytest.mark.parametrize(
    "construction",
    ["Box{value: 5}", "Box<i32>{value: 5}"],
)
def test_v2_generic_struct_construction_has_milestone_diagnostic(construction: str) -> None:
    source = f"struct Box<T> {{\n    value: T\n}}\nfn main() i32 {{\n    let box: Box<i32> = {construction}\n    return 0\n}}\n"
    with pytest.raises(CompileError, match="generic struct construction is scheduled after the bootstrap milestone"):
        Compiler().check_source(source)


def test_v2_parenthesized_tuple_return_runs(tmp_path: Path) -> None:
    source = tmp_path / "tuple_return.zy"
    source.write_text(
        """fn pair() (i32, str) {
    return (12, "tuple")
}

fn main() i32 {
    let (number: i32, text: str) = pair()
    if number == 12 && text == "tuple" {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("tuple_return.exe" if sys.platform.startswith("win") else "tuple_return")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_struct_metadata_lists_attributes_and_methods(tmp_path: Path) -> None:
    source = tmp_path / "struct_metadata.zy"
    source.write_text(
        """struct Player {
    public hp: i32
    private name: str = "player"
}

public fn (player: Player) update() i32 {
    return player.hp
}

private fn (player: Player) reset() i32 {
    return 0
}

fn main() i32 {
    let player = Player{hp: 100}
    let attributes: List<str> = player.__attributes__
    let methods: List<str> = player.__methods__
    let first_attribute: str = attributes.get(0) catch err {
        return 1
    }
    let second_attribute: str = attributes.get(1) catch err {
        return 2
    }
    let first_method: str = methods.get(0) catch err {
        return 3
    }
    let second_method: str = methods.get(1) catch err {
        return 4
    }
    if attributes.len() == 2 && methods.len() == 2 && first_attribute == "hp" && second_attribute == "name" && first_method == "update" && second_method == "reset" {
        return 0
    }
    return 5
}
""",
        encoding="utf-8",
    )
    compiler = Compiler()
    generated = compiler.emit_file(source)
    executable = tmp_path / ("struct_metadata.exe" if sys.platform.startswith("win") else "struct_metadata")
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert 'zy2_meta_Player_attributes[] = { "hp", "name" }' in generated
    assert 'zy2_meta_Player_methods[] = { "update", "reset" }' in generated


@pytest.mark.parametrize("reserved", ["__attributes__", "__methods__"])
def test_v2_struct_metadata_names_are_reserved(reserved: str) -> None:
    source = f"struct Bad {{\n    {reserved}: i32\n}}\nfn main() i32 {{\n    return 0\n}}\n"
    with pytest.raises(CompileError, match="reserved for struct metadata"):
        Compiler().check_source(source)


def test_v2_box_arc_copies_assigns_returns_and_cleans_scopes(tmp_path: Path) -> None:
    source = tmp_path / "box_arc.zy"
    source.write_text(
        """struct Point {
    x: i32
}

fn read(value: Box<i32>) i32 {
    return value.value
}

fn identity(value: Box<i32>) Box<i32> {
    return value
}

fn make() Box<i32> {
    return Box(42)
}

fn maybe(found: bool) Box<i32> throws Error {
    let guard = Box(99)
    if found {
        return Box(5)
    }
    stop "missing box"
}

fn main() i32 {
    let root = make()
    if root.__strong_count__ != 1 {
        return 1
    }
    if true {
        let alias = root
        if root.__strong_count__ != 2 {
            return 2
        }
        alias.value = 43
    }
    if root.__strong_count__ != 1 || root.value != 43 {
        return 3
    }

    let returned = identity(root)
    if root.__strong_count__ != 2 || root != returned {
        return 4
    }
    returned = returned
    if root.__strong_count__ != 2 {
        return 5
    }
    returned = Box(7)
    if root.__strong_count__ != 1 || returned.value != 7 {
        return 6
    }
    if read(Box(9)) != 9 || Box(11).value != 11 {
        return 7
    }
    if Box(1) == Box(1) || Box(2).__strong_count__ != 1 {
        return 11
    }

    let recovered = maybe(false) catch err {
        recover Box(6)
    }
    if recovered.value != 6 {
        return 8
    }

    let point = Box(Point{x: 10})
    point.value.x = 12
    if point.value.x != 12 {
        return 9
    }

    let iteration: i32 = 0
    while iteration < 2 {
        let loop_alias = root
        iteration = iteration + 1
        if iteration == 1 {
            continue
        }
        break
    }
    if root.__strong_count__ != 1 {
        return 10
    }
    return root.value - 43
}
""",
        encoding="utf-8",
    )
    compiler = Compiler()
    generated = compiler.emit_file(source)
    executable = tmp_path / ("box_arc.exe" if sys.platform.startswith("win") else "box_arc")
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "zy2_arc_retain" in generated
    assert "zy2_arc_release" in generated
    assert "zy2_arc_assert_clean" in generated
    assert "zy2_box_i32_retain" in generated
    assert "zy2_box_i32_release" in generated


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "struct Bad {\n    value: Box<i32>\n}\nfn main() i32 {\n    return 0\n}\n",
            "Box<T> fields require managed aggregate destructors",
        ),
        (
            "fn main() i32 {\n    let nested = Box(Box(1))\n    return 0\n}\n",
            "needs a managed destructor that is not implemented",
        ),
        (
            "fn pair<T>(value: T) (T, T) {\n    return value, value\n}\nfn main() i32 {\n    let pair_value = pair(Box(1))\n    return 0\n}\n",
            "Box<T> cannot be nested in a generic return type yet",
        ),
    ],
)
def test_v2_box_rejects_unmanaged_aggregate_positions(source: str, message: str) -> None:
    with pytest.raises(CompileError, match=message):
        Compiler().check_source(source)


def test_v2_dynamic_list_mutation_aliases_nested_values_and_arc(tmp_path: Path) -> None:
    source = tmp_path / "dynamic_list.zy"
    source.write_text(
        """struct Point {
    x: i32
}

fn identity(values: List<i32>) List<i32> {
    return values
}

fn main() i32 {
    let values: List<i32> = []
    LIST_PUSH__(values, 10)
    values.add(20)
    LIST_SET__(values, 1, 22) catch err {
        recover
    }

    let alias = values
    LIST_PUSH__(alias, 30)
    let returned = identity(values)
    if returned.len() != 3 || returned.capacity() < returned.len() || returned.is_empty() {
        return 1
    }

    let removed: i32 = values.remove(0) catch err {
        recover -1
    }
    let popped: i32 = values.pop() catch err {
        recover -1
    }
    let remaining: i32 = values.get(0) catch err {
        recover -1
    }
    if removed != 10 || popped != 30 || remaining != 22 {
        return 2
    }

    let inner: List<i32> = [1]
    let nested: List<List<i32>> = [inner]
    let fetched: List<i32> = nested.get(0) catch err {
        recover []
    }
    LIST_PUSH__(fetched, 2)
    if inner.len() != 2 {
        return 3
    }

    let boxes: List<Box<i32>> = [Box(7)]
    LIST_PUSH__(boxes, Box(8))
    let moved: Box<i32> = boxes.pop() catch err {
        recover Box(-1)
    }
    if moved.value != 8 || boxes.len() != 1 {
        return 4
    }
    boxes.clear()

    let point = Point{x: 1}
    let attributes = point.__attributes__
    LIST_PUSH__(attributes, "extra")
    if attributes.len() != 2 || point.__attributes__.len() != 1 {
        return 5
    }

    let arguments = GET_ARGS__
    LIST_PUSH__(arguments, "local")
    if arguments.len() != 2 {
        return 6
    }

    values.clear()
    if !values.is_empty() {
        return 7
    }
    return 0
}
""",
        encoding="utf-8",
    )

    compiler = Compiler()
    generated = compiler.emit_file(source)
    executable = tmp_path / ("dynamic-list.exe" if sys.platform.startswith("win") else "dynamic-list")
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable), "original"], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "_ensure_mutable" in generated
    assert "_push_copy" in generated
    assert "_storage_drop" in generated
    assert "static inline ZY2_MAYBE_UNUSED" in generated


def test_v2_list_uses_builtin_len_and_checked_index_syntax(tmp_path: Path) -> None:
    source = tmp_path / "list_syntax.zy"
    source.write_text(
        """fn main() i32 {
    let values: List<i32> = [10, 20]
    let nested: List<List<i32>> = [values]
    let first: i32 = values[0] catch err {
        recover -1
    }
    let inner: List<i32> = nested[0] catch err {
        recover []
    }
    LIST_PUSH__(inner, 30)
    let last: i32 = inner[2] catch err {
        recover -1
    }
    if LIST_LEN__(values) != 3 || LIST_LEN__(nested) != 1 || first != 10 || last != 30 {
        return 1
    }
    return 0
}
""",
        encoding="utf-8",
    )

    executable = tmp_path / ("list-syntax.exe" if sys.platform.startswith("win") else "list-syntax")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            'fn main() i32 {\n    let size = LIST_LEN__("hello")\n    return 0\n}\n',
            "LIST_LEN__ expects `List<T>`, got `str`",
        ),
        (
            'fn main() i32 {\n    let value = "hello"[0]\n    return 0\n}\n',
            "indexing with `\\[\\]` requires `List<T>`, got `str`",
        ),
        (
            'fn main() i32 {\n    let values = [1]\n    let value = values["zero"]\n    return 0\n}\n',
            "expected `i32`, got `str`",
        ),
        (
            'fn main() i32 {\n    let values = [1]\n    LIST_PUSH__(values, "two")\n    return 0\n}\n',
            "expected `i32`, got `str`",
        ),
        (
            "fn main() i32 {\n    LIST_PUSH__([1], 2)\n    return 0\n}\n",
            "LIST_PUSH__ requires a local List variable",
        ),
        (
            'fn main() i32 {\n    let values = [1]\n    LIST_SET__(values, "zero", 2)\n    return 0\n}\n',
            "expected `i32`, got `str`",
        ),
    ],
)
def test_v2_list_builtin_syntax_rejects_invalid_operands(source: str, message: str) -> None:
    with pytest.raises(CompileError, match=message):
        Compiler().check_source(source)


def test_v2_list_index_reports_source_location(tmp_path: Path) -> None:
    source = tmp_path / "list_index_error.zy"
    source.write_text(
        """fn main() i32 {
    let values: List<i32> = [10]
    let value: i32 = values[1]
    return value
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("list-index-error.exe" if sys.platform.startswith("win") else "list-index-error")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert "list_index_error.zy:3:28: List index out of range" in result.stderr.replace("\\", "/")


def test_v2_dynamic_list_reports_mutation_errors(tmp_path: Path) -> None:
    source = tmp_path / "list_errors.zy"
    source.write_text(
        """fn main() i32 {
    let values: List<i32> = []
    LIST_SET__(values, 0, 1)
    return 0
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("list-errors.exe" if sys.platform.startswith("win") else "list-errors")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert "list_errors.zy:3:5: List index out of range" in result.stderr.replace("\\", "/")


def test_v2_list_field_waits_for_managed_aggregate_destructors() -> None:
    source = """struct Bad {
    values: List<i32>
}

fn main() i32 {
    return 0
}
"""

    with pytest.raises(CompileError, match="List<T> fields require managed aggregate destructors"):
        Compiler().check_source(source)


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


def test_v2_unhandled_error_is_red_when_color_is_forced(tmp_path: Path) -> None:
    source = tmp_path / "red_stop.zy"
    source.write_text(
        'fn main() i32 throws Error {\n    stop "red stop"\n}\n',
        encoding="utf-8",
    )
    executable = tmp_path / ("red-stop.exe" if sys.platform.startswith("win") else "red-stop")
    Compiler().build_file(source, executable)
    environment = os.environ.copy()
    environment.pop("NO_COLOR", None)
    environment["ZYEN_COLOR"] = "always"
    result = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode != 0
    assert result.stderr.startswith("\x1b[31m")
    assert "red_stop.zy:2:5: red stop" in result.stderr.replace("\\", "/")
    assert result.stderr.endswith("\x1b[0m")


def test_v2_cli_compile_error_is_red_when_color_is_forced(tmp_path: Path) -> None:
    source = tmp_path / "red_compile_error.zy"
    source.write_text("fn main() i32 {\n    return missing\n}\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("NO_COLOR", None)
    environment["ZYEN_COLOR"] = "always"
    result = subprocess.run(
        [sys.executable, "-m", "zyenlang.v2", "check", str(source)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 1
    assert result.stderr.startswith("\x1b[31m")
    assert "unknown name `missing`" in result.stderr
    assert result.stderr.rstrip().endswith("\x1b[0m")


def test_v2_eprint_is_red_when_color_is_forced(tmp_path: Path) -> None:
    source = tmp_path / "red_eprint.zy"
    source.write_text(
        'import <std/io> as io\n\nfn main() i32 {\n    io.eprint("warning")\n    return 0\n}\n',
        encoding="utf-8",
    )
    executable = tmp_path / ("red-eprint.exe" if sys.platform.startswith("win") else "red-eprint")
    Compiler().build_file(source, executable)
    environment = os.environ.copy()
    environment.pop("NO_COLOR", None)
    environment["ZYEN_COLOR"] = "always"
    result = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0
    assert result.stderr == "\x1b[31mwarning\n\x1b[0m"


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


def test_v2_module_qualified_struct_literal_builds_and_runs(tmp_path: Path) -> None:
    model = tmp_path / "model.zy"
    model.write_text(
        "public struct Dict {\n    public size: i32 = 0\n}\n",
        encoding="utf-8",
    )
    main = tmp_path / "main.zy"
    main.write_text(
        'import "model.zy" as model\n\nfn main() i32 {\n'
        "    let value = model.Dict{}\n"
        "    if TYPEOF__(value, model.Dict) {\n"
        "        return value.size\n"
        "    }\n"
        "    return 1\n"
        "}\n",
        encoding="utf-8",
    )

    executable = tmp_path / ("qualified-struct.exe" if sys.platform.startswith("win") else "qualified-struct")
    Compiler().build_file(main, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_private_imported_struct_cannot_be_constructed(tmp_path: Path) -> None:
    model = tmp_path / "model.zy"
    model.write_text("private struct Secret {\n}\n", encoding="utf-8")
    main = tmp_path / "main.zy"
    main.write_text(
        'import "model.zy" as model\n\nfn main() i32 {\n'
        "    let value = model.Secret{}\n"
        "    return 0\n"
        "}\n",
        encoding="utf-8",
    )

    with pytest.raises(CompileError, match="struct `model.Secret` is private"):
        Compiler().check_file(main)


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
import <std/process> as process

fn main() i32 {
    let args = process.args(GET_ARGS__)
    let executable = process.executable(GET_EXE__)
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
    let args: List<str> = GET_ARGS__
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
    wrong_type = "fn main() i32 {\n    let args: i32 = GET_ARGS__\n    return 0\n}\n"
    shadowed = "fn main() i32 {\n    let GET_ARGS__: i32 = 1\n    return 0\n}\n"

    with pytest.raises(CompileError, match="expected `i32`, got `List<str>`"):
        Compiler().check_source(wrong_type)
    with pytest.raises(CompileError, match="reserved process value"):
        Compiler().check_source(shadowed)


@pytest.mark.parametrize("name", ["GET_ARGS__", "GET_EXE__"])
def test_v2_process_special_values_are_restricted_to_main(name: str) -> None:
    return_type = "List<str>" if name == "GET_ARGS__" else "str"
    source = f"fn helper() {return_type} {{\n    return {name}\n}}\n\nfn main() i32 {{\n    return 0\n}}\n"

    with pytest.raises(CompileError, match=r"only available inside `fn main\(\)`"):
        Compiler().check_source(source)


def test_v2_typeof_remains_available_outside_main() -> None:
    source = """fn is_i32(value: i32) bool {
    return TYPEOF__(value, i32)
}

fn main() i32 {
    if is_i32(1) {
        return 0
    }
    return 1
}
"""

    Compiler().check_source(source)


@pytest.mark.parametrize(
    ("source", "old_name", "new_name"),
    [
        ("fn main() i32 {\n    let value = GET_ARGS\n    return 0\n}\n", "GET_ARGS", "GET_ARGS__"),
        ("fn main() i32 {\n    let value = GET_EXE\n    return 0\n}\n", "GET_EXE", "GET_EXE__"),
        ("fn main() i32 {\n    let values = [1]\n    let size = LEN__(values)\n    return 0\n}\n", "LEN__", "LIST_LEN__"),
        ("fn main() i32 {\n    let values = [1]\n    PUSH__(values, 2)\n    return 0\n}\n", "PUSH__", "LIST_PUSH__"),
        ("fn main() i32 {\n    let values = [1]\n    SET__(values, 0, 2)\n    return 0\n}\n", "SET__", "LIST_SET__"),
        ("fn main() i32 {\n    let values = [1]\n    PUSH(values, 2)\n    return 0\n}\n", "PUSH", "LIST_PUSH__"),
        ("fn main() i32 {\n    let values = [1]\n    SET(values, 0, 2)\n    return 0\n}\n", "SET", "LIST_SET__"),
        ("fn main() i32 {\n    let value = typeof 1 i32\n    return 0\n}\n", "typeof", "TYPEOF__"),
    ],
)
def test_v2_old_special_words_report_their_replacements(source: str, old_name: str, new_name: str) -> None:
    with pytest.raises(CompileError) as caught:
        Compiler().check_source(source)

    assert f"`{old_name}` was renamed to `{new_name}`" in str(caught.value)


def test_v2_unknown_uppercase_double_underscore_name_is_reserved() -> None:
    source = "fn main() i32 {\n    let FAKE__ = 1\n    return 0\n}\n"

    with pytest.raises(CompileError, match="reserved for compiler special forms"):
        Compiler().check_source(source)


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
private native fn add(left: i32 = 20, right: i32 = 22) i32 = "native_add"

fn main() i32 {
    return add() - 42
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


def test_v2_native_default_parameter_type_is_checked_when_unused() -> None:
    source = """private native fn invalid(value: i32 = "wrong") i32 = "native_invalid"

fn main() i32 {
    return 0
}
"""

    with pytest.raises(CompileError, match="expected `i32`, got `str`"):
        Compiler().check_source(source)


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


def test_v2_gui_retained_button_layout_and_callback(tmp_path: Path) -> None:
    source = tmp_path / "gui_widgets.zy"
    source.write_text(
        """import <std/gui> as gui
import <std/io> as io

fn clicked() void {
    io.print("clicked")
}

fn main() i32 {
    let layout = gui.column(20, 30, 180, 44, 8)
    let button = layout.button_at(2, "Save", clicked)
    let label = layout.label_at(0, "Settings")
    if button.y != 134 || label.y != 43 || !button.contains(30, 140) || button.contains(300, 140) {
        return 1
    }
    if !button.handle("release\\t30\\t140") {
        return 2
    }
    if button.handle("release\\t300\\t140") {
        return 3
    }
    return 0
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("gui-widgets.exe" if sys.platform.startswith("win") else "gui-widgets")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["clicked"]


def test_v2_fstrings_format_typed_values_and_evaluate_once(tmp_path: Path) -> None:
    source = tmp_path / "fstrings.zy"
    source.write_text(
        """import <std/io> as io

fn measured() i32 {
    io.print("measured once")
    return 42
}

fn echo(value: str) str {
    return value
}

fn main() i32 {
    let maybe: str | null = null
    let value: f64 = (f64)15 / (f64)10
    io.print(f"answer={measured()}, float={value}, ready={true}, optional={maybe}")
    io.print(f"literal braces: {{ok}}, nested={echo(\"yes\")}")
    return 0
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("fstrings.exe" if sys.platform.startswith("win") else "fstrings")
    compiler = Compiler()
    generated = compiler.emit_file(source)
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "measured once",
        "answer=42, float=1.5, ready=true, optional=null",
        "literal braces: {ok}, nested=yes",
    ]
    assert "zy2_format_begin" in generated


def test_v2_fstring_rejects_non_stringifiable_struct() -> None:
    source = """struct Value {
    public number: i32
}

fn main() i32 {
    let value = Value{number: 1}
    let text: str = f"{value}"
    return 0
}
"""
    with pytest.raises(CompileError, match="f-string interpolation does not support `Value`"):
        Compiler().check_source(source)


def test_v2_file_special_value_tracks_each_source_module(tmp_path: Path) -> None:
    module = tmp_path / "location.zy"
    module.write_text(
        """public fn file() str {
    return FILE__
}
""",
        encoding="utf-8",
    )
    source = tmp_path / "main.zy"
    source.write_text(
        """import "location.zy" as location
import <std/io> as io

fn main() i32 {
    io.print(f"{FILE__}|{location.file()}")
    return 0
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("file-value.exe" if sys.platform.startswith("win") else "file-value")
    compiler = Compiler()
    generated = compiler.emit_file(source)
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    expected = f"{source.resolve()}|{module.resolve()}"
    assert result.stdout.strip() == expected
    assert "__zy2_get_file" not in generated


def test_v2_file_special_value_cannot_be_shadowed() -> None:
    source = """fn main() i32 {
    let FILE__: str = "fake"
    return 0
}
"""
    with pytest.raises(CompileError, match="reserved compiler value"):
        Compiler().check_source(source)


def test_v2_named_function_values_struct_callbacks_and_call_chains_run(tmp_path: Path) -> None:
    source = tmp_path / "function_values.zy"
    source.write_text(
        """import <std/io> as io

fn clicked() void {
    io.print("clicked")
}

fn add(a: i32, b: i32) i32 {
    return a + b
}

fn pick() fn(i32, i32) i32 {
    return add
}

struct Button {
    public let x: i32 = 0
    public let y: i32 = 0
    public let when_click_func: fn() void
}

fn (button: Button) click() void {
    button.when_click_func()
}

fn main() i32 {
    let button = Button{x: 10, y: 20, when_click_func: clicked}
    let same_button = Button{x: 10, y: 20, when_click_func: clicked}
    if button != same_button {
        return 3
    }
    button.click()
    let operation: fn(i32, i32) i32 = add
    if operation(20, 22) != 42 {
        return 1
    }
    if pick()(12, 10) != 22 {
        return 2
    }
    return 0
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("function-values.exe" if sys.platform.startswith("win") else "function-values")
    compiler = Compiler()
    generated = compiler.emit_file(source)
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["clicked"]
    assert "typedef void (*zy2_fn_void_to_void)(void);" in generated
    assert "attempted to call an empty function value" in generated


def test_v2_function_values_work_across_imported_modules(tmp_path: Path) -> None:
    library = tmp_path / "callbacks.zy"
    library.write_text(
        """public fn increment(value: i32) i32 {
    return value + 1
}

public fn provide() fn(i32) i32 {
    return increment
}

public fn apply(increment: fn(i32) i32, value: i32) i32 {
    return increment(value)
}

public fn before_local_shadow() i32 {
    let first: i32 = increment(19)
    let increment: fn(i32) i32 = provide()
    return first + increment(20)
}
""",
        encoding="utf-8",
    )
    source = tmp_path / "main.zy"
    source.write_text(
        """import "callbacks.zy" as callbacks

fn main() i32 {
    let direct: fn(i32) i32 = callbacks.increment
    let provided: fn(i32) i32 = callbacks.provide()
    let applied: i32 = callbacks.apply(direct, 20)
    return direct(20) + provided(20) + applied + callbacks.before_local_shadow() - 104
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("module-callback.exe" if sys.platform.startswith("win") else "module-callback")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_v2_function_value_signature_mismatch_is_rejected() -> None:
    source = """fn identity(value: i32) i32 {
    return value
}

fn main() i32 {
    let wrong: fn(i32, i32) i32 = identity
    return 0
}
"""
    with pytest.raises(CompileError, match=r"expected `fn\(i32, i32\) i32`, got `fn\(i32\) i32`"):
        Compiler().check_source(source)


def test_v2_empty_struct_callback_reports_zy_source_location(tmp_path: Path) -> None:
    source = tmp_path / "empty_callback.zy"
    source.write_text(
        """struct Button {
    public when_click_func: fn() void
}

fn main() i32 {
    let button = Button{}
    button.when_click_func()
    return 0
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("empty-callback.exe" if sys.platform.startswith("win") else "empty-callback")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert f"{source}:7:" in result.stderr
    assert "attempted to call an empty function value" in result.stderr


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


def test_v2_compound_assignment_operators_execute(tmp_path: Path) -> None:
    source = tmp_path / "compound_assignment.zy"
    source.write_text(
        """struct Counter {
    value: i32 = 1
}

fn main() i32 {
    let value: i32 = 10
    value += 5
    value -= 3
    value *= 2
    value /= 4
    value %= 4

    let counter = Counter{}
    counter.value += 2
    if value == 2 && counter.value == 3 {
        return 0
    }
    return 1
}
""",
        encoding="utf-8",
    )

    executable = tmp_path / ("compound-assignment.exe" if sys.platform.startswith("win") else "compound-assignment")
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
    if TYPEOF__(value, i32) && !TYPEOF__(value, str) && TYPEOF__("hello", str) && TYPEOF__([1, 2], List<i32>) && TYPEOF__(make_value(), i32) {
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
    source = "fn main() i32 {\n    let value = TYPEOF__(1, MissingType)\n    return 0\n}\n"

    with pytest.raises(CompileError, match="unknown type `MissingType`") as caught:
        Compiler().check_source(source, "typeof_error.zy")

    assert caught.value.span is not None and caught.value.span.line == 2


def test_v2_typeof_old_prefix_form_reports_migration_syntax() -> None:
    source = "fn main() i32 {\n    let value = TYPEOF__ 1 i32\n    return 0\n}\n"

    with pytest.raises(CompileError, match=r"TYPEOF__ now uses `TYPEOF__\(value, Type\)`"):
        Compiler().check_source(source)


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
