from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 package dependency
    import tomli as tomllib


MAX_HEADER_BYTES = 4 * 1024 * 1024
MAX_AST_BYTES = 32 * 1024 * 1024
MAX_AST_NODES = 200_000
MAX_AST_DEPTH = 128
PARSER_TIMEOUT_SECONDS = 30


class BindgenError(Exception):
    pass


@dataclass(frozen=True)
class CParam:
    name: str
    c_type: str


@dataclass(frozen=True)
class CFunction:
    name: str
    return_type: str
    params: tuple[CParam, ...]
    prototype: str


@dataclass(frozen=True)
class BindgenResult:
    config: Path
    template: Path
    adapter_c: Path
    adapter_h: Path
    parser: tuple[str, ...]
    callable_count: int
    todo_count: int


_SCALARS = {
    "void": "void",
    "_Bool": "bool",
    "bool": "bool",
    "char": "i8",
    "signed char": "i8",
    "unsigned char": "u8",
    "short": "i16",
    "short int": "i16",
    "signed short": "i16",
    "signed short int": "i16",
    "unsigned short": "u16",
    "unsigned short int": "u16",
    "int": "i32",
    "signed": "i32",
    "signed int": "i32",
    "unsigned": "u32",
    "unsigned int": "u32",
    "long long": "i64",
    "long long int": "i64",
    "signed long long": "i64",
    "signed long long int": "i64",
    "unsigned long long": "u64",
    "unsigned long long int": "u64",
    "int8_t": "i8",
    "int16_t": "i16",
    "int32_t": "i32",
    "int64_t": "i64",
    "uint8_t": "u8",
    "uint16_t": "u16",
    "uint32_t": "u32",
    "uint64_t": "u64",
    "size_t": "usize",
    "float": "f32",
    "double": "f64",
}

_C_ABI_TYPES = {
    "bool": "bool",
    "i8": "int8_t",
    "i16": "int16_t",
    "i32": "int32_t",
    "i64": "int64_t",
    "u8": "uint8_t",
    "u16": "uint16_t",
    "u32": "uint32_t",
    "u64": "uint64_t",
    "usize": "size_t",
    "f32": "float",
    "f64": "double",
    "void": "void",
    "str": "ZL_String",
}


def _safe_module_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise BindgenError("--module must be a C-style identifier")
    return value


def _validate_define(value: str) -> str:
    if "\n" in value or "\r" in value or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:=[^\s\x00-\x1f]*)?", value):
        raise BindgenError(f"unsafe preprocessor define `{value}`")
    return value


def find_bindgen_parser() -> list[str]:
    override = os.environ.get("ZY_BINDGEN_CLANG", "").strip()
    if override:
        command = shlex.split(override, posix=not sys.platform.startswith("win"))
        if not command:
            raise BindgenError("ZY_BINDGEN_CLANG is empty")
        return command
    roots = [Path(__file__).resolve().parents[2]]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
    executable = "zig.exe" if sys.platform.startswith("win") else "zig"
    for root in roots:
        for candidate in (root / "toolchain" / executable, root / "toolchain" / "zig" / executable):
            if candidate.is_file():
                return [str(candidate), "cc"]
    clang = shutil.which("clang") or shutil.which("clang.exe")
    if clang:
        return [clang]
    raise BindgenError("no bindgen parser found; set ZY_BINDGEN_CLANG, use bundled Zig, or install Clang")


