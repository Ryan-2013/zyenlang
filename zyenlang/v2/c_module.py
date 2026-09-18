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
class TemplateFunction:
    name: str
    symbol: str
    params: tuple[tuple[str, ast.TypeNode], ...]
    return_type: ast.TypeNode


@dataclass(frozen=True)
class NativeTemplate:
    path: Path
    module_name: str
    hidden_name: str
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
        elif char == "," and depth == 0:
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


def parse_template_type(raw: str, span: SourceSpan, struct_names: set[str], *, allow_void: bool) -> ast.TypeNode:
    value = "".join(raw.split())
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
            parse_template_type(item, span, struct_names, allow_void=False)
            for item in (_split_args(params_body) if params_body else [])
        )
        return ast.FunctionTypeNode(
            span,
            params,
            parse_template_type(tail, span, struct_names, allow_void=True),
        )
    if value in struct_names:
        return ast.NamedTypeNode(span, value)
    if value in {"ZL_List", "ZL_list", "List"} or value.startswith(("ZL_List<", "ZL_list<", "List<")):
        raise ValueError(
            "List is not a stable c_module ABI type in v0.3; expose scalar/str/struct values or an opaque native handle"
        )
    if value.startswith(("ZL_ptr", "ptr<", "Raw<")):
        raise ValueError(
            "raw pointers are not source-level c_module values in v0.3; wrap the pointer in a native handle API"
        )
    supported = "fixed-width numbers, bool, str, void, fn(...)T, and declared ZLC_STRUCT names"
    raise ValueError(f"unsupported c_module type `{raw}`; expected {supported}")


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
        raw_structs: list[tuple[str, list[list[str]]]] = []
        raw_functions: list[list[str]] = []
        metadata: dict[str, list[str]] = {
            "headers": [], "sources": [], "include_dirs": [], "lib_dirs": [],
            "libraries": [], "cflags": [], "ldflags": [],
        }
        platform = native_platform_name().upper()
        for macro, body in calls:
            args = _split_args(body)
            if macro == "ZLC_MODULE":
                if len(args) != 1:
                    raise ValueError("ZLC_MODULE expects 1 argument")
                if module_name:
                    raise ValueError("template contains more than one ZLC_MODULE")
                module_name = _require_ident(_value(args[0]), "module name")
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
            if macro in {"ZLC_FIELD", "ZLC_PARAM"}:
                continue
            raise ValueError(f"unknown c_module template macro `{macro}`")
        if not module_name:
            raise ValueError("template is missing ZLC_MODULE")
        if not raw_functions:
            raise ValueError("template must contain at least one ZLC_FN")

        struct_names = {name for name, _ in raw_structs}
        if len(struct_names) != len(raw_structs):
            raise ValueError("duplicate ZLC_STRUCT name")
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
                type_node = parse_template_type(_value(raw_type), span, struct_names, allow_void=False)
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
            params: list[tuple[str, ast.TypeNode]] = []
            seen_params: set[str] = set()
            for raw_param in args[3:]:
                raw_name, raw_type = _nested_macro(raw_param, "ZLC_PARAM", 2)
                param_name = _require_ident(_value(raw_name), "parameter name")
                if param_name in seen_params:
                    raise ValueError(f"duplicate parameter `{param_name}` in `{name}`")
                seen_params.add(param_name)
                params.append((
                    param_name,
                    parse_template_type(_value(raw_type), span, struct_names, allow_void=False),
                ))
            functions.append(TemplateFunction(
                name,
                symbol,
                tuple(params),
                parse_template_type(_value(args[2]), span, struct_names, allow_void=True),
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
            if source.suffix.lower() != ".c" or not source.is_file():
                raise ValueError(f"native source not found or not a .c file: {source}")
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
