from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

import pytest

from zyenlang.v2 import CompileError, Compiler
from zyenlang.v2 import c_module


def run_source(tmp_path: Path, source: str, *, name: str = "program") -> subprocess.CompletedProcess[str]:
    path = tmp_path / f"{name}.zy"
    path.write_text(source, encoding="utf-8")
    executable = tmp_path / (f"{name}.exe" if sys.platform.startswith("win") else name)
    Compiler().build_executable(path, executable)
    return subprocess.run([str(executable)], capture_output=True, text=True, check=False)


def test_c_module_template_load_is_typed_deduplicated_and_native(tmp_path: Path) -> None:
    (tmp_path / "math.h").write_text(
        "#ifndef V3_MATH_H\n#define V3_MATH_H\n#include <stdint.h>\n"
        "typedef struct NativePair { int32_t left; int32_t right; } NativePair;\n"
        "int32_t v3_add(int32_t a, int32_t b);\n"
        "NativePair v3_pair(int32_t left, int32_t right);\n#endif\n",
        encoding="utf-8",
    )
    (tmp_path / "math.c").write_text(
        '#include "math.h"\nint32_t v3_add(int32_t a, int32_t b) { return a + b; }\n'
        "NativePair v3_pair(int32_t left, int32_t right) { return (NativePair){left, right}; }\n",
        encoding="utf-8",
    )
    (tmp_path / "math.zlcm.h").write_text(
        'ZLC_MODULE(math)\nZLC_HEADER("math.h")\nZLC_SOURCE("math.c")\n'
        "ZLC_STRUCT(NativePair, ZLC_FIELD(left, i32), ZLC_FIELD(right, i32))\n"
        "ZLC_FN(add, v3_add, i32, ZLC_PARAM(a, i32), ZLC_PARAM(b, i32))\n"
        "ZLC_FN(pair, v3_pair, NativePair, ZLC_PARAM(left, i32), ZLC_PARAM(right, i32))\n",
        encoding="utf-8",
    )
    result = run_source(
        tmp_path,
        """import std::c_module as c_module

struct MathFacade {
    public source: c_module::Module = c_module::load("math.zlcm.h")
}

fn main() i32 {
    let first: c_module::Module = c_module::load("math.zlcm.h")
    let second: c_module::Module = c_module::load("math.zlcm.h")
    let facade = MathFacade{}
    let pair = first.pair(19, 23)
    if pair.left + pair.right != 42 {
        return 1
    }
    return first.add(20, second.add(10, facade.source.add(5, 7))) - 42
}
""",
        name="c_module_math",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_c_module_template_callback_uses_shared_function_abi(tmp_path: Path) -> None:
    (tmp_path / "callback.c").write_text(
        """#include <stdint.h>
#include "zyenlang_c_abi.h"
typedef int32_t (*callback_i32)(void*, int32_t);
int32_t invoke_callback(ZL_Function callback, int32_t value) {
    if (!zl_fn_matches(callback, "fn(i32) i32")) return -100;
    return ZL_FN_CALL_AS(callback_i32, callback)(callback.env, value);
}
""",
        encoding="utf-8",
    )
    (tmp_path / "callback.zlcm.h").write_text(
        'ZLC_MODULE(callback)\nZLC_SOURCE("callback.c")\n'
        "ZLC_FN(invoke, invoke_callback, i32, ZLC_PARAM(callback, fn(i32)->i32), ZLC_PARAM(value, i32))\n",
        encoding="utf-8",
    )
    result = run_source(
        tmp_path,
        """import std::c_module as cm

fn main() i32 {
    let amount = 2
    let callback: fn(i32) i32 = fn(value: i32) i32 {
        return value + amount
    }
    let bridge: cm::Module = cm::load("callback.zlcm.h")
    return bridge.invoke(callback, 40) - 42
}
""",
        name="c_module_callback",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_c_module_same_declared_module_name_uses_path_identity(tmp_path: Path) -> None:
    for directory, symbol, value in (("first", "first_value", 19), ("second", "second_value", 23)):
        root = tmp_path / directory
        root.mkdir()
        (root / "value.c").write_text(
            f"#include <stdint.h>\nint32_t {symbol}(void) {{ return {value}; }}\n",
            encoding="utf-8",
        )
        (root / "value.zlcm.h").write_text(
            f'ZLC_MODULE(shared_name)\nZLC_SOURCE("value.c")\nZLC_FN(value, {symbol}, i32)\n',
            encoding="utf-8",
        )
    result = run_source(
        tmp_path,
        """import std::c_module as cm

fn main() i32 {
    let first: cm::Module = cm::load("first/value.zlcm.h")
    let second: cm::Module = cm::load("second/value.zlcm.h")
    return first.value() + second.value() - 42
}
""",
        name="c_module_same_name",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_c_module_rejects_duplicate_native_struct_names(tmp_path: Path) -> None:
    for directory, symbol in (("first", "first_point"), ("second", "second_point")):
        root = tmp_path / directory
        root.mkdir()
        (root / "point.zlcm.h").write_text(
            "ZLC_MODULE(points)\n"
            "ZLC_STRUCT(NativePoint, ZLC_FIELD(value, i32))\n"
            f"ZLC_FN(make, {symbol}, NativePoint)\n",
            encoding="utf-8",
        )
    source = tmp_path / "duplicate_struct.zy"
    source.write_text(
        """import std::c_module as cm
fn main() i32 {
    let first: cm::Module = cm::load("first/point.zlcm.h")
    let second: cm::Module = cm::load("second/point.zlcm.h")
    return 0
}
""",
        encoding="utf-8",
    )

    with pytest.raises(CompileError, match="declared by more than one c_module template"):
        Compiler().check_file(source)


def test_c_module_unix_metadata_applies_to_linux_and_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "flags.zlcm.h").write_text(
        "ZLC_MODULE(flags)\n"
        'ZLC_CFLAG_UNIX("-DUNIX_FAMILY=1")\n'
        'ZLC_CFLAG_LINUX("-DLINUX_ONLY=1")\n'
        'ZLC_CFLAG_WINDOWS("-DWINDOWS_ONLY=1")\n'
        "ZLC_FN(value, native_value, i32)\n",
        encoding="utf-8",
    )
    source = tmp_path / "flags.zy"
    source.write_text(
        "import std::c_module as cm\n"
        "fn main() i32 {\n"
        '    let flags: cm::Module = cm::load("flags.zlcm.h")\n'
        "    return 0\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(c_module, "native_platform_name", lambda: "linux")

    program = Compiler().check_file(source)

    assert program.native_cflags == ("-DUNIX_FAMILY=1", "-DLINUX_ONLY=1")


@pytest.mark.parametrize(
    ("template", "message"),
    [
        (
            "ZLC_MODULE(first)\nZLC_MODULE(second)\nZLC_FN(value, native_value, i32)\n",
            "more than one ZLC_MODULE",
        ),
        ("ZLC_MODULE(empty)\n", "at least one ZLC_FN"),
        (
            "ZLC_MODULE(raw)\nZLC_FN(read, native_read, ptr<i32>)\n",
            "raw pointers are not source-level c_module values",
        ),
        (
            "ZLC_MODULE(unknown)\nZLC_EVIL(value)\nZLC_FN(value, native_value, i32)\n",
            "unknown c_module template macro",
        ),
    ],
)
def test_c_module_invalid_template_diagnostics(
    tmp_path: Path, template: str, message: str
) -> None:
    (tmp_path / "invalid.zlcm.h").write_text(template, encoding="utf-8")
    source = tmp_path / "invalid.zy"
    source.write_text(
        "import std::c_module as cm\n"
        "fn main() i32 {\n"
        '    let value: cm::Module = cm::load("invalid.zlcm.h")\n'
        "    return 0\n"
        "}\n",
        encoding="utf-8",
    )

    with pytest.raises(CompileError, match=message):
        Compiler().check_file(source)


def test_c_module_template_metadata_cannot_escape_package(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-native.c"
    outside.write_text("int outside_native(void) { return 0; }\n", encoding="utf-8")
    (tmp_path / "escape.zlcm.h").write_text(
        'ZLC_MODULE(escape)\nZLC_SOURCE("../outside-native.c")\n'
        "ZLC_FN(value, outside_native, i32)\n",
        encoding="utf-8",
    )
    source = tmp_path / "escape.zy"
    source.write_text(
        "import std::c_module as cm\n"
        "fn main() i32 {\n"
        '    let value: cm::Module = cm::load("escape.zlcm.h")\n'
        "    return 0\n"
        "}\n",
        encoding="utf-8",
    )

    with pytest.raises(CompileError, match="path escapes the package root"):
        Compiler().check_file(source)


def test_c_module_missing_template_is_a_compile_error(tmp_path: Path) -> None:
    source = tmp_path / "missing.zy"
    source.write_text(
        "import std::c_module as cm\n"
        "fn main() i32 {\n"
        '    let value: cm::Module = cm::load("missing.zlcm.h")\n'
        "    return 0\n"
        "}\n",
        encoding="utf-8",
    )

    with pytest.raises(CompileError, match="c_module template not found:.*missing.zlcm.h"):
        Compiler().check_file(source)


def test_c_module_types_from_different_templates_do_not_assign_or_leak_hidden_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "first.zlcm.h").write_text(
        "ZLC_MODULE(first)\nZLC_FN(value, first_value, i32)\n",
        encoding="utf-8",
    )
    (tmp_path / "second.zlcm.h").write_text(
        "ZLC_MODULE(second)\nZLC_FN(value, second_value, i32)\n",
        encoding="utf-8",
    )
    source = tmp_path / "mismatch.zy"
    source.write_text(
        "import std::c_module as cm\n"
        "fn main() i32 {\n"
        '    let first: cm::Module = cm::load("first.zlcm.h")\n'
        '    let second: cm::Module = cm::load("second.zlcm.h")\n'
        "    first = second\n"
        "    return 0\n"
        "}\n",
        encoding="utf-8",
    )

    with pytest.raises(CompileError) as failure:
        Compiler().check_file(source)
    message = str(failure.value)
    assert 'c_module::Module("first.zlcm.h")' in message
    assert 'c_module::Module("second.zlcm.h")' in message
    assert "__zlcm_" not in message


def test_removed_c_module_import_reports_the_new_form() -> None:
    with pytest.raises(CompileError, match="import std::c_module as c_module"):
        Compiler().check_source(
            'import c_module.load("math.zlcm.h") as math_source\nfn main() i32 { return 0 }\n'
        )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "fn main() i32 {\n    let bridge: c_module::Module = c_module::load(\"math.zlcm.h\")\n    return 0\n}\n",
            "requires `import std::c_module as c_module`",
        ),
        (
            "import std::c_module as cm\nfn take(value: cm::Module) void {}\nfn main() i32 { return 0 }\n",
            "only valid on a directly initialized local or field",
        ),
        (
            "import std::c_module as cm\nfn main() i32 {\n    let path = \"math.zlcm.h\"\n    let bridge: cm::Module = cm::load(path)\n    return 0\n}\n",
            "requires one string literal path",
        ),
        (
            "import std::c_module as cm\nimport std::c_module as bridge\nfn main() i32 {\n    let value: cm::Module = bridge::load(\"math.zlcm.h\")\n    return 0\n}\n",
            "type alias `cm` and load alias `bridge` must match",
        ),
    ],
)
def test_c_module_misuse_diagnostics(tmp_path: Path, source: str, message: str) -> None:
    path = tmp_path / "main.zy"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(CompileError, match=re.escape(message)):
        Compiler().check_file(path)


