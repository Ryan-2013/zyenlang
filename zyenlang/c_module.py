from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Iterable


BASIC_TYPES = {"int", "float", "bool", "str", "void", "List", "ptr"}
TYPE_ALIASES = {
    "ZL_List": "List",
    "ZL_list": "List",
    "ZL_ptr": "ptr",
}
NATIVE_KEYS = ("headers", "sources", "include_dirs", "lib_dirs", "libs", "cflags", "ldflags")


def native_platform_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unix"


def empty_native_metadata() -> dict:
    return {key: [] for key in NATIVE_KEYS}


def is_native_module_path(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".zlcm.h") or name.endswith(".zlcm.json")


def _strip_c_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out: list[str] = []
    for line in text.splitlines():
        if "//" in line:
            line = line.split("//", 1)[0]
        out.append(line)
    return "\n".join(out)


def _split_macro_args(body: str) -> list[str]:
    args: list[str] = []
    start = 0
    depth = 0
    in_str = False
    escaped = False
    for i, ch in enumerate(body):
        if in_str:
            if ch == '"' and not escaped:
                in_str = False
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
            continue
        if ch == '"':
            in_str = True
            escaped = False
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth -= 1
            continue
        if ch == "," and depth == 0:
            args.append(body[start:i].strip())
            start = i + 1
    tail = body[start:].strip()
    if tail:
        args.append(tail)
    return args


def _macro_calls(text: str) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    i = 0
    while i < len(text):
        m = re.search(r"\b(ZLC_[A-Z_]+)\s*\(", text[i:])
        if not m:
            break
        name = m.group(1)
        open_i = i + m.end() - 1
        j = open_i + 1
        depth = 1
        in_str = False
        escaped = False
        while j < len(text):
            ch = text[j]
            if in_str:
                if ch == '"' and not escaped:
                    in_str = False
                escaped = ch == "\\" and not escaped
                if ch != "\\":
                    escaped = False
                j += 1
                continue
            if ch == '"':
                in_str = True
                escaped = False
                j += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    calls.append((name, text[open_i + 1:j].strip()))
                    j += 1
                    break
            j += 1
        i = j
    return calls


