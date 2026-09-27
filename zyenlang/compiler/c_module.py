from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path

from . import ast
from .diagnostics import CompileError, SourceSpan


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PATH_MACROS = {
    "HEADER": "headers",
    "SOURCE": "sources",
    "INCLUDE_DIR": "include_dirs",
    "LIB_DIR": "lib_dirs",
}
_TEXT_MACROS = {
    "LIB": "libraries",
    "CFLAG": "cflags",
    "LDFLAG": "ldflags",
}
MAX_TEMPLATE_BYTES = 1024 * 1024
MAX_TEMPLATE_MACROS = 1024
MAX_TEMPLATE_STRUCTS = 256
MAX_TEMPLATE_FUNCTIONS = 512
MAX_TEMPLATE_MEMBERS = 256


@dataclass(frozen=True)
class TemplateField:
    name: str
    type_node: ast.TypeNode


@dataclass(frozen=True)
class TemplateStruct:
    name: str
    fields: tuple[TemplateField, ...]


@dataclass(frozen=True)
class TemplateHandle:
    name: str
    drop_symbol: str | None


@dataclass(frozen=True)
class TemplateEnumCase:
    name: str
    value: int


@dataclass(frozen=True)
class TemplateEnum:
    name: str
    underlying_type: ast.TypeNode
    cases: tuple[TemplateEnumCase, ...]
    flags: bool = False


@dataclass(frozen=True)
class TemplateConstant:
    name: str
    type_node: ast.TypeNode
    value: int | float | bool | str


@dataclass(frozen=True)
class TemplateTypePolicy:
    ownership: str | None = None
    parent: str | None = None
    direction: str = "in"


@dataclass(frozen=True)
class TemplateFunction:
    name: str
    symbol: str
    params: tuple[tuple[str, ast.TypeNode, TemplateTypePolicy], ...]
    return_type: ast.TypeNode
    return_policy: TemplateTypePolicy = TemplateTypePolicy()
    failure: tuple[str, int | None, str | None] | None = None


@dataclass(frozen=True)
class NativeTemplate:
    path: Path
    module_name: str
    hidden_name: str
    abi_version: int
    handles: tuple[TemplateHandle, ...]
    enums: tuple[TemplateEnum, ...]
    constants: tuple[TemplateConstant, ...]
    structs: tuple[TemplateStruct, ...]
    functions: tuple[TemplateFunction, ...]
    headers: tuple[Path, ...]
    sources: tuple[Path, ...]
    include_dirs: tuple[Path, ...]
    lib_dirs: tuple[Path, ...]
    libraries: tuple[str, ...]
    cflags: tuple[str, ...]
    ldflags: tuple[str, ...]


def native_platform_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unix"


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _split_args(body: str) -> list[str]:
    values: list[str] = []
    start = 0
    depth = 0
    generic_depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(body):
        if quoted:
            if char == '"' and not escaped:
                quoted = False
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            continue
        if char == '"':
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "<":
            generic_depth += 1
        elif char == ">" and not (index > 0 and body[index - 1] == "-"):
            generic_depth -= 1
        elif char == "," and depth == 0 and generic_depth == 0:
            values.append(body[start:index].strip())
            start = index + 1
    tail = body[start:].strip()
    if tail:
        values.append(tail)
    return values


def _macro_calls(text: str) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    cursor = 0
    while match := re.search(r"\b(ZLC_[A-Z_]+)\s*\(", text[cursor:]):
        name = match.group(1)
        opening = cursor + match.end() - 1
        index = opening + 1
        depth = 1
        quoted = False
        escaped = False
        while index < len(text):
            char = text[index]
            if quoted:
                if char == '"' and not escaped:
                    quoted = False
                escaped = char == "\\" and not escaped
                if char != "\\":
                    escaped = False
            elif char == '"':
                quoted = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    calls.append((name, text[opening + 1:index].strip()))
                    index += 1
                    break
            index += 1
        else:
            raise ValueError(f"unterminated template macro `{name}`")
        cursor = index
    return calls