def test_generic_class_static_method_mutation_and_arc_run(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """class Cache<T> {
    private value: T

    public init(value: T) {
        this.value = value
    }

    public fn get() T {
        return CLONE__(this.value)
    }

    public mut fn set(value: T) void {
        this.value = value
    }

    public static fn same(value: T) T {
        return value
    }

    deinit {
    }
}

fn main() i32 {
    let first: Cache<i32> = Cache<i32>(10)
    let second = first
    DROP__(first)
    second.set(Cache<i32>::same(42))
    return second.get() - 42
}
""",
        name="generic_class",
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            """class Counter {
    private value: i32 = 0
    public fn bad() void {
        this.value = 1
    }
}
fn main() i32 { return 0 }
""",
            "readonly class method cannot modify `this`",
        ),
        (
            """class Values {
    private items: List<i32> = []
    public fn bad() void {
        LIST_PUSH__(this.items, 1)
    }
}
fn main() i32 { return 0 }
""",
            "readonly class method cannot modify `this`",
        ),
        (
            """class Counter {
    public mut fn set() void {
    }
    public fn bad() void {
        this.set()
    }
}
fn main() i32 { return 0 }
""",
            "readonly class method cannot modify `this`",
        ),
        (
            """class Cache<T> {
    public init(value: T) {
    }
}
fn main() i32 {
    let value = Cache(1)
    return 0
}
""",
            "requires explicit type arguments",
        ),
    ],
)
def test_class_mutability_and_generic_rules(source: str, message: str) -> None:
    with pytest.raises(CompileError, match=message):
        Compiler().check_source(source)