def _run_ast_parser(
    header: Path,
    include_dirs: tuple[Path, ...],
    defines: tuple[str, ...],
) -> tuple[dict, tuple[str, ...]]:
    parser = find_bindgen_parser()
    command = [
        *parser,
        "-x", "c", "-std=c11", "-fsyntax-only",
        *(item for directory in include_dirs for item in ("-I", str(directory))),
        *(f"-D{_validate_define(value)}" for value in defines),
        "-Xclang", "-ast-dump=json", "-",
    ]
    try:
        completed = subprocess.run(
            command,
            input=header.read_bytes(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=header.parent,
            timeout=PARSER_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BindgenError(f"bindgen parser failed: {exc}") from exc
    if len(completed.stdout) > MAX_AST_BYTES or len(completed.stderr) > MAX_AST_BYTES:
        raise BindgenError(f"bindgen parser output exceeds {MAX_AST_BYTES} bytes")
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BindgenError("bindgen parser returned invalid JSON AST") from exc
    if completed.returncode != 0:
        meaningful = [
            line
            for line in detail.splitlines()
            if line.strip()
            and "argument unused during compilation: '-c'" not in line
            and not line.rstrip().endswith(":1:1: error: FileNotFound")
        ]
        if meaningful or Path(parser[0]).name.lower() not in {"zig", "zig.exe"}:
            raise BindgenError(f"Clang rejected `{header}`: {detail[:4000]}")
    return parsed, tuple(parser)


def _walk_ast(root: dict) -> list[dict]:
    found: list[dict] = []
    stack: list[tuple[dict, int]] = [(root, 0)]
    visited = 0
    while stack:
        node, depth = stack.pop()
        visited += 1
        if visited > MAX_AST_NODES:
            raise BindgenError(f"Clang AST exceeds {MAX_AST_NODES} nodes")
        if depth > MAX_AST_DEPTH:
            raise BindgenError(f"Clang AST exceeds depth {MAX_AST_DEPTH}")
        found.append(node)
        for child in reversed(node.get("inner", ())):
            if isinstance(child, dict):
                stack.append((child, depth + 1))
    return found


def _function_return_type(qual_type: str) -> str:
    marker = qual_type.find(" (")
    return qual_type[:marker].strip() if marker >= 0 else qual_type.strip()


def _extract_functions(ast_root: dict, header: Path) -> tuple[CFunction, ...]:
    target = os.path.normcase(str(header.resolve()))
    functions: dict[str, CFunction] = {}
    for node in _walk_ast(ast_root):
        if node.get("kind") != "FunctionDecl" or node.get("isImplicit"):
            continue
        name = node.get("name")
        qual_type = node.get("type", {}).get("qualType")
        if not isinstance(name, str) or not isinstance(qual_type, str):
            continue
        location = node.get("loc", {})
        file_name = location.get("file")
        included = location.get("includedFrom", {}).get("file")
        parser_stdin_file = isinstance(file_name, str) and file_name.endswith("-stdin.c")
        if file_name and file_name != "<stdin>" and not parser_stdin_file and os.path.normcase(str(Path(file_name).resolve())) != target:
            continue
        if not file_name and included:
            continue
        params: list[CParam] = []
        unnamed = 0
        for child in node.get("inner", ()):
            if child.get("kind") != "ParmVarDecl":
                continue
            param_name = child.get("name")
            if not param_name:
                unnamed += 1
                param_name = f"arg{unnamed}"
            param_type = child.get("type", {}).get("qualType")
            if not isinstance(param_type, str):
                break
            params.append(CParam(param_name, param_type.strip()))
        else:
            prototype = f"{_function_return_type(qual_type)} {name}(" + ", ".join(
                f"{item.c_type} {item.name}" for item in params
            ) + ")"
            functions[name] = CFunction(name, _function_return_type(qual_type), tuple(params), prototype)
    return tuple(functions[name] for name in sorted(functions))


def _normalize_c_type(value: str) -> str:
    result = re.sub(r"\b(?:const|volatile|restrict|_Atomic)\b", "", value)
    return " ".join(result.split())


def _automatic_type(value: str, *, return_position: bool = False) -> str | None:
    normalized = _normalize_c_type(value)
    if normalized in _SCALARS:
        return _SCALARS[normalized]
    if not return_position and re.fullmatch(r"const\s+char\s*\*", value.strip()):
        return "str"
    return None


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_initial_config(module: str, header: Path, functions: tuple[CFunction, ...], library: str | None) -> str:
    lines = [
        "# Generated review file for `zy bindgen`.",
        "# Pointer declarations stay disabled until ownership and length rules are explicit.",
        "# Add [handles.Name] with c_type/drop, and [functions.fn.lengths] for Slice length parameters.",
        "[module]",
        f"name = {_toml_quote(module)}",
        f"header = {_toml_quote(str(header.resolve()))}",
    ]
    if library:
        lines.append(f"library = {_toml_quote(library)}")
    for function in functions:
        return_type = _automatic_type(function.return_type, return_position=True)
        mappings = {param.name: _automatic_type(param.c_type) for param in function.params}
        enabled = return_type is not None and all(value is not None for value in mappings.values())
        lines.extend([
            "",
            f"[functions.{function.name}]",
            f"enabled = {'true' if enabled else 'false'}",
            f"prototype = {_toml_quote(function.prototype)}",
            f"return_type = {_toml_quote(return_type or 'TODO')}",
        ])
        if not enabled:
            lines.append("todo = \"review pointer ownership, length, nullability, and error policy\"")
        if mappings:
            lines.append(f"[functions.{function.name}.params]")
            for param in function.params:
                lines.append(f"{param.name} = {_toml_quote(mappings[param.name] or 'TODO')}")
    return "\n".join(lines) + "\n"


def _load_config(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            value = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BindgenError(f"cannot read bindgen config `{path}`: {exc}") from exc
    if not isinstance(value.get("module"), dict) or not isinstance(value.get("functions", {}), dict):
        raise BindgenError("bindgen config requires [module] and [functions.*] tables")
    return value


def _valid_zy_type(value: str) -> bool:
    if value in _C_ABI_TYPES:
        return True
    wrapper = re.fullmatch(r"(optional|owned|borrowed|borrowed_static|consumed|out|inout)<(.+)>", value)
    if wrapper:
        kind, inner = wrapper.groups()
        if kind in {"out", "inout"}:
            return inner in _C_ABI_TYPES and inner != "void"
        if kind == "optional":
            return _handle_name(inner) is not None
        if kind == "borrowed" and "," in inner:
            handle, parent = (item.strip() for item in inner.split(",", 1))
            return re.fullmatch(r"[A-Za-z_]\w*", handle) is not None and re.fullmatch(r"[A-Za-z_]\w*", parent) is not None
        return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", inner) is not None
    return re.fullmatch(r"(?:Mut)?Slice<(?:u8|i8|u16|i16|u32|i32|u64|i64|f32|f64)>", value) is not None


def _adapter_c_type(zy_type: str) -> str:
    if zy_type in _C_ABI_TYPES:
        return _C_ABI_TYPES[zy_type]
    if zy_type.startswith("Slice<"):
        return "ZL_Slice"
    if zy_type.startswith("MutSlice<"):
        return "ZL_MutSlice"
    if zy_type.startswith("optional<"):
        return _adapter_c_type(zy_type[len("optional<"):-1])
    if zy_type.startswith("out<") or zy_type.startswith("inout<"):
        return f"{_adapter_c_type(zy_type[zy_type.index('<') + 1:-1])}*"
    if any(zy_type.startswith(prefix) for prefix in ("owned<", "borrowed<", "borrowed_static<", "consumed<", "optional<")):
        return "ZL_Handle"
    raise BindgenError(f"unsupported configured ABI type `{zy_type}`")


def _forward_argument(name: str, c_type: str, zy_type: str, module: str, handle_tags: dict[str, str] | None = None) -> str:
    if zy_type == "str":
        return f"({c_type})zl_string_data({name})"
    if zy_type.startswith("Slice<") or zy_type.startswith("MutSlice<"):
        return f"({c_type}){name}.data"
    if zy_type.startswith("out<") or zy_type.startswith("inout<"):
        return f"({c_type}){name}"
    if zy_type.startswith("optional<"):
        return _forward_argument(name, c_type, zy_type[len("optional<"):-1], module, handle_tags)
    handle = _handle_name(zy_type)
    if handle:
        type_id = (handle_tags or {}).get(handle, f"{module}:{handle}")
        if zy_type.startswith("consumed<"):
            return f"({c_type})zl_handle_take({name}, \"{type_id}\")"
        return f"({c_type})zl_handle_data({name}, \"{type_id}\")"
    return f"({c_type}){name}"


def _handle_name(zy_type: str) -> str | None:
    names = re.findall(r"(?:owned|borrowed|borrowed_static|consumed)<([A-Za-z_][A-Za-z0-9_]*)", zy_type)
    return names[-1] if names else None


def _native_type_tag(template_path: Path, handle: str) -> str:
    identity = os.path.normcase(str(template_path.resolve())).replace("\\", "/")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"zlcm:{digest}:{handle}"


def _failure_macro(rule: dict, by_name: dict[str, CFunction], adapter_h: list[str], adapter_c: list[str], module: str) -> str | None:
    failure = rule.get("failure")
    if failure is None:
        return None
    if not isinstance(failure, str) or not re.fullmatch(r"null|nonzero|negative|equal\(-?\d+\)", failure):
        raise BindgenError("failure must be null, nonzero, negative, or equal(INTEGER)")
    message_name = rule.get("error_message")
    message_wrapper: str | None = None
    if message_name is not None:
        if not isinstance(message_name, str) or message_name not in by_name:
            raise BindgenError(f"error_message `{message_name}` is not a function from the parsed header")
        message = by_name[message_name]
        if message.params or _normalize_c_type(message.return_type) not in {"char *", "const char *"}:
            raise BindgenError("error_message must name a zero-argument function returning char*")
        message_wrapper = f"zy_bindgen_{module}_{message_name}_message"
        declaration = f"ZL_String {message_wrapper}(void);"
        if declaration not in adapter_h:
            adapter_h.append(declaration)
            adapter_c.extend([
                f"ZL_String {message_wrapper}(void) {{",
                f"    return zl_string_borrow((const char*){message_name}());",
                "}", "",
            ])
    if failure.startswith("equal("):
        parts = ["equal", failure[6:-1]]
    else:
        parts = [failure]
    if message_wrapper:
        parts.append(message_wrapper)
    return f"ZLC_FAIL({', '.join(parts)})"


def _write_generated(path: Path, content: str, force: bool) -> None:
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == content:
            return
        if not force:
            raise BindgenError(f"refusing to overwrite changed generated file `{path}`; review it or use --force")
    path.write_text(content, encoding="utf-8", newline="\n")


def generate_bindings(
    header: Path,
    module: str,
    out_dir: Path,
    *,
    include_dirs: tuple[Path, ...] = (),
    defines: tuple[str, ...] = (),
    library: str | None = None,
    config_path: Path | None = None,
    force: bool = False,
) -> BindgenResult:
    module = _safe_module_name(module)
    header = header.resolve()
    if not header.is_file() or header.suffix.lower() != ".h":
        raise BindgenError(f"C header not found or not a .h file: {header}")
    if header.stat().st_size > MAX_HEADER_BYTES:
        raise BindgenError(f"header exceeds {MAX_HEADER_BYTES} bytes")
    include_dirs = tuple(path.resolve() for path in include_dirs)
    if any(not path.is_dir() for path in include_dirs):
        raise BindgenError("every -I path must be an existing directory")
    if library is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.+-]*", library):
        raise BindgenError("--library contains unsupported characters")
    ast_root, parser = _run_ast_parser(header, include_dirs, defines)
    functions = _extract_functions(ast_root, header)
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    config = (config_path.resolve() if config_path else out_dir / f"{module}.zybind.toml")
    if not config.exists():
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(_render_initial_config(module, header, functions, library), encoding="utf-8", newline="\n")
    settings = _load_config(config)
    if settings["module"].get("name") != module:
        raise BindgenError("bindgen config module name does not match --module")

    function_settings = settings.get("functions", {})
    handle_settings = settings.get("handles", {})
    if not isinstance(handle_settings, dict):
        raise BindgenError("[handles.*] entries must be TOML tables")
    template_path = out_dir / f"{module}.zlcm.h"
    template_lines = [
        "ZLC_ABI(3)",
        f"ZLC_MODULE({module})",
        f'ZLC_SOURCE("{module}_zy.c")',
        f'ZLC_HEADER("{module}_zy.h")',
    ]
    selected_library = library or settings["module"].get("library")
    if selected_library:
        template_lines.append(f'ZLC_LIB("{selected_library}")')
    header_guard = f"ZY_BINDGEN_{module.upper()}_H"
    adapter_h = [
        f"#ifndef {header_guard}", f"#define {header_guard}",
        "#include <stdint.h>", "#include <stddef.h>", '#include "zyenlang_c_abi.h"', "",
    ]
    adapter_c = [
        f'#include "{module}_zy.h"',
        f'#include {json.dumps(str(header).replace(chr(92), "/"))}',
        "",
    ]
    handle_rules: dict[str, tuple[str, str, str]] = {}
    for handle_name in sorted(handle_settings):
        rule = handle_settings[handle_name]
        if not re.fullmatch(r"[A-Za-z_]\w*", handle_name) or not isinstance(rule, dict):
            raise BindgenError("handle names must be identifiers with [handles.Name] tables")
        c_type = rule.get("c_type")
        drop = rule.get("drop")
        if not isinstance(c_type, str) or not re.fullmatch(r"(?:const\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+", c_type.strip()):
            raise BindgenError(f"handle `{handle_name}` requires a pointer c_type")
        if not isinstance(drop, str) or not re.fullmatch(r"[A-Za-z_]\w*", drop):
            raise BindgenError(f"handle `{handle_name}` requires a destructor function name")
        shim = f"zy_bindgen_{module}_drop_{handle_name}"
        tag = _native_type_tag(template_path, handle_name)
        handle_rules[handle_name] = (c_type.strip(), shim, tag)
        template_lines.append(f"ZLC_HANDLE({handle_name}, ZLC_DROP({shim}))")
        adapter_c.extend([
            f"static void {shim}(void* value) {{",
            f"    {drop}(({c_type.strip()})value);",
            "}", "",
        ])
    callable_count = 0
    todo_count = 0
    by_name = {item.name: item for item in functions}
    for name in sorted(function_settings):
        raw = by_name.get(name)
        rule = function_settings[name]
        if raw is None or not isinstance(rule, dict):
            continue
        if not rule.get("enabled", False):
            todo_count += 1
            template_lines.append(f"// TODO bindgen skipped: {rule.get('prototype', name)}")
            continue
        return_type = str(rule.get("return_type", "TODO"))
        params_rule = rule.get("params", {})
        lengths = rule.get("lengths", {})
        if not _valid_zy_type(return_type) or not isinstance(params_rule, dict):
            raise BindgenError(f"function `{name}` has an invalid return_type or params mapping")
        if not isinstance(lengths, dict):
            raise BindgenError(f"function `{name}` lengths mapping must be a table")
        length_to_slice: dict[str, str] = {}
        raw_params = {param.name: param for param in raw.params}
        for slice_name, length_name in lengths.items():
            if not isinstance(slice_name, str) or not isinstance(length_name, str):
                raise BindgenError(f"function `{name}` length mappings must contain parameter names")
            if slice_name not in raw_params or length_name not in raw_params or slice_name == length_name:
                raise BindgenError(f"function `{name}` has an invalid `{slice_name}` length mapping")
            slice_type = str(params_rule.get(slice_name, "TODO"))
            if not (slice_type.startswith("Slice<") or slice_type.startswith("MutSlice<")):
                raise BindgenError(f"function `{name}` length source `{slice_name}` must be a Slice")
            if _automatic_type(raw_params[length_name].c_type) not in {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "usize"}:
                raise BindgenError(f"function `{name}` length parameter `{length_name}` must be an integer")
            if length_name in length_to_slice:
                raise BindgenError(f"function `{name}` length parameter `{length_name}` is mapped twice")
            length_to_slice[length_name] = slice_name
        mapped: list[tuple[CParam, str]] = []
        for param in raw.params:
            if param.name in length_to_slice:
                continue
            zy_type = str(params_rule.get(param.name, "TODO"))
            if not _valid_zy_type(zy_type):
                raise BindgenError(f"function `{name}` parameter `{param.name}` needs an explicit ABI type")
            handle = _handle_name(zy_type)
            if handle and handle not in handle_rules:
                raise BindgenError(f"function `{name}` uses undeclared handle `{handle}`")
            mapped.append((param, zy_type))
        return_handle = _handle_name(return_type)
        if return_handle and return_handle not in handle_rules:
            raise BindgenError(f"function `{name}` returns undeclared handle `{return_handle}`")
        wrapper = f"zy_bindgen_{module}_{name}"
        c_params = ", ".join(f"{_adapter_c_type(typ)} {param.name}" for param, typ in mapped) or "void"
        adapter_h.append(f"{_adapter_c_type(return_type)} {wrapper}({c_params});")
        mapped_by_name = {param.name: typ for param, typ in mapped}
        handle_tags = {key: value[2] for key, value in handle_rules.items()}
        call_args = ", ".join(
            f"({param.c_type}){length_to_slice[param.name]}.len"
            if param.name in length_to_slice
            else _forward_argument(param.name, param.c_type, mapped_by_name[param.name], module, handle_tags)
            for param in raw.params
        )
        adapter_c.append(f"{_adapter_c_type(return_type)} {wrapper}({c_params}) {{")
        if return_type == "void":
            adapter_c.extend([f"    {name}({call_args});", "}", ""])
        elif return_type.startswith("owned<") or return_type.startswith("optional<owned<"):
            assert return_handle is not None
            _, drop_shim, tag = handle_rules[return_handle]
            adapter_c.extend([
                f"    void* value = (void*){name}({call_args});",
                f"    return zl_handle_adopt(value, \"{tag}\", {drop_shim});",
                "}", "",
            ])
        elif return_type.startswith("borrowed_static<"):
            assert return_handle is not None
            tag = handle_rules[return_handle][2]
            adapter_c.extend([
                f"    void* value = (void*){name}({call_args});",
                f"    return zl_handle_borrow(value, \"{tag}\");",
                "}", "",
            ])
        elif return_type.startswith("borrowed<"):
            assert return_handle is not None
            borrowed = return_type[len("borrowed<"):-1].split(",", 1)
            if len(borrowed) != 2 or borrowed[1].strip() not in mapped_by_name:
                raise BindgenError(f"function `{name}` borrowed return must name its parent parameter")
            parent = borrowed[1].strip()
            if _handle_name(mapped_by_name[parent]) is None:
                raise BindgenError(f"function `{name}` borrowed parent `{parent}` must be a handle")
            tag = handle_rules[return_handle][2]
            adapter_c.extend([
                f"    void* value = (void*){name}({call_args});",
                f"    return zl_handle_borrow_from(value, \"{tag}\", {parent});",
                "}", "",
            ])
        elif return_type == "str":
            adapter_c.extend([f"    return zl_string_borrow((const char*){name}({call_args}));", "}", ""])
        else:
            adapter_c.extend([f"    return ({_adapter_c_type(return_type)}){name}({call_args});", "}", ""])
        zlc_params = ", ".join(f"ZLC_PARAM({param.name}, {typ})" for param, typ in mapped)
        failure = _failure_macro(rule, by_name, adapter_h, adapter_c, module)
        entries = [value for value in (zlc_params, failure) if value]
        suffix = f", {', '.join(entries)}" if entries else ""
        template_lines.append(f"ZLC_FN({name}, {wrapper}, {return_type}{suffix})")
        callable_count += 1
    adapter_h.extend(["", f"#endif /* {header_guard} */", ""])

    template_text = "\n".join(template_lines) + "\n"
    adapter_h_text = "\n".join(adapter_h)
    adapter_c_text = "\n".join(adapter_c)
    adapter_c_path = out_dir / f"{module}_zy.c"
    adapter_h_path = out_dir / f"{module}_zy.h"
    _write_generated(template_path, template_text, force)
    _write_generated(adapter_c_path, adapter_c_text, force)
    _write_generated(adapter_h_path, adapter_h_text, force)
    return BindgenResult(config, template_path, adapter_c_path, adapter_h_path, parser, callable_count, todo_count)