def _template_value(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return str(json.loads(raw))
    return raw


def _template_param(raw: str) -> dict:
    raw = raw.strip()
    m = re.fullmatch(r"ZLC_PARAM\s*\((.*)\)", raw, flags=re.S)
    if not m:
        raise ValueError(f"expected ZLC_PARAM(name, type), got `{raw}`")
    args = _split_macro_args(m.group(1))
    if len(args) != 2:
        raise ValueError(f"ZLC_PARAM expects 2 args, got {len(args)} in `{raw}`")
    return {"name": _template_value(args[0]), "type": _template_value(args[1])}


def _template_field(raw: str) -> dict:
    raw = raw.strip()
    match = re.fullmatch(r"ZLC_FIELD\s*\((.*)\)", raw, flags=re.S)
    if not match:
        raise ValueError(f"expected ZLC_FIELD(name, type), got `{raw}`")
    args = _split_macro_args(match.group(1))
    if len(args) != 2:
        raise ValueError(f"ZLC_FIELD expects 2 args, got {len(args)} in `{raw}`")
    return {"name": _template_value(args[0]), "type": _template_value(args[1])}


def read_template_header(path: Path) -> dict:
    text = _strip_c_comments(path.read_text(encoding="utf-8"))
    data: dict = {
        "headers": [],
        "sources": [],
        "include_dirs": [],
        "lib_dirs": [],
        "libs": [],
        "cflags": [],
        "ldflags": [],
        "structs": [],
        "functions": [],
    }
    for macro, body in _macro_calls(text):
        args = _split_macro_args(body)
        if macro == "ZLC_MODULE":
            if len(args) != 1:
                raise ValueError("ZLC_MODULE expects 1 arg")
            data["module"] = _template_value(args[0])
        elif macro == "ZLC_HEADER":
            if len(args) != 1:
                raise ValueError("ZLC_HEADER expects 1 arg")
            data["headers"].append(_template_value(args[0]))
        elif macro == "ZLC_SOURCE":
            if len(args) != 1:
                raise ValueError("ZLC_SOURCE expects 1 arg")
            data["sources"].append(_template_value(args[0]))
        elif macro == "ZLC_INCLUDE_DIR":
            if len(args) != 1:
                raise ValueError("ZLC_INCLUDE_DIR expects 1 arg")
            data["include_dirs"].append(_template_value(args[0]))
        elif macro == "ZLC_LIB_DIR":
            if len(args) != 1:
                raise ValueError("ZLC_LIB_DIR expects 1 arg")
            data["lib_dirs"].append(_template_value(args[0]))
        elif macro == "ZLC_LIB":
            if len(args) != 1:
                raise ValueError("ZLC_LIB expects 1 arg")
            data["libs"].append(_template_value(args[0]))
        elif macro == "ZLC_CFLAG":
            if len(args) != 1:
                raise ValueError("ZLC_CFLAG expects 1 arg")
            data["cflags"].append(_template_value(args[0]))
        elif macro == "ZLC_LDFLAG":
            if len(args) != 1:
                raise ValueError("ZLC_LDFLAG expects 1 arg")
            data["ldflags"].append(_template_value(args[0]))
        elif macro == "ZLC_FN":
            if len(args) < 3:
                raise ValueError("ZLC_FN expects at least 3 args")
            data["functions"].append({
                "zy": _template_value(args[0]),
                "c": _template_value(args[1]),
                "return": _template_value(args[2]),
                "params": [_template_param(arg) for arg in args[3:]],
            })
        elif macro == "ZLC_STRUCT":
            if len(args) < 1:
                raise ValueError("ZLC_STRUCT expects a name and optional ZLC_FIELD entries")
            data["structs"].append({
                "name": _template_value(args[0]),
                "fields": [_template_field(arg) for arg in args[1:]],
            })
        elif macro in {"ZLC_PARAM", "ZLC_FIELD"}:
            continue
        else:
            raise ValueError(f"unknown c_module template macro `{macro}`")
    return data


def read_manifest(path: Path) -> dict:
    if path.suffix.lower() != ".json":
        return read_template_header(path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def _as_list(data: dict, plural: str, singular: str | None = None) -> list[str]:
    value = data.get(plural)
    if value is None and singular:
        value = data.get(singular)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError(f"`{plural}` must be a string or list of strings")


def _module_name(data: dict) -> str:
    module = str(data.get("module", "")).strip()
    if not module:
        raise ValueError("manifest is missing `module`")
    return module


def _zy_ident(value: str) -> str:
    ident = re.sub(r"\W+", "_", value.strip()).strip("_")
    if not ident:
        raise ValueError(f"`{value}` cannot be converted to a ZyenLang identifier")
    if ident[0].isdigit():
        ident = "_" + ident
    return ident


def _module_struct_name(module: str) -> str:
    ident = _zy_ident(module)
    if not ident[0].isupper():
        ident = ident[0].upper() + ident[1:]
    return ident + "_Module"


def hidden_struct_name(manifest_path: Path, module: str | None = None) -> str:
    """Return a stable internal type name for one resolved native manifest."""
    path = manifest_path.resolve()
    module_name = module or _module_name(read_manifest(path))
    ident = _zy_ident(module_name)
    if not ident[0].isupper():
        ident = ident[0].upper() + ident[1:]
    path_key = str(path).replace("\\", "/").lower().encode("utf-8")
    digest = hashlib.sha256(path_key).hexdigest()[:12]
    return f"Zlcm_{ident}_{digest}_Module"


def _normalize_template_type(raw_type: str, struct_names: set[str], *, allow_void: bool) -> str:
    raw = raw_type.strip().replace(" ", "")
    raw = TYPE_ALIASES.get(raw, raw)
    for prefix in ("ZL_ptr<", "ptr<"):
        if raw.startswith(prefix) and raw.endswith(">"):
            inner = raw[len(prefix):-1]
            if not inner:
                break
            return f"ptr<{_normalize_template_type(inner, struct_names, allow_void=False)}>"
    if raw.startswith("fn("):
        depth = 0
        close_index = -1
        for index, ch in enumerate(raw[2:], start=2):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_index = index
                    break
        if close_index < 0 or not raw[close_index + 1:].startswith("->"):
            raise ValueError(f"invalid c_module function type `{raw_type}`; expected fn(...)->T")
        params_body = raw[3:close_index]
        params = _split_macro_args(params_body) if params_body else []
        normalized_params = [
            _normalize_template_type(param, struct_names, allow_void=False) for param in params
        ]
        ret = _normalize_template_type(raw[close_index + 3:], struct_names, allow_void=True)
        return f"fn({','.join(normalized_params)})->{ret}"
    if raw in BASIC_TYPES:
        if raw == "void" and not allow_void:
            raise ValueError("void is not valid in this position")
        return raw
    if raw in struct_names:
        return raw
    supported = "int, float, bool, str, void, ZL_List, ZL_ptr<T>, fn(...)->T, and declared ZLC_STRUCT names"
    raise ValueError(f"unsupported c_module type `{raw_type}`; expected one of {supported}")


def _param_decl(param: dict, struct_names: set[str]) -> str:
    name = str(param.get("name", "")).strip()
    if not name:
        raise ValueError("parameter is missing `name`")
    if _zy_ident(name) != name:
        raise ValueError(f"invalid parameter name `{name}`")
    typ = _normalize_template_type(str(param.get("type", "")), struct_names, allow_void=False)
    return f"{name}: {typ}"


def _param_name(param: dict) -> str:
    name = str(param.get("name", "")).strip()
    if not name:
        raise ValueError("parameter is missing `name`")
    return name


def generate_module_text(
    manifest_path: Path,
    *,
    struct_name: str | None = None,
    include_load: bool = True,
    source_label: str | None = None,
) -> tuple[str, str]:
    data = read_manifest(manifest_path)
    module = _module_name(data)
    resolved_struct_name = struct_name or _module_struct_name(module)
    headers = _as_list(data, "headers", "header")
    sources = _as_list(data, "sources", "source")
    include_dirs = _as_list(data, "include_dirs", "include_dir")
    lib_dirs = _as_list(data, "lib_dirs", "lib_dir")
    libs = _as_list(data, "libs", "lib")
    cflags = _as_list(data, "cflags", "cflag")
    ldflags = _as_list(data, "ldflags", "ldflag")
    structs = data.get("structs", [])
    funcs = data.get("functions", [])
    if not isinstance(structs, list):
        raise ValueError("`structs` must be a list")
    if not isinstance(funcs, list) or not funcs:
        raise ValueError("manifest must contain non-empty `functions` list")

    struct_names: set[str] = set()
    for struct in structs:
        if not isinstance(struct, dict):
            raise ValueError("struct entry must be an object")
        name = str(struct.get("name", "")).strip()
        if not name or _zy_ident(name) != name or not name[0].isupper():
            raise ValueError(f"struct name `{name}` must be a capitalized C/ZyenLang identifier")
        if name in struct_names:
            raise ValueError(f"duplicate ZLC_STRUCT `{name}`")
        struct_names.add(name)

    lines: list[str] = [
        "// Generated in memory by compiler-native c_module.load",
        f"// module: {module}",
    ]
    if source_label is not None:
        lines.append(f"// c_module_type: {resolved_struct_name} = {json.dumps(source_label)}")
    if headers:
        lines.append("// c_headers: " + ", ".join(headers))
    if sources:
        lines.append("// c_sources: " + ", ".join(sources))
    if include_dirs:
        lines.append("// c_include_dirs: " + ", ".join(include_dirs))
    if lib_dirs:
        lines.append("// c_lib_dirs: " + ", ".join(lib_dirs))
    if libs:
        lines.append("// c_libs: " + ", ".join(libs))
    if cflags:
        lines.append("// c_cflags: " + ", ".join(cflags))
    if ldflags:
        lines.append("// c_ldflags: " + ", ".join(ldflags))
    lines.append("")
    for struct in structs:
        struct_name_value = str(struct.get("name", "")).strip()
        fields = struct.get("fields", [])
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"ZLC_STRUCT `{struct_name_value}` must contain at least one ZLC_FIELD")
        lines.append(f"// c_module_external_struct: {struct_name_value}")
        lines.append(f"struct {struct_name_value} {{")
        seen_fields: set[str] = set()
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError(f"field in `{struct_name_value}` must be an object")
            field_name = str(field.get("name", "")).strip()
            if not field_name or _zy_ident(field_name) != field_name:
                raise ValueError(f"invalid field name `{field_name}` in `{struct_name_value}`")
            if field_name in seen_fields:
                raise ValueError(f"duplicate field `{field_name}` in `{struct_name_value}`")
            seen_fields.add(field_name)
            field_type = _normalize_template_type(str(field.get("type", "")), struct_names, allow_void=False)
            if field_type == struct_name_value:
                raise ValueError(
                    f"ZLC_STRUCT `{struct_name_value}` cannot contain itself by value; use ZL_ptr<{struct_name_value}>"
                )
            lines.append(f"    let this.{field_name}: {field_type};")
        lines.append("}")
        lines.append("")
    lines.append(f"struct {resolved_struct_name} {{")
    lines.append("    let this._zlcm_keepalive: int;")
    lines.append("")
    for fn in funcs:
        if not isinstance(fn, dict):
            raise ValueError("function entry must be an object")
        zy_name = str(fn.get("zy", "")).strip()
        c_name = str(fn.get("c", "")).strip()
        ret_type = _normalize_template_type(str(fn.get("return", "")), struct_names, allow_void=True)
        params = fn.get("params", [])
        if not zy_name or not c_name:
            raise ValueError("function entry needs `zy` and `c`")
        if not isinstance(params, list):
            raise ValueError(f"`params` for `{zy_name}` must be a list")
        param_decls = ", ".join(_param_decl(p, struct_names) for p in params)
        arg_list = ", ".join(_param_name(p) for p in params)
        param_types = [
            _normalize_template_type(str(p.get("type", "")), struct_names, allow_void=False)
            for p in params
        ]
        native_signature = f"fn({','.join(param_types)})->{ret_type}"
        lines.append(f"    // c_module_native_fn: {c_name} = {native_signature}")
        lines.append(f"    fn {zy_name}({param_decls}) -> {ret_type} {{")
        lines.append("        set this._zlcm_keepalive = this._zlcm_keepalive;")
        if ret_type == "void":
            lines.append(f"        {c_name}({arg_list});")
        else:
            lines.append(f"        return {c_name}({arg_list});")
        lines.append("    }")
        lines.append("")
    lines.append("}")
    if include_load:
        lines.append("")
        lines.append(f"fn load() -> {resolved_struct_name} {{")
        lines.append(f"    let module: {resolved_struct_name} = {resolved_struct_name};")
        lines.append("    return module;")
        lines.append("}")
        lines.append("")
    return module, "\n".join(lines).rstrip() + "\n"


def write_module(manifest_path: Path, output: Path | None = None) -> Path:
    module, body = generate_module_text(manifest_path)
    out_path = output or manifest_path.with_name(module + ".zy")
    out_path.write_text(body, encoding="utf-8")
    return out_path


def resolve_many(base: Path, values: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    for value in values:
        p = Path(value)
        out.append(p if p.is_absolute() else base / p)
    return out


def _comment_list(line: str, key: str) -> list[str] | None:
    m = re.match(rf"\s*//\s*{re.escape(key)}\s*:\s*(.*)$", line)
    if not m:
        return None
    body = m.group(1).strip()
    if not body:
        return []
    return [item.strip() for item in body.split(",") if item.strip()]


def native_metadata_from_zy(path: Path) -> dict:
    base = path.parent
    out = empty_native_metadata()
    path_keys = {
        "c_headers": "headers",
        "c_sources": "sources",
        "c_include_dirs": "include_dirs",
        "c_lib_dirs": "lib_dirs",
    }
    string_keys = {
        "c_libs": "libs",
        "c_cflags": "cflags",
        "c_ldflags": "ldflags",
    }
    platform = native_platform_name()
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        for comment_key, out_key in path_keys.items():
            values = _comment_list(line, comment_key)
            if values is not None:
                out[out_key].extend(resolve_many(base, values))
            platform_values = _comment_list(line, f"{comment_key}_{platform}")
            if platform_values is not None:
                out[out_key].extend(resolve_many(base, platform_values))
        for comment_key, out_key in string_keys.items():
            values = _comment_list(line, comment_key)
            if values is not None:
                out[out_key].extend(values)
            platform_values = _comment_list(line, f"{comment_key}_{platform}")
            if platform_values is not None:
                out[out_key].extend(platform_values)
    return out


def native_metadata_from_manifest(path: Path) -> dict:
    data = read_manifest(path)
    base = path.parent
    out = empty_native_metadata()
    path_fields = {
        "headers": "header",
        "sources": "source",
        "include_dirs": "include_dir",
        "lib_dirs": "lib_dir",
    }
    for plural, singular in path_fields.items():
        out[plural].extend(resolve_many(base, _as_list(data, plural, singular)))
    for plural, singular in (("libs", "lib"), ("cflags", "cflag"), ("ldflags", "ldflag")):
        out[plural].extend(_as_list(data, plural, singular))
    platform = native_platform_name()
    for plural in path_fields:
        out[plural].extend(resolve_many(base, _as_list(data, f"{plural}_{platform}")))
    for plural in ("libs", "cflags", "ldflags"):
        out[plural].extend(_as_list(data, f"{plural}_{platform}"))
    return finalize_native_metadata(out)


def merge_native_metadata(target: dict, extra: dict) -> None:
    for key in NATIVE_KEYS:
        target[key].extend(extra.get(key, []))


def _dedupe_paths(values: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for value in values:
        key = str(value.resolve()).lower()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _dedupe_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def finalize_native_metadata(meta: dict) -> dict:
    out = empty_native_metadata()
    for key in ("headers", "sources", "include_dirs", "lib_dirs"):
        out[key] = _dedupe_paths(list(meta.get(key, [])))
    for key in ("libs", "cflags", "ldflags"):
        out[key] = _dedupe_strings(list(meta.get(key, [])))
    return out


def c_compiler_command() -> list[str]:
    override = os.environ.get("ZY_CC", "").strip()
    if override:
        return shlex.split(override, posix=not sys.platform.startswith("win"))

    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parents[1])
    executable = "zig.exe" if sys.platform.startswith("win") else "zig"
    for root in roots:
        candidate = root / "toolchain" / executable
        if candidate.is_file():
            return [str(candidate), "cc"]
        candidate = root / "toolchain" / "zig" / executable
        if candidate.is_file():
            return [str(candidate), "cc"]

    for name in ("gcc", "clang", "cc"):
        found = shutil.which(name)
        if found:
            return [found]
    raise FileNotFoundError(
        "no C compiler found; use a portable ZyenLang release, install gcc/clang, "
        "or set ZY_CC"
    )


def gcc_command(meta: dict, main_c: Path, output_exe: Path) -> list[str]:
    cmd: list[str] = c_compiler_command() + ["-std=c11", "-Wall", "-Wextra", "-Wno-unused-function"]
    cmd.extend(meta["cflags"])
    abi_dir = Path(__file__).resolve().parent / "std"
    abi_header = abi_dir / "zyenlang_c_abi.h"
    cmd.extend(["-I", str(abi_dir), "-include", str(abi_header)])
    for include_dir in meta["include_dirs"]:
        cmd.extend(["-I", str(include_dir)])
    for header in meta["headers"]:
        cmd.extend(["-include", str(header)])
    cmd.append(str(main_c))
    cmd.extend(str(source) for source in meta["sources"])
    cmd.extend(["-o", str(output_exe)])
    for lib_dir in meta["lib_dirs"]:
        cmd.extend(["-L", str(lib_dir)])
    cmd.extend(f"-l{lib}" for lib in meta["libs"])
    cmd.extend(meta["ldflags"])
    return cmd