def test_references_clone_and_mutate_without_ownership(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """fn main() i32 {
    let source: i32 = 10
    let read: &i32 = &source
    let copied: i32 = CLONE_REF__(read)
    let value: i32 = 10
    let write: &mut i32 = &mut value
    REF_SET__(write, 20)
    return value - copied - 10
}
""",
        name="references",
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            """fn main() i32 {
    let value = 1
    let read: &i32 = &value
    let write: &mut i32 = &mut value
    return CLONE_REF__(read)
}
""",
            "cannot mutably borrow `value` while another reference exists",
        ),
        (
            """fn main() i32 {
    let value = 1
    let write: &mut i32 = &mut value
    let read: &i32 = &value
    REF_SET__(write, 2)
    return 0
}
""",
            "cannot borrow `value` while a mutable reference exists",
        ),
        (
            """fn main() i32 {
    let value = Box(1)
    let read: &Box<i32> = &value
    DROP__(value)
    let copy = CLONE_REF__(read)
    return copy.value
}
""",
            "cannot drop `value` while it is borrowed",
        ),
        (
            """fn main() i32 {
    let value = 1
    let read: &i32 = &value
    REF_SET__(read, 2)
    return 0
}
""",
            "REF_SET__ requires `&mut T`",
        ),
        (
            """fn bad(value: i32) &i32 {
    return &value
}
fn main() i32 { return 0 }
""",
            "references cannot be returned",
        ),
        (
            """struct Bad {
    value: &i32
}
fn main() i32 { return 0 }
""",
            "references cannot be stored",
        ),
        (
            """fn main() i32 {
    let value = 1
    let read: &i32 = &value
    let nested: &&i32 = &read
    return 0
}
""",
            "references cannot point to references",
        ),
        (
            """fn main() i32 {
    let value = 1
    let read: &i32 = &value
    let callback = fn() i32 {
        return CLONE_REF__(read)
    }
    return callback()
}
""",
            "references cannot be captured",
        ),
    ],
)
def test_reference_safety_diagnostics(source: str, message: str) -> None:
    with pytest.raises(CompileError, match=message):
        Compiler().check_source(source)