def _value(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        value = json.loads(raw)
        if not isinstance(value, str):
            raise ValueError("template string value must decode to text")
        return value
    return raw


def _require_ident(value: str, purpose: str) -> str:
    if not _IDENT.fullmatch(value):
        raise ValueError(f"invalid {purpose} `{value}`")
    return value


def _find_function_close(value: str) -> int:
    depth = 0
    for index, char in enumerate(value[2:], start=2):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _outer_generic(value: str) -> tuple[str, list[str]] | None:
    opening = value.find("<")
    if opening <= 0 or not value.endswith(">"):
        return None
    depth = 0
    for index, char in enumerate(value[opening:], start=opening):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
            if depth == 0 and index != len(value) - 1:
                return None
    if depth != 0:
        return None
    return value[:opening], _split_args(value[opening + 1:-1])


def parse_template_type(
    raw: str,
    span: SourceSpan,
    struct_names: set[str],
    *,
    allow_void: bool,
    handle_names: set[str] | None = None,
    enum_names: set[str] | None = None,
) -> ast.TypeNode:
    value = "".join(raw.split())
    handle_names = handle_names or set()
    enum_names = enum_names or set()
    aliases = {
        "int": "i32",
        "float": "f64",
        "ZL_String": "str",
    }
    value = aliases.get(value, value)
    scalar = {
        "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "usize",
        "f32", "f64", "bool", "str", "void",
    }
    if value in scalar:
        if value == "void" and not allow_void:
            raise ValueError("void is not valid in this template position")
        return ast.NamedTypeNode(span, value)
    if value.startswith("fn("):
        close = _find_function_close(value)
        if close < 0:
            raise ValueError(f"invalid c_module function type `{raw}`")
        params_body = value[3:close]
        tail = value[close + 1:]
        if tail.startswith("->"):
            tail = tail[2:]
        if not tail:
            raise ValueError(f"function type `{raw}` is missing a return type")
        params = tuple(
            parse_template_type(
                item,
                span,
                struct_names,
                allow_void=False,
                handle_names=handle_names,
                enum_names=enum_names,
            )
            for item in (_split_args(params_body) if params_body else [])
        )
        return ast.FunctionTypeNode(
            span,
            params,
            parse_template_type(
                tail,
                span,
                struct_names,
                allow_void=True,
                handle_names=handle_names,
                enum_names=enum_names,
            ),
        )
    generic = _outer_generic(value)
    if generic and generic[0] in {"Slice", "MutSlice"}:
        if len(generic[1]) != 1:
            raise ValueError(f"{generic[0]} requires exactly one element type")
        element = parse_template_type(
            generic[1][0],
            span,
            struct_names,
            allow_void=False,
            handle_names=handle_names,
            enum_names=enum_names,
        )
        return ast.NamedTypeNode(
            span,
            "__zl_mut_slice" if generic[0] == "MutSlice" else "__zl_slice",
            (element,),
        )
    if value in struct_names | handle_names | enum_names:
        return ast.NamedTypeNode(span, value)
    if value in {"ZL_List", "ZL_list", "List"} or value.startswith(("ZL_List<", "ZL_list<", "List<")):
        raise ValueError(
            "List is not a stable c_module ABI type in v0.3; expose scalar/str/struct values or an opaque native handle"
        )
    if value.startswith(("ZL_ptr", "ptr<", "Raw<")):
        raise ValueError(
            "raw pointers are not source-level c_module values in v0.3; wrap the pointer in a native handle API"
        )
    supported = (
        "fixed-width numbers, bool, str, void, fn(...)T, Slice<T>, MutSlice<T>, "
        "and declared handle/enum/struct names"
    )
    raise ValueError(f"unsupported c_module type `{raw}`; expected {supported}")


def parse_template_ffi_type(
    raw: str,
    span: SourceSpan,
    struct_names: set[str],
    handle_names: set[str],
    enum_names: set[str],
    *,
    allow_void: bool,
) -> tuple[ast.TypeNode, TemplateTypePolicy]:
    value = "".join(raw.split())
    optional = False
    ownership: str | None = None
    parent: str | None = None
    direction = "in"
    while generic := _outer_generic(value):
        wrapper, args = generic
        if wrapper == "optional":
            if len(args) != 1 or optional:
                raise ValueError("optional<T> requires exactly one non-optional type")
            optional = True
            value = args[0]
            continue
        if wrapper in {"owned", "borrowed", "borrowed_static", "consumed"}:
            if ownership is not None:
                raise ValueError("native ownership wrappers cannot be nested")
            expected = 2 if wrapper == "borrowed" and len(args) == 2 else 1
            if len(args) != expected:
                raise ValueError(f"{wrapper}<...> has invalid arguments")
            ownership = wrapper
            value = args[0]
            if len(args) == 2:
                parent = _require_ident(args[1], "borrow parent parameter")
            continue
        if wrapper in {"out", "inout"}:
            if len(args) != 1 or direction != "in":
                raise ValueError(f"{wrapper}<T> requires exactly one value type")
            direction = wrapper
            value = args[0]
            continue
        break
    node = parse_template_type(
        value,
        span,
        struct_names,
        allow_void=allow_void,
        handle_names=handle_names,
        enum_names=enum_names,
    )
    if ownership is not None:
        if not isinstance(node, ast.NamedTypeNode) or node.name not in handle_names:
            raise ValueError(f"{ownership}<T> requires a declared ZLC_HANDLE type")
    is_handle = isinstance(node, ast.NamedTypeNode) and node.name in handle_names
    if is_handle and ownership is None:
        raise ValueError(f"native handle `{node.name}` requires an explicit ownership wrapper")
    if optional and not is_handle:
        raise ValueError("optional<T> represents C NULL and therefore requires a declared ZLC_HANDLE type")
    if direction != "in" and (
        isinstance(node, ast.NamedTypeNode) and node.name in {"__zl_slice", "__zl_mut_slice"}
    ):
        raise ValueError("out/inout Slice values are not supported; use MutSlice<T>")
    if optional:
        node = ast.OptionalTypeNode(span, node)
    return node, TemplateTypePolicy(ownership, parent, direction)


def _literal(raw: str) -> int | float | bool | str:
    value = raw.strip()
    if value.startswith('"') and value.endswith('"'):
        return _value(value)
    if value in {"true", "false"}:
        return value == "true"
    try:
        return int(value, 0)
    except ValueError:
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"expected a literal value, got `{raw}`") from exc