def test_class_references_dispatch_readonly_and_mutating_methods(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """class Cache<T> {
    private value: T
    public init(value: T) { this.value = value }
    public fn get() T { return CLONE__(this.value) }
    public mut fn set(value: T) void { this.value = value }
}

fn inspect(cache: &Cache<i32>) i32 { return cache.get() }
fn update(cache: &mut Cache<i32>) void { cache.set(42) }

fn main() i32 {
    let cache = Cache<i32>(10)
    if inspect(&cache) != 10 { return 1 }
    update(&mut cache)
    return cache.get() - 42
}
""",
        name="class_references",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_list_references_are_zero_copy_readonly_and_explicitly_mutating(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """fn count(values: &List<i32>) usize { return LIST_LEN__(values) }
fn append(values: &mut List<i32>) void {
    values.push(42)
    LIST_PUSH__(values, 43)
}

fn main() i32 {
    let values: List<i32> = [1]
    let snapshot = values
    if count(&values) != 1 { return 1 }
    append(&mut values)
    (&mut values).push(44)
    LIST_PUSH__(&mut values, 45)
    if values.len() != 5 { return 2 }
    if snapshot.len() != 1 { return 3 }
    return 0
}
""",
        name="list_references",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_list_element_references_write_and_pin_cow_storage(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """class Bag {
    public items: List<str> = []
    public mut fn add(value: str) void { this.items.push(value) }
}

fn main() i32 {
    let numbers: List<i32> = [10]
    let write: &mut i32 = &mut numbers[0]
    REF_SET__(write, 20)
    if numbers[0] != 20 { return 1 }

    let bag = Bag()
    bag.add("old")
    let alias = bag
    let read: &str = &bag.items[0]
    alias.add("new")
    let old = CLONE_REF__(read)
    if old != "old" { return 2 }
    if bag.items[0] != "old" { return 3 }
    if alias.items[0] != "old" { return 4 }
    return 0
}
""",
        name="list_element_references",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_reference_loan_ends_after_last_statement_use(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """fn main() i32 {
    let value = 10
    let read: &i32 = &value
    let copied = CLONE_REF__(read)
    let write: &mut i32 = &mut value
    REF_SET__(write, 32)
    return value + copied - 42
}
""",
        name="reference_last_use",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_field_borrows_track_disjoint_places(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """struct Pair {
    public left: i32
    public right: i32
}
class Cell {
    public value: i32 = 10
}
fn main() i32 {
    let pair = Pair{left: 10, right: 0}
    let left: &i32 = &pair.left
    pair.right = 32
    let cell = Cell()
    let value: &mut i32 = &mut cell.value
    REF_SET__(value, 42)
    return CLONE_REF__(left) + pair.right + cell.value - 84
}
""",
        name="field_borrows",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_readonly_class_reference_rejects_mutating_method() -> None:
    source = """class Counter {
    public mut fn increment() void {}
}
fn bad(counter: &Counter) void { counter.increment() }
fn main() i32 { return 0 }
"""
    with pytest.raises(CompileError, match="requires `&mut Counter`"):
        Compiler().check_source(source)


def test_mutable_list_element_reference_rejects_class_alias_path() -> None:
    source = """class Values {
    public items: List<i32> = [1]
}
fn main() i32 {
    let values = Values()
    let item: &mut i32 = &mut values.items[0]
    return 0
}
"""
    with pytest.raises(CompileError, match="cannot cross a class or reference alias"):
        Compiler().check_source(source)

    nested_source = """class Values {
    public items: List<i32> = [1]
}
struct Holder {
    public values: Values
}
fn main() i32 {
    let holder = Holder{values: Values()}
    let item: &mut i32 = &mut holder.values.items[0]
    return 0
}
"""
    with pytest.raises(CompileError, match="cannot cross a class or reference alias"):
        Compiler().check_source(nested_source)


def test_readonly_list_element_reference_works_through_list_parameter(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """fn first(values: &List<i32>) i32 {
    let item: &i32 = &values[0]
    return CLONE_REF__(item)
}
fn main() i32 {
    let values: List<i32> = [42]
    return first(&values) - 42
}
""",
        name="readonly_list_element_parameter",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_mutable_list_element_reference_works_for_value_struct_field(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """struct Values {
    items: List<i32>
}
fn main() i32 {
    let values = Values{items: [10]}
    let item: &mut i32 = &mut values.items[0]
    REF_SET__(item, 42)
    return values.items[0] - 42
}
""",
        name="mutable_struct_list_element",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_mutable_class_reference_can_replace_handle(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """class Cache {
    public value: i32
    public init(value: i32) { this.value = value }
    public fn get() i32 { return this.value }
}
fn replace(cache: &mut Cache) void { REF_SET__(cache, Cache(42)) }
fn main() i32 {
    let cache = Cache(10)
    replace(&mut cache)
    return cache.get() - 42
}
""",
        name="replace_class_handle",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_list_element_borrow_can_recover_from_bounds_error(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """fn main() i32 {
    let values: List<i32> = [42]
    (&values[9]) catch err {
        if err.message != "List index out of range" { return 1 }
        recover
    }
    return 0
}
""",
        name="list_element_borrow_bounds",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_mutable_reference_copy_is_rejected() -> None:
    source = """fn main() i32 {
    let value = 1
    let first: &mut i32 = &mut value
    let second: &mut i32 = first
    return 0
}
"""
    with pytest.raises(CompileError, match="mutable references cannot be copied"):
        Compiler().check_source(source)


def test_mutable_reference_can_be_temporarily_viewed_as_readonly(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """fn read(value: &i32) i32 { return CLONE_REF__(value) }
fn main() i32 {
    let value = 10
    let write: &mut i32 = &mut value
    if read(write) != 10 { return 1 }
    REF_SET__(write, 42)
    return value - 42
}
""",
        name="mutable_to_readonly_reference",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_nested_class_field_borrow_requires_local_handle() -> None:
    source = """class Child { public value: i32 = 1 }
class Parent { public child: Child }
fn main() i32 {
    let parent = Parent()
    let value: &i32 = &parent.child.value
    return 0
}
"""
    with pytest.raises(CompileError, match="nested class handle"):
        Compiler().check_source(source)


def test_closure_factories_and_chained_function_calls_run(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """fn make_factory(base: i32) fn(i32) fn(i32) i32 {
    return fn(delta: i32) fn(i32) i32 {
        return fn(value: i32) i32 {
            return base + delta + value
        }
    }
}

fn main() i32 {
    let callback = make_factory(10)(12)
    let alias = callback
    DROP__(callback)
    if alias(20) != 42 {
        return 1
    }
    if make_factory(1)(2)(3) != 6 {
        return 2
    }
    return 0
}
""",
        name="closures",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_defer_is_lifo_and_runs_on_all_control_flow_exits(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """import std::io as io

class Token {
    private name: str
    public init(name: str) {
        this.name = name
    }
    deinit {
        io::print(this.name)
    }
}

fn mark(text: str) void {
    io::print(text)
}

fn choose() fn(str) void {
    io::print("choose")
    return mark
}

fn early() i32 {
    let value = Token("drop-return")
    defer mark("defer-1")
    defer choose()("defer-2")
    return 7
}

fn fail() i32 throws Error {
    let value = Token("drop-stop")
    defer mark("defer-stop")
    stop "expected"
}

fn main() i32 {
    io::print((str)early())
    let recovered = fail() catch err {
        let value = Token("drop-catch")
        defer mark("defer-catch")
        io::print(err.message)
        recover 9
    }
    io::print((str)recovered)
    let index = 0
    while index < 3 {
        let value = Token("drop-loop")
        defer mark("defer-loop")
        index = index + 1
        if index < 2 {
            continue
        }
        break
    }
    return 0
}
""",
        name="defer_cleanup",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "choose",
        "defer-2",
        "defer-1",
        "drop-return",
        "7",
        "defer-stop",
        "drop-stop",
        "expected",
        "defer-catch",
        "drop-catch",
        "9",
        "defer-loop",
        "drop-loop",
        "defer-loop",
        "drop-loop",
    ]


def test_unicode_scalar_string_operations_and_errors_run(tmp_path: Path) -> None:
    result = run_source(
        tmp_path,
        """fn main() i32 {
    let text = "A你🙂"
    if STR_LEN__(text) != 3 || STR_BYTE_LEN__(text) != 8 {
        return 1
    }
    let middle = STR_GET__(text, 1) catch err {
        recover ""
    }
    let tail = STR_SLICE__(text, 1, 3) catch err {
        recover ""
    }
    let missing = STR_GET__(text, 8) catch err {
        recover err.message
    }
    if middle != "你" || tail != "你🙂" || missing != "str scalar index out of range" {
        return 2
    }
    return 0
}
""",
        name="unicode_string",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_old_namespace_and_struct_method_syntax_have_migration_errors() -> None:
    with pytest.raises(CompileError, match="imports use"):
        Compiler().check_source('import <std/io> as io\nfn main() i32 { return 0 }\n')

    with pytest.raises(CompileError, match="receiver functions were removed"):
        Compiler().check_source(
            "struct Value { x: i32 }\nfn (value: Value) get() i32 { return value.x }\nfn main() i32 { return 0 }\n"
        )


def test_module_dot_call_reports_double_colon_migration(tmp_path: Path) -> None:
    path = tmp_path / "main.zy"
    path.write_text(
        "import std::io as io\nfn main() i32 {\n    io.print(\"bad\")\n    return 0\n}\n",
        encoding="utf-8",
    )
    with pytest.raises(CompileError, match=r"write `io::print"):
        Compiler().check_file(path)


def test_native_callback_abi_retains_closure_replaces_and_clears(tmp_path: Path) -> None:
    native = tmp_path / "callback_native.c"
    native.write_text(
        """#include <stdint.h>
#include "zyenlang_c_abi.h"

typedef int32_t (*callback_i32)(void*, int32_t);
static ZL_Function stored;

int32_t callback_store(ZL_Function value) {
    if (!zl_fn_matches(value, "fn(i32) i32")) return -1;
    zl_fn_assign(&stored, value);
    return 0;
}

int32_t callback_invoke(int32_t value) {
    if (zl_fn_is_none(stored)) return -1000;
    return ZL_FN_CALL_AS(callback_i32, stored)(stored.env, value);
}

ZL_Function callback_load(void) {
    return zl_fn_retain(stored);
}

void callback_clear(void) {
    zl_fn_clear(&stored);
}

int32_t callback_is_empty(void) {
    return zl_fn_is_none(stored) ? 1 : 0;
}
""",
        encoding="utf-8",
    )
    source = tmp_path / "callback.zy"
    source.write_text(
        """native source "callback_native.c"

private native fn store(callback: fn(i32) i32) i32 = "callback_store"
private native fn invoke(value: i32) i32 = "callback_invoke"
private native fn load() fn(i32) i32 = "callback_load"
private native fn clear() void = "callback_clear"
private native fn is_empty() i32 = "callback_is_empty"

fn increment(value: i32) i32 {
    return value + 1
}

fn main() i32 {
    let amount = 1
    let closure: fn(i32) i32 = fn(value: i32) i32 {
        return value + amount
    }
    if store(closure) != 0 {
        return 1
    }
    DROP__(closure)
    if invoke(41) != 42 {
        return 2
    }
    let loaded = load()
    if loaded(20) != 21 {
        return 3
    }
    DROP__(loaded)
    if store(increment) != 0 || invoke(9) != 10 {
        return 4
    }
    clear()
    if is_empty() != 1 {
        return 5
    }
    return 0
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("callback.exe" if sys.platform.startswith("win") else "callback")
    Compiler().build_executable(source, executable)

    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