def _validate_constant_literal(name: str, type_node: ast.TypeNode, value: int | float | bool | str, enum_names: set[str]) -> None:
    if not isinstance(type_node, ast.NamedTypeNode):
        raise ValueError(f"constant `{name}` requires a scalar or enum type")
    type_name = type_node.name
    if type_name in enum_names:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"constant `{name}` enum value must be an integer")
        return
    integer_ranges = {
        "i8": (-(2**7), 2**7 - 1), "u8": (0, 2**8 - 1),
        "i16": (-(2**15), 2**15 - 1), "u16": (0, 2**16 - 1),
        "i32": (-(2**31), 2**31 - 1), "u32": (0, 2**32 - 1),
        "i64": (-(2**63), 2**63 - 1), "u64": (0, 2**64 - 1),
        "usize": (0, 2**64 - 1),
    }
    if type_name in integer_ranges:
        if not isinstance(value, int) or isinstance(value, bool) or not integer_ranges[type_name][0] <= value <= integer_ranges[type_name][1]:
            raise ValueError(f"constant `{name}` does not fit `{type_name}`")
    elif type_name in {"f32", "f64"}:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"constant `{name}` requires a numeric literal")
    elif type_name == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"constant `{name}` requires true or false")
    elif type_name == "str":
        if not isinstance(value, str):
            raise ValueError(f"constant `{name}` requires a string literal")
    else:
        raise ValueError(f"constant `{name}` type `{type_name}` is not a stable literal ABI type")


def _nested_macro(raw: str, name: str, expected: int) -> list[str]:
    match = re.fullmatch(rf"{name}\s*\((.*)\)", raw.strip(), flags=re.S)
    if not match:
        raise ValueError(f"expected {name}(...), got `{raw}`")
    args = _split_args(match.group(1))
    if len(args) != expected:
        raise ValueError(f"{name} expects {expected} arguments")
    return args


def _dedupe(values):
    result = []
    seen = set()
    for value in values:
        key = os.path.normcase(str(value)) if isinstance(value, Path) else (
            value.casefold() if sys.platform.startswith("win") else value
        )
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _safe_flag(value: str, kind: str) -> str:
    parts = shlex.split(value, posix=not sys.platform.startswith("win"))
    if len(parts) != 1 or any(char in value for char in "\r\n\0"):
        raise ValueError(f"{kind} must contain one compiler argument per macro")
    blocked = {"-o", "-c", "-E", "-S", "/Fo", "/Fe"}
    if value in blocked or value.startswith(("-o", "@", "/Fo", "/Fe")):
        raise ValueError(f"unsafe {kind} `{value}`")
    return value


def load_template(path: Path, span: SourceSpan, package_root: Path) -> NativeTemplate:
    path = path.resolve()
    if path.suffixes[-2:] != [".zlcm", ".h"]:
        raise CompileError("c_module.load expects a `.zlcm.h` template", span)
    if path != package_root and package_root not in path.parents:
        raise CompileError("c_module template path escapes its package root", span)
    if not path.is_file():
        raise CompileError(f"c_module template not found: {path}", span)
    try:
        if path.stat().st_size > MAX_TEMPLATE_BYTES:
            raise ValueError(f"template exceeds the {MAX_TEMPLATE_BYTES}-byte safety limit")
        calls = _macro_calls(_strip_comments(path.read_text(encoding="utf-8")))
        if len(calls) > MAX_TEMPLATE_MACROS:
            raise ValueError(f"template exceeds the {MAX_TEMPLATE_MACROS}-macro safety limit")
        module_name = ""
        abi_version = 2
        raw_handles: list[tuple[str, str | None]] = []
        raw_enums: list[tuple[str, str, list[list[str]], bool]] = []
        raw_constants: list[list[str]] = []
        raw_structs: list[tuple[str, list[list[str]]]] = []
        raw_functions: list[list[str]] = []
        metadata: dict[str, list[str]] = {
            "headers": [], "sources": [], "include_dirs": [], "lib_dirs": [],
            "libraries": [], "cflags": [], "ldflags": [],
        }
        platform = native_platform_name().upper()
        for macro, body in calls:
            args = _split_args(body)
            if macro == "ZLC_ABI":
                if len(args) != 1:
                    raise ValueError("ZLC_ABI expects 1 argument")
                if _literal(args[0]) != 3:
                    raise ValueError("only c_module ABI 3 can be requested explicitly")
                abi_version = 3
                continue
            if macro == "ZLC_MODULE":
                if len(args) != 1:
                    raise ValueError("ZLC_MODULE expects 1 argument")
                if module_name:
                    raise ValueError("template contains more than one ZLC_MODULE")
                module_name = _require_ident(_value(args[0]), "module name")
                continue
            if macro == "ZLC_HANDLE":
                if len(args) not in {1, 2}:
                    raise ValueError("ZLC_HANDLE expects a name and optional ZLC_DROP(symbol)")
                name = _require_ident(_value(args[0]), "handle name")
                if not name[0].isupper():
                    raise ValueError(f"handle name `{name}` must start with an uppercase letter")
                drop_symbol = None
                if len(args) == 2:
                    drop_args = _nested_macro(args[1], "ZLC_DROP", 1)
                    drop_symbol = _require_ident(_value(drop_args[0]), "handle drop symbol")
                raw_handles.append((name, drop_symbol))
                continue
            if macro in {"ZLC_ENUM", "ZLC_FLAGS"}:
                if len(args) < 2:
                    raise ValueError(f"{macro} expects a name, underlying type, and optional ZLC_CASE values")
                name = _require_ident(_value(args[0]), "enum name")
                if not name[0].isupper():
                    raise ValueError(f"enum name `{name}` must start with an uppercase letter")
                raw_enums.append((
                    name,
                    _value(args[1]),
                    [_nested_macro(item, "ZLC_CASE", 2) for item in args[2:]],
                    macro == "ZLC_FLAGS",
                ))
                continue
            if macro == "ZLC_CONST":
                if len(args) != 3:
                    raise ValueError("ZLC_CONST expects name, type, and literal value")
                raw_constants.append(args)
                continue
            platform_match = re.fullmatch(
                r"ZLC_(HEADER|SOURCE|INCLUDE_DIR|LIB_DIR|LIB|CFLAG|LDFLAG)_(WINDOWS|LINUX|MACOS|UNIX)",
                macro,
            )
            if platform_match:
                if len(args) != 1:
                    raise ValueError(f"{macro} expects 1 argument")
                selected_platform = platform_match.group(2)
                if selected_platform == platform or (
                    selected_platform == "UNIX" and platform in {"LINUX", "MACOS", "UNIX"}
                ):
                    key = (_PATH_MACROS | _TEXT_MACROS)[platform_match.group(1)]
                    metadata[key].append(_value(args[0]))
                continue
            simple = re.fullmatch(r"ZLC_(HEADER|SOURCE|INCLUDE_DIR|LIB_DIR|LIB|CFLAG|LDFLAG)", macro)
            if simple:
                if len(args) != 1:
                    raise ValueError(f"{macro} expects 1 argument")
                key = (_PATH_MACROS | _TEXT_MACROS)[simple.group(1)]
                metadata[key].append(_value(args[0]))
                continue
            if macro == "ZLC_STRUCT":
                if not args:
                    raise ValueError("ZLC_STRUCT expects a name")
                if len(raw_structs) >= MAX_TEMPLATE_STRUCTS:
                    raise ValueError(f"template exceeds the {MAX_TEMPLATE_STRUCTS}-struct safety limit")
                if len(args) - 1 > MAX_TEMPLATE_MEMBERS:
                    raise ValueError(f"ZLC_STRUCT exceeds the {MAX_TEMPLATE_MEMBERS}-field safety limit")
                raw_structs.append((_require_ident(_value(args[0]), "struct name"), [
                    _nested_macro(item, "ZLC_FIELD", 2) for item in args[1:]
                ]))
                continue
            if macro == "ZLC_FN":
                if len(args) < 3:
                    raise ValueError("ZLC_FN expects name, C symbol, return type, and optional parameters")
                if len(raw_functions) >= MAX_TEMPLATE_FUNCTIONS:
                    raise ValueError(f"template exceeds the {MAX_TEMPLATE_FUNCTIONS}-function safety limit")
                if len(args) - 3 > MAX_TEMPLATE_MEMBERS:
                    raise ValueError(f"ZLC_FN exceeds the {MAX_TEMPLATE_MEMBERS}-parameter safety limit")
                raw_functions.append(args)
                continue
            if macro in {"ZLC_FIELD", "ZLC_PARAM", "ZLC_CASE", "ZLC_DROP", "ZLC_FAIL"}:
                continue
            raise ValueError(f"unknown c_module template macro `{macro}`")
        if not module_name:
            raise ValueError("template is missing ZLC_MODULE")
        if not any((raw_functions, raw_structs, raw_handles, raw_enums, raw_constants)):
            raise ValueError("template must declare at least one ZLC_FN or ABI v3 type/constant")

        if abi_version < 3 and any((raw_handles, raw_enums, raw_constants)):
            raise ValueError("ZLC_HANDLE/ZLC_ENUM/ZLC_FLAGS/ZLC_CONST require ZLC_ABI(3)")

        struct_names = {name for name, _ in raw_structs}
        if len(struct_names) != len(raw_structs):
            raise ValueError("duplicate ZLC_STRUCT name")
        handle_names = {name for name, _ in raw_handles}
        if len(handle_names) != len(raw_handles):
            raise ValueError("duplicate ZLC_HANDLE name")
        enum_names = {name for name, _, _, _ in raw_enums}
        if len(enum_names) != len(raw_enums):
            raise ValueError("duplicate ZLC_ENUM/ZLC_FLAGS name")
        type_names = struct_names | handle_names | enum_names
        if len(type_names) != len(struct_names) + len(handle_names) + len(enum_names):
            raise ValueError("native type names must be unique within a template")
        handles = [TemplateHandle(name, drop) for name, drop in raw_handles]
        enums: list[TemplateEnum] = []
        for name, raw_underlying, raw_cases, flags in raw_enums:
            underlying = parse_template_type(raw_underlying, span, set(), allow_void=False)
            if not isinstance(underlying, ast.NamedTypeNode) or underlying.name not in {
                "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "usize"
            }:
                raise ValueError(f"enum `{name}` requires an integer underlying type")
            cases: list[TemplateEnumCase] = []
            seen_cases: set[str] = set()
            for raw_case_name, raw_case_value in raw_cases:
                case_name = _require_ident(_value(raw_case_name), "enum case name")
                if case_name in seen_cases:
                    raise ValueError(f"duplicate enum case `{case_name}` in `{name}`")
                seen_cases.add(case_name)
                case_value = _literal(raw_case_value)
                if not isinstance(case_value, int) or isinstance(case_value, bool):
                    raise ValueError(f"enum case `{name}::{case_name}` requires an integer literal")
                cases.append(TemplateEnumCase(case_name, case_value))
            enums.append(TemplateEnum(name, underlying, tuple(cases), flags))

        constants: list[TemplateConstant] = []
        seen_constants: set[str] = set()
        for raw_name, raw_type, raw_value in raw_constants:
            constant_name = _require_ident(_value(raw_name), "constant name")
            if constant_name in seen_constants:
                raise ValueError(f"duplicate constant `{constant_name}`")
            seen_constants.add(constant_name)
            constant_type = parse_template_type(
                _value(raw_type),
                span,
                struct_names,
                allow_void=False,
                handle_names=handle_names,
                enum_names=enum_names,
            )
            value = _literal(raw_value)
            _validate_constant_literal(constant_name, constant_type, value, enum_names)
            constants.append(TemplateConstant(constant_name, constant_type, value))

        structs: list[TemplateStruct] = []
        for name, raw_fields in raw_structs:
            if not name[0].isupper():
                raise ValueError(f"struct name `{name}` must start with an uppercase letter")
            if not raw_fields:
                raise ValueError(f"ZLC_STRUCT `{name}` must contain at least one ZLC_FIELD")
            fields: list[TemplateField] = []
            seen_fields: set[str] = set()
            for raw_name, raw_type in raw_fields:
                field_name = _require_ident(_value(raw_name), "field name")
                if field_name in seen_fields:
                    raise ValueError(f"duplicate field `{field_name}` in `{name}`")
                seen_fields.add(field_name)
                type_node = parse_template_type(
                    _value(raw_type),
                    span,
                    struct_names,
                    allow_void=False,
                    handle_names=handle_names,
                    enum_names=enum_names,
                )
                if isinstance(type_node, ast.NamedTypeNode) and type_node.name == name:
                    raise ValueError(f"ZLC_STRUCT `{name}` cannot contain itself by value")
                fields.append(TemplateField(field_name, type_node))
            structs.append(TemplateStruct(name, tuple(fields)))

        functions: list[TemplateFunction] = []
        seen_functions: set[str] = set()
        for args in raw_functions:
            name = _require_ident(_value(args[0]), "function name")
            symbol = _require_ident(_value(args[1]), "C symbol")
            if name in seen_functions:
                raise ValueError(f"duplicate ZLC_FN `{name}`")
            seen_functions.add(name)
            params: list[tuple[str, ast.TypeNode, TemplateTypePolicy]] = []
            seen_params: set[str] = set()
            failure: tuple[str, int | None, str | None] | None = None
            for raw_param in args[3:]:
                if re.match(r"^ZLC_FAIL\s*\(", raw_param.strip()):
                    if failure is not None:
                        raise ValueError(f"duplicate ZLC_FAIL in `{name}`")
                    match = re.fullmatch(r"ZLC_FAIL\s*\((.*)\)", raw_param.strip(), flags=re.S)
                    assert match is not None
                    fail_args = _split_args(match.group(1))
                    if not 1 <= len(fail_args) <= 3:
                        raise ValueError("ZLC_FAIL expects policy, optional value, and optional message symbol")
                    kind = _value(fail_args[0])
                    fail_value: int | None = None
                    message_symbol: str | None = None
                    if kind == "equal":
                        if len(fail_args) < 2:
                            raise ValueError("ZLC_FAIL(equal, value, ...) requires a value")
                        parsed_value = _literal(fail_args[1])
                        if not isinstance(parsed_value, int) or isinstance(parsed_value, bool):
                            raise ValueError("ZLC_FAIL equal value must be an integer")
                        fail_value = parsed_value
                        if len(fail_args) == 3:
                            message_symbol = _require_ident(_value(fail_args[2]), "error message symbol")
                    else:
                        if kind not in {"null", "nonzero", "negative"}:
                            raise ValueError(f"unsupported ZLC_FAIL policy `{kind}`")
                        if len(fail_args) == 2:
                            message_symbol = _require_ident(_value(fail_args[1]), "error message symbol")
                        elif len(fail_args) == 3:
                            raise ValueError(f"ZLC_FAIL({kind}, ...) accepts at most one message symbol")
                    failure = (kind, fail_value, message_symbol)
                    continue
                raw_name, raw_type = _nested_macro(raw_param, "ZLC_PARAM", 2)
                param_name = _require_ident(_value(raw_name), "parameter name")
                if param_name in seen_params:
                    raise ValueError(f"duplicate parameter `{param_name}` in `{name}`")
                seen_params.add(param_name)
                param_type, param_policy = parse_template_ffi_type(
                    _value(raw_type),
                    span,
                    struct_names,
                    handle_names,
                    enum_names,
                    allow_void=False,
                )
                params.append((param_name, param_type, param_policy))
            return_type, return_policy = parse_template_ffi_type(
                _value(args[2]),
                span,
                struct_names,
                handle_names,
                enum_names,
                allow_void=True,
            )
            if return_policy.direction != "in":
                raise ValueError("out/inout are valid only on parameters")
            if isinstance(return_type, ast.NamedTypeNode) and return_type.name in {"__zl_slice", "__zl_mut_slice"}:
                raise ValueError("Slice values cannot be returned; they are synchronous native-call parameters")
            if abi_version < 3 and (
                return_policy.ownership is not None
                or isinstance(return_type, (ast.OptionalTypeNode,))
                or any(
                    policy.direction != "in" or policy.ownership is not None
                    for _, _, policy in params
                )
                or any(
                    isinstance(typ, ast.NamedTypeNode) and typ.name in {"__zl_slice", "__zl_mut_slice"}
                    for _, typ, _ in params
                )
                or failure is not None
            ):
                raise ValueError("ownership, Slice, out/inout, optional, and ZLC_FAIL require ZLC_ABI(3)")
            for _, _, policy in params:
                if policy.parent is not None:
                    raise ValueError("borrowed<T, parent> is valid only as a return type")
            if return_policy.parent is not None and return_policy.parent not in seen_params:
                raise ValueError(f"borrowed return refers to unknown parameter `{return_policy.parent}`")
            if failure is not None and failure[0] == "null":
                target = return_type.inner if isinstance(return_type, ast.OptionalTypeNode) else return_type
                if not isinstance(target, ast.NamedTypeNode) or target.name not in handle_names:
                    raise ValueError("ZLC_FAIL(null, ...) requires a handle return type")
            if failure is not None and failure[0] in {"nonzero", "negative", "equal"}:
                if not isinstance(return_type, ast.NamedTypeNode) or return_type.name not in {
                    "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "usize"
                }:
                    raise ValueError(f"ZLC_FAIL({failure[0]}, ...) requires an integer return type")
                if failure[0] == "negative" and return_type.name.startswith("u"):
                    raise ValueError("ZLC_FAIL(negative, ...) requires a signed integer return type")
            functions.append(TemplateFunction(
                name,
                symbol,
                tuple(params),
                return_type,
                return_policy,
                failure,
            ))

        base = path.parent
        path_values = {
            key: tuple((base / value).resolve() for value in metadata[key])
            for key in _PATH_MACROS.values()
        }
        for key, values in path_values.items():
            for value in values:
                if value != package_root and package_root not in value.parents:
                    raise ValueError(f"{key} path escapes the package root: {value}")
        for source in path_values["sources"]:
            if source.suffix.lower() not in {".c", ".cc", ".cpp", ".cxx"} or not source.is_file():
                raise ValueError(f"native source not found or not a C/C++ source file: {source}")
        for header in path_values["headers"]:
            if not header.is_file():
                raise ValueError(f"native header not found: {header}")
        for directory in (*path_values["include_dirs"], *path_values["lib_dirs"]):
            if not directory.is_dir():
                raise ValueError(f"native directory not found: {directory}")
        libraries = tuple(metadata["libraries"])
        for library in libraries:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.+-]*", library):
                raise ValueError(f"invalid native library name `{library}`")
        path_identity = os.path.normcase(str(path)).replace("\\", "/")
        digest = hashlib.sha256(path_identity.encode("utf-8")).hexdigest()[:16]
        hidden_name = f"__zlcm_{digest}__{path.name}"
        return NativeTemplate(
            path,
            module_name,
            hidden_name,
            abi_version,
            tuple(handles),
            tuple(enums),
            tuple(constants),
            tuple(structs),
            tuple(functions),
            _dedupe(path_values["headers"]),
            _dedupe(path_values["sources"]),
            _dedupe(path_values["include_dirs"]),
            _dedupe(path_values["lib_dirs"]),
            _dedupe(list(libraries)),
            _dedupe([_safe_flag(value, "ZLC_CFLAG") for value in metadata["cflags"]]),
            _dedupe([_safe_flag(value, "ZLC_LDFLAG") for value in metadata["ldflags"]]),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise CompileError(f"invalid c_module template `{path.name}`: {exc}", span) from exc
