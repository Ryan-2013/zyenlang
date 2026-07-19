from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set

from . import c_module as native_c_module


class ZyenError(Exception):
    pass


@dataclass
class StructDef:
    name: str
    fields: Dict[str, str] = field(default_factory=dict)
    defaults: Dict[str, str] = field(default_factory=dict)
    owned_pointer_fields: Set[str] = field(default_factory=set)
    methods: Dict[str, "FunctionDef"] = field(default_factory=dict)


@dataclass
class FunctionDef:
    name: str
    ret_type: str
    params: Dict[str, str] = field(default_factory=dict)
    defaults: Dict[str, str] = field(default_factory=dict)
    defined: bool = False
    declared_line: int = 0
    defined_line: int = 0


@dataclass
class TranspileContext:
    structs: Dict[str, StructDef] = field(default_factory=dict)
    functions: Dict[str, FunctionDef] = field(default_factory=dict)
    symbols: Dict[str, str] = field(default_factory=dict)
    consts: set[str] = field(default_factory=set)
    ptr_targets: Dict[str, str] = field(default_factory=dict)
    list_refs: Set[str] = field(default_factory=set)
    # A named local List can carry a hidden set of possible element types.
    # None means the set was erased (for example at a plain List parameter).
    list_item_types: Dict[str, Optional[Set[str]]] = field(default_factory=dict)
    scope_stack: List[Set[str]] = field(default_factory=list)
    # Managed locals owned by each lexical scope. Function parameters and
    # lifted capture snapshots are borrowed and are intentionally absent.
    owned_scope_stack: List[List[Tuple[str, str]]] = field(default_factory=list)
    current_function: Optional[str] = None
    current_return_type: str = "void"
    loop_depth: int = 0
    auto_ptr_counter: int = 0
    managed_temp_counter: int = 0
    # ZEP-0010: fn-typed values map normalized signature → C typedef name
    fn_typedefs: Dict[str, str] = field(default_factory=dict)
    # ZEP-0013: lambda-lifted nested fns, keyed by inner name within each outer fn.
    # Each value is a dict with keys: outer, inner, lifted_name, env_struct,
    # captures (list of (name, ztype)), params (Dict), ret_type (str),
    # body_lines (List[(line_no, line)]).
    lifted_fns: List[dict] = field(default_factory=list)
    lifted_fn_index: Dict[Tuple[str, str], int] = field(default_factory=dict)
    c_module_types: Dict[str, str] = field(default_factory=dict)
    external_structs: Set[str] = field(default_factory=set)
    native_function_types: Dict[str, str] = field(default_factory=dict)


BUILTIN_TYPES = {"int", "float", "bool", "str", "ptr", "void", "List"}
INTERNAL_TYPES = {"Any"}
INT_MIN = -2147483648
INT_MAX = 2147483647

# Legacy type-position detector used by a few fallback diagnostics. Actual
# declarations use parse_type_prefix(), which handles recursive ptr/fn types.
_TYPE_FN_RX = r"fn\s*\((?:[^()]|\([^()]*\))*\)\s*(?:->\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?(?:\s*<\s*[^=;]+\s*>)?)?"
_TYPE_SIMPLE_RX = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?(?:\s*<\s*[^=;]+\s*>)?"
TYPE_RX = f"(?:{_TYPE_FN_RX}|{_TYPE_SIMPLE_RX})"

#int 類型 分詞器
def is_int_literal(expr: str) -> bool:
    return re.match(r"^-?\d+$", expr.strip()) is not None


#int 類型 分詞器
def parse_int_literal(expr: str) -> int:
    return int(expr.strip())

#檢查int大小
def check_int_literal_range(expr: str, line_no: int, target_type: str = "int") -> None:
    """Reject integer literals that cannot fit ZyenLang's current int."""
    if target_type != "int":
        return
    if not is_int_literal(expr):
        return
    value = parse_int_literal(expr)
    if value < INT_MIN or value > INT_MAX:
        raise ZyenError(
            f"line {line_no}: integer literal out of range for int: {expr.strip()} "
            f"(allowed {INT_MIN}..{INT_MAX}); use str for huge numbers for now"
        )

#檢查int大小
def assert_expr_literals_fit(expr: str, ctx: "TranspileContext", line_no: int, expected_type: str = "") -> None:
    """Small static guard against obvious int overflow in declarations/returns/sets."""
    raw = expr.strip()
    if is_int_literal(raw):
        typ = expected_type or infer_type(raw, ctx)
        if ztype_base(typ) == "int":
            check_int_literal_range(raw, line_no, "int")
    if is_array_literal(raw):
        body = raw[1:-1].strip()
        if body:
            for item in split_args(body):
                if is_int_literal(item):
                    check_int_literal_range(item, line_no, "int")

#去除注釋
def strip_comment(line: str) -> str:
    in_str = False
    escaped = False
    for i in range(len(line) - 1):
        ch = line[i]
        if ch == '"' and not escaped:
            in_str = not in_str
        if not in_str and line[i : i + 2] == "//":
            return line[:i]
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    return line

#跨行合併：如果一個語句寫在多行（如長函數定義或多行括號），它會將這些行合併成一條邏輯語句
#}esle{     這漾句子的處理
def clean_lines(source: str) -> List[Tuple[int, str]]:
    """Convert physical source lines into parser-ready logical lines.

    Older ZyenLang versions were almost fully line-oriented and only joined
    multi-line list literals.  v0.1.47 upgrades this stage into a small
    tokenizer-aware statement assembler.  It keeps strings/comments safe and
    allows common formatted code such as:

        fn add(
            a: int,
            b: int
        ) -> int {
            return add(
                a,
                b
            );
        }

    It is still intentionally simple: it does not split two statements written
    on one physical line.  Zyen style remains one statement per logical line.
    """

    def scan_state(text: str, state: Tuple[int, int, int, bool, bool]) -> Tuple[int, int, int, bool, bool]:
        paren, bracket, brace, in_str, escaped = state
        for ch in text:
            if ch == '"' and not escaped:
                in_str = not in_str
            elif not in_str:
                if ch == '(':
                    paren += 1
                elif ch == ')':
                    paren -= 1
                elif ch == '[':
                    bracket += 1
                elif ch == ']':
                    bracket -= 1
                elif ch == '{':
                    brace += 1
                elif ch == '}':
                    brace -= 1
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
        return paren, bracket, brace, in_str, escaped

    def normalize_space(text: str) -> str:
        # Do not collapse whitespace globally: string literals may intentionally
        # contain repeated spaces.  Physical lines are already stripped and
        # joined with a single separator, which is enough for parser regexes.
        return text.strip()

    def is_block_header(text: str) -> bool:
        t = text.strip()
        if not t.endswith("{"):
            return False
        if re.match(r"^(fn|struct|if|for)\b", t):
            return True
        if re.match(r"^}\s*else(\s+if\b.*)?\s*\{\s*$", t):
            return True
        if re.match(r"^else(\s+if\b.*)?\s*\{\s*$", t):
            return True
        return False

    def logical_complete(text: str, state: Tuple[int, int, int, bool, bool]) -> bool:
        paren, bracket, brace, in_str, _escaped = state
        if in_str:
            return False
        t = text.strip()
        if not t:
            return True
        if t == "}":
            return True
        if is_block_header(t):
            return True
        if t.endswith(";") and paren == 0 and bracket == 0 and brace == 0:
            return True
        return False

    result: List[Tuple[int, str]] = []
    pending: List[str] = []
    pending_start = 0
    state = (0, 0, 0, False, False)  # paren, bracket, brace, in_str, escaped

    for no, raw in enumerate(source.splitlines(), start=1):
        line = strip_comment(raw).strip()
        if not line:
            continue

        if not pending:
            # Closing block lines are structural tokens, not expression braces.
            if line == "}" or re.match(r"^}\s*else", line):
                result.append((no, normalize_space(line)))
                continue
            # Accept `else {` / `else if (...) {` even when users format the
            # closing brace on the previous line.  The emitter expects
            # `} else {` as one logical line, so normalize it here.
            if result and line.startswith("else") and result[-1][1] == "}":
                prev_no, _prev = result.pop()
                combined = normalize_space("} " + line)
                result.append((prev_no, combined))
                continue
            pending_start = no

        pending.append(line)
        joined = normalize_space(" ".join(pending))
        state = scan_state(line, state)

        if min(state[0], state[1], state[2]) < 0:
            raise ZyenError(f"line {no}: unmatched closing delimiter")

        if logical_complete(joined, state):
            # Block headers intentionally leave one `{` unmatched because it is
            # consumed later by the structured emitter.  Reset state at logical
            # boundaries so the following body statements are parsed normally.
            result.append((pending_start, joined))
            pending = []
            pending_start = 0
            state = (0, 0, 0, False, False)

    if pending:
        joined = normalize_space(" ".join(pending))
        if state[3]:
            raise ZyenError(f"line {pending_start}: unterminated string literal")
        raise ZyenError(f"line {pending_start}: incomplete statement or header: {joined}")
    return result

def split_args(text: str) -> List[str]:
    args: List[str] = []
    cur: List[str] = []
    depth = 0
    in_str = False
    escaped = False
    # NOTE: `<` and `>` intentionally do NOT count toward depth. They appear as
    # comparison operators (`a < b`) and inside the `->` of fn types
    # (`fn(int)->int`); treating them as brackets would break both cases.
    # Multi-arg generics (`Dict<int,str>`) are not in v0.1.49, so this is safe.
    for ch in text:
        if ch == '"' and not escaped:
            in_str = not in_str
        elif not in_str:
            if ch in "({[":
                depth += 1
            elif ch in ")}]":
                depth -= 1
            elif ch == "," and depth == 0:
                item = "".join(cur).strip()
                if item:
                    args.append(item)
                cur = []
                continue
        cur.append(ch)
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    item = "".join(cur).strip()
    if item:
        args.append(item)
    return args


def parse_type_prefix(text: str, start: int = 0) -> Tuple[str, int]:
    """Parse one recursive Zyen type and return (canonical_type, end_index)."""
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    if text.startswith("fn", i) and (i + 2 == len(text) or not (text[i + 2].isalnum() or text[i + 2] == "_")):
        i += 2
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text) or text[i] != "(":
            raise ZyenError("expected `(` after `fn` in function type")
        i += 1
        params: List[str] = []
        while True:
            while i < len(text) and text[i].isspace():
                i += 1
            if i < len(text) and text[i] == ")":
                i += 1
                break
            param, i = parse_type_prefix(text, i)
            params.append(param)
            while i < len(text) and text[i].isspace():
                i += 1
            if i < len(text) and text[i] == ",":
                i += 1
                continue
            if i < len(text) and text[i] == ")":
                i += 1
                break
            raise ZyenError("expected `,` or `)` in function type")
        while i < len(text) and text[i].isspace():
            i += 1
        if not text.startswith("->", i):
            raise ZyenError("function type needs `-> return_type`")
        ret_type, i = parse_type_prefix(text, i + 2)
        return f"fn({','.join(params)})->{ret_type}", i

    ident = re.match(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?", text[i:])
    if not ident:
        raise ZyenError(f"expected a type near `{text[i:].strip()}`")
    base = ident.group(0)
    i += len(base)
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "<":
        inner, i = parse_type_prefix(text, i + 1)
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text) or text[i] != ">":
            raise ZyenError(f"unclosed generic type `{base}<...>`")
        i += 1
        return f"{base}<{inner}>", i
    return base, i


def parse_variable_declaration_syntax(line: str, *, trailing_semicolon: bool, owned_pointer: bool = False) -> Optional[Tuple[str, str, Optional[str], Optional[str]]]:
    source = line.strip()
    if trailing_semicolon:
        if not source.endswith(";"):
            return None
        source = source[:-1].rstrip()
    star = r"\*\s*" if owned_pointer else ""
    match = re.match(rf"(let|const)\s+{star}([A-Za-z_]\w*)\b", source)
    if not match:
        return None
    kind, name = match.group(1), match.group(2)
    rest = source[match.end():].strip()
    explicit_type: Optional[str] = None
    if rest.startswith(":"):
        explicit_type, end = parse_type_prefix(rest, 1)
        rest = rest[end:].strip()
    if not rest:
        return kind, name, explicit_type, None
    if not rest.startswith("="):
        return None
    expr = rest[1:].strip()
    if not expr:
        return None
    return kind, name, explicit_type, expr


def parse_struct_field_line(line: str) -> Optional[Tuple[str, str, Optional[str], bool]]:
    match = re.match(r"let\s+(\*\s*)?this\.([A-Za-z_]\w*)\s*:\s*", line)
    if not match or not line.rstrip().endswith(";"):
        return None
    body = line[match.end():].rstrip()
    body = body[:-1].rstrip()
    field_type, end = parse_type_prefix(body)
    rest = body[end:].strip()
    owned_pointer = match.group(1) is not None
    field_name = match.group(2)
    if not rest:
        return field_name, field_type, None, owned_pointer
    if rest.startswith("=") and rest[1:].strip():
        return field_name, field_type, rest[1:].strip(), owned_pointer
    raise ZyenError(f"invalid struct field declaration `{line}`")


def _struct_default_init(struct_name: str, ctx: "TranspileContext") -> str:
    """Emit a C99 designated initializer that applies declared field defaults.

    For a struct with no field defaults, returns `(StructName){0}` — same as
    the pre-ZEP-0006 behaviour. For a struct with one or more defaults, emits
    `(StructName){ .a = 1, .b = "x" }`; C99 zero-fills the rest.
    """
    struct = ctx.structs.get(struct_name)
    if struct is None or not struct.defaults:
        return f"({struct_name}){{0}}"
    parts = []
    for field_name, default_expr in struct.defaults.items():
        field_type = struct.fields.get(field_name, "")
        if field_name in struct.owned_pointer_fields:
            value = owned_pointer_initializer_expr(
                field_type, default_expr, f"{struct_name}.{field_name}", ctx
            )
            parts.append(f".{field_name} = {value}")
            continue
        coerced = coerce_named_fn_to_fnval(default_expr, field_type, ctx)
        value = coerced if coerced is not None else transform_expr(default_expr, ctx)
        if type_has_managed_value(field_type, ctx) and not expression_produces_owned_value(default_expr, ctx):
            value = retain_expr_for_type(value, field_type, ctx)
        parts.append(f".{field_name} = {value}")
    return f"({struct_name}){{ " + ", ".join(parts) + " }"


def _strip_module_prefix_type(ztype: str) -> str:
    """Strip `mod.` qualifiers from a type expression.

    Imported user structs live in the global namespace, so `test.Car` is the
    same type as `Car`. We strip the prefix here so the rest of the pipeline
    only ever sees the unqualified name.
    """
    if not ztype:
        return ztype
    ztype = ztype.replace(" ", "")
    if ztype.startswith("fn("):
        params, ret_type = parse_fn_type(ztype)
        stripped_params = [_strip_module_prefix_type(param) for param in params]
        stripped_ret = _strip_module_prefix_type(ret_type)
        return f"fn({','.join(stripped_params)})->{stripped_ret}"
    m = re.match(r"^([A-Za-z_]\w*)<(.+)>$", ztype)
    if m:
        inner = _strip_module_prefix_type(m.group(2))
        return f"{m.group(1)}<{inner}>"
    if "." in ztype:
        return ztype.split(".", 1)[1]
    return ztype


def ztype_base(ztype: str) -> str:
    ztype = ztype.strip().replace(" ", "")
    if struct_union_types(ztype) is not None:
        return "Any"
    if _generic_inner_type(ztype, "ptr") is not None:
        return "ptr"
    return ztype


_STRUCT_UNION_PREFIX = "__zl_struct_union__<"


def make_struct_union_type(types: Set[str]) -> str:
    return _STRUCT_UNION_PREFIX + "|".join(sorted(types)) + ">"


def struct_union_types(ztype: str) -> Optional[Set[str]]:
    normalized = ztype.strip().replace(" ", "")
    if not normalized.startswith(_STRUCT_UNION_PREFIX) or not normalized.endswith(">"):
        return None
    body = normalized[len(_STRUCT_UNION_PREFIX):-1]
    return set(body.split("|")) if body else set()


def ptr_inner_type(ztype: str) -> Optional[str]:
    return _generic_inner_type(ztype.strip().replace(" ", ""), "ptr")


def _generic_inner_type(ztype: str, outer: str) -> Optional[str]:
    prefix = outer + "<"
    if not ztype.startswith(prefix) or not ztype.endswith(">"):
        return None
    depth = 0
    for index, ch in enumerate(ztype[len(outer):], start=len(outer)):
        if ch == "<":
            depth += 1
        elif ch == ">" and (index == 0 or ztype[index - 1] != "-"):
            depth -= 1
            if depth == 0 and index != len(ztype) - 1:
                return None
            if depth < 0:
                return None
    if depth != 0:
        return None
    inner = ztype[len(prefix):-1]
    return inner or None


def is_fn_type(ztype: str) -> bool:
    """ZEP-0010: detect `fn(...)->T` type expressions."""
    s = ztype.strip().replace(" ", "")
    return s.startswith("fn(")


def parse_fn_type(ztype: str) -> Tuple[List[str], str]:
    """ZEP-0010: parse `fn(int,int)->int` into ([param types], ret type)."""
    s = ztype.strip().replace(" ", "")
    if not s.startswith("fn("):
        raise ZyenError(f"not a fn type: `{ztype}`")
    open_i = 2
    depth = 0
    close_i = -1
    for i in range(open_i, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                close_i = i
                break
    if close_i < 0:
        raise ZyenError(f"unclosed `fn(` in `{ztype}`")
    params_str = s[open_i + 1:close_i]
    rest = s[close_i + 1:]
    if rest == "" or rest == "->void":
        ret_type = "void"
    elif rest.startswith("->"):
        ret_type = rest[2:]
        if not ret_type:
            raise ZyenError(f"missing return type after `->` in `{ztype}`")
    else:
        raise ZyenError(f"expected `->T` after `fn(...)`, got `{rest}` in `{ztype}`")
    param_types = split_args(params_str) if params_str else []
    for p in param_types:
        if ":" in p:
            raise ZyenError(
                f"fn type parameter cannot have a name; write `fn(int,int)->int` not `fn(a:int,b:int)->int`, got `{p}` in `{ztype}`"
            )
    return param_types, ret_type


def fn_typedef_name(ztype: str) -> str:
    """Stable C identifier for a fn type. `fn(int,int)->int` -> `ZL_fn_int_int_to_int`."""
    param_types, ret_type = parse_fn_type(ztype)

    def slug(x: str) -> str:
        return (
            x.replace(" ", "")
            .replace("->", "_to_")  # MUST come before single '>' replacement
            .replace("<", "_of_")
            .replace(">", "")
            .replace(",", "_")
            .replace("(", "_lp_")
            .replace(")", "_rp_")
        )

    p = "_".join(slug(x) for x in param_types) if param_types else "void"
    return f"ZL_fn_{p}_to_{slug(ret_type)}"


def fn_call_helper_name(ztype: str) -> str:
    return "zl_fn_call_" + fn_typedef_name(ztype)[len("ZL_fn_"):]


def fn_owned_call_helper_name(ztype: str) -> str:
    return fn_call_helper_name(ztype) + "_owned"


def fn_call_variant_name(ztype: str, owned_args_mask: int, owns_callee: bool) -> str:
    name = fn_call_helper_name(ztype)
    if owned_args_mask:
        name += f"_args_{owned_args_mask}"
    if owns_callee:
        name += "_owned"
    return name


def owned_argument_mask(args: List[str], param_types: List[str], ctx: "TranspileContext") -> int:
    mask = 0
    for index, (arg, param_type) in enumerate(zip(args, param_types)):
        if type_has_managed_value(param_type, ctx) and expression_produces_owned_value(arg, ctx):
            mask |= 1 << index
    return mask


def direct_owned_args_wrapper_name(target: str, mask: int) -> str:
    return f"{target}_zl_owned_args_{mask}"


def fn_call_pointer_name(ztype: str) -> str:
    return "ZL_call_" + fn_typedef_name(ztype)[len("ZL_fn_"):]


def register_fn_typedef(ctx: "TranspileContext", ztype: str) -> str:
    """Record a fn type so a typedef is emitted in the C prelude. Returns its C name."""
    name = fn_typedef_name(ztype)
    if name not in ctx.fn_typedefs:
        ctx.fn_typedefs[name] = ztype.strip().replace(" ", "")
        param_types, ret_type = parse_fn_type(ztype)
        for pt in param_types:
            register_fn_types_in_type(ctx, pt)
        register_fn_types_in_type(ctx, ret_type)
    return name


def register_fn_types_in_type(ctx: "TranspileContext", ztype: str) -> None:
    """Register fn signatures nested in fn returns, params, or ptr cells."""
    normalized = ztype.strip().replace(" ", "")
    if is_fn_type(normalized):
        register_fn_typedef(ctx, normalized)
        return
    inner = ptr_inner_type(normalized)
    if inner is not None:
        register_fn_types_in_type(ctx, inner)


def validate_user_type(ztype: str, line_no: int, context: str = "type") -> None:
    """Reject user-facing use of Any and bare ptr where a concrete target is required."""
    t = ztype.strip().replace(" ", "")
    if is_fn_type(t):
        try:
            param_types, ret_type = parse_fn_type(t)
        except ZyenError as e:
            raise ZyenError(f"line {line_no}: invalid fn type `{ztype}`: {e}")
        for pt in param_types:
            validate_user_type(pt, line_no, "fn type parameter")
        if ret_type != "void":
            validate_user_type(ret_type, line_no, "fn return type")
        return
    pointer_inner = ptr_inner_type(t)
    if pointer_inner is not None:
        validate_user_type(pointer_inner, line_no, "pointer target")
        return
    if t == "Any" or ptr_inner_type(t) == "Any":
        raise ZyenError(f"line {line_no}: `Any` is internal to List; use a concrete type such as int/float/bool/str/ptr<T>/List")


def ensure_assignable(expected_type: str, actual_type: str, line_no: int, what: str = "assignment") -> None:
    expected = expected_type.replace(" ", "")
    actual = actual_type.replace(" ", "")
    eb = ztype_base(expected)
    ab = ztype_base(actual)
    location = f"line {line_no}: " if line_no > 0 else ""
    if eb == "Any":
        return  # internal List cell storage only
    if ab == "Any":
        raise ZyenError(f"{location}List values are dynamic; cast explicitly before {what}, e.g. `(int)value` or `(str)value`")
    if is_fn_type(expected) and actual == "none":
        return
    if eb == "ptr" and ab == "ptr":
        expected_inner = ptr_inner_type(expected)
        actual_inner = ptr_inner_type(actual)
        if expected_inner in {None, "void"} or actual_inner is None or expected_inner == actual_inner:
            return
        if is_fn_type(expected_inner) and is_fn_type(actual_inner):
            raise ZyenError(
                f"{location}function pointer signature mismatch in {what}: expected `{expected_type}`, got `{actual_type}`; "
                "function pointer signatures must match exactly and cannot be cast between signatures"
            )
        raise ZyenError(
            f"{location}pointer type mismatch in {what}: expected `{expected_type}`, got `{actual_type}`; "
            f"use an explicit `({expected_type})value` cast"
        )
    if eb == ab:
        return
    if eb == "float" and ab == "int":
        return
    if eb == "ptr" and ab == "none":
        return
    raise ZyenError(f"{location}type mismatch in {what}: expected `{expected_type}`, got `{actual_type}`")


def display_ztype(ztype: str, ctx: "TranspileContext") -> str:
    label = ctx.c_module_types.get(ztype.replace(" ", ""))
    if label is not None:
        return f'c_module.Module({json.dumps(label)})'
    return ztype


def ensure_c_module_assignable(
    expected_type: str,
    actual_type: str,
    ctx: "TranspileContext",
    line_no: int,
    what: str,
) -> None:
    expected = expected_type.replace(" ", "")
    actual = actual_type.replace(" ", "")
    if expected not in ctx.c_module_types and actual not in ctx.c_module_types:
        return
    if expected != actual:
        location = f"line {line_no}: " if line_no > 0 else ""
        raise ZyenError(
            f"{location}type mismatch in {what}: expected `{display_ztype(expected, ctx)}`, "
            f"got `{display_ztype(actual, ctx)}`"
        )


def c_type(ztype: str) -> str:
    ztype = ztype.strip()
    if is_fn_type(ztype):
        return "ZL_Function"
    pm = re.match(r"ptrstruct\s*<\s*([A-Za-z_]\w*)\s*>", ztype)
    if pm:
        return f"{pm.group(1)}*"
    base = ztype_base(ztype)
    mapping = {
        "int": "int",
        "float": "double",
        "bool": "bool",
        "str": "const char*",
        "ptr": "ptr",
        "void": "void",
        "Any": "Any",
        "List": "ZL_List",
    }
    return mapping.get(base, base)


def type_name_for_runtime(ztype: str) -> str:
    normalized = ztype.strip().replace(" ", "")
    return normalized if ztype_base(normalized) == "ptr" else ztype_base(normalized)


def is_none_literal(expr: str) -> bool:
    return expr.strip() == "None"


def parse_params(text: str, line_no: int) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Parse function parameters.

    v0.1.49 supports default parameter values:
        fn add(a: int, b: int = 1) -> int { ... }

    Return (params, defaults).  `params` preserves declaration order.
    Defaulted parameters must be trailing so call completion remains simple and
    C codegen can fill missing arguments from left to right.
    """
    params: Dict[str, str] = {}
    defaults: Dict[str, str] = {}
    text = text.strip()
    if not text:
        return params, defaults
    seen_default = False
    for part in split_args(text):
        part = part.strip()
        if not part:
            continue
        default_expr = ""
        # Split on a top-level '=' only.  Default expressions may contain nested
        # calls or strings, but currently should not contain assignment syntax.
        depth = 0
        in_str = False
        escaped = False
        eq_i = -1
        for idx, ch in enumerate(part):
            if ch == '"' and not escaped:
                in_str = not in_str
            elif not in_str:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "=" and depth == 0:
                    eq_i = idx
                    break
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
        if eq_i >= 0:
            default_expr = part[eq_i + 1 :].strip()
            part = part[:eq_i].strip()
            if not default_expr:
                raise ZyenError(f"line {line_no}: default parameter value is empty")
            seen_default = True
        elif seen_default:
            raise ZyenError(f"line {line_no}: non-default parameter cannot follow a default parameter")

        if ":" in part:
            name, typ = part.split(":", 1)
            name = name.strip()
            typ = typ.strip().replace(" ", "")
        else:
            # v0.1 default for untyped parameters in the C backend.
            name = part.strip()
            typ = "int"
        if not re.match(r"^[A-Za-z_]\w*$", name):
            raise ZyenError(f"line {line_no}: invalid parameter name: {name}")
        if name in params:
            raise ZyenError(f"line {line_no}: duplicate parameter name: {name}")
        validate_user_type(typ, line_no, "parameter")
        params[name] = typ
        if default_expr:
            defaults[name] = default_expr
    return params, defaults


def parse_fn_header_line(line: str) -> Optional[Tuple[str, str, str, str]]:
    """Parse a `fn NAME(...) -> T {` or `fn NAME(...) -> T;` header.

    Returns (name, params_text, ret_type, kind) where kind is 'defn' or 'decl'.
    Manual scan instead of regex because params or return type may contain
    parentheses (`fn(int,int)->int`).
    """
    m = re.match(r"fn\s+([A-Za-z_]\w*)\s*\(", line)
    if not m:
        return None
    name = m.group(1)
    open_i = m.end() - 1
    close_i = find_matching_paren(line, open_i)
    if close_i < 0:
        return None
    params_text = line[open_i + 1:close_i]
    rest = line[close_i + 1:].strip()
    if rest.startswith("->"):
        body = rest[2:].strip()
        if body.endswith("{"):
            ret_type = body[:-1].strip()
            kind = "defn"
        elif body.endswith(";"):
            ret_type = body[:-1].strip()
            kind = "decl"
        else:
            return None
    elif rest == "{":
        ret_type, kind = "void", "defn"
    elif rest == ";":
        ret_type, kind = "void", "decl"
    else:
        return None
    return name, params_text, ret_type.replace(" ", ""), kind


def params_compatible(a: FunctionDef, b: FunctionDef) -> bool:
    return a.ret_type == b.ret_type and list(a.params.items()) == list(b.params.items())


def merge_function_signature(ctx: TranspileContext, fn: FunctionDef, line_no: int, is_definition: bool) -> None:
    """Register a function declaration or definition.

    A declaration looks like:
        fn add(a: int, b: int = 1) -> int;

    A definition looks like:
        fn add(a: int, b: int = 1) -> int { ... }

    Declarations may appear before definitions.  The definition may omit default
    values; defaults from the declaration are then reused.  If both sides provide
    defaults for the same parameter, they must match exactly.
    """
    old = ctx.functions.get(fn.name)
    if old is None:
        ctx.functions[fn.name] = fn
        return
    if not params_compatible(old, fn):
        raise ZyenError(f"line {line_no}: function `{fn.name}` declaration/definition signature does not match earlier declaration")
    if is_definition and old.defined:
        raise ZyenError(f"line {line_no}: function `{fn.name}` is already defined at line {old.defined_line}")
    merged_defaults = dict(old.defaults)
    for pname, default in fn.defaults.items():
        if pname in merged_defaults and merged_defaults[pname] != default:
            raise ZyenError(f"line {line_no}: default value for parameter `{pname}` of `{fn.name}` does not match earlier declaration")
        merged_defaults[pname] = default
    old.defaults = merged_defaults
    if is_definition:
        old.defined = True
        old.defined_line = line_no
    else:
        old.declared_line = old.declared_line or line_no

def skip_block(lines: List[Tuple[int, str]], start_i: int) -> int:
    """Return the index just after the block that starts at start_i."""
    depth = 0
    i = start_i
    while i < len(lines):
        line = lines[i][1]
        depth += line.count("{")
        depth -= line.count("}")
        i += 1
        if depth <= 0:
            return i
    return i


def collect_signatures(lines: List[Tuple[int, str]]) -> TranspileContext:
    ctx = TranspileContext()
    i = 0
    while i < len(lines):
        line_no, line = lines[i]
        sm = re.match(r"struct\s+([A-Za-z_]\w*)\s*\{\s*$", line)
        if sm:
            struct_name = sm.group(1)
            if struct_name in BUILTIN_TYPES:
                raise ZyenError(f"line {line_no}: `{struct_name}` is a built-in type name and cannot be used as a struct name")
            fields: Dict[str, str] = {}
            struct_defaults: Dict[str, str] = {}
            owned_pointer_fields: Set[str] = set()
            methods: Dict[str, FunctionDef] = {}
            i += 1
            while i < len(lines):
                f_no, f_line = lines[i]
                if f_line == "}":
                    break
                # Struct fields are part of the current object layout. v0.1.22
                # required `let this.name: type;`; v0.1.49 also accepts an
                # optional trailing default expression:
                #     let this.name: type;
                #     let this.name: type = expr;   (ZEP-0006)
                parsed_field = parse_struct_field_line(f_line)
                if parsed_field:
                    field_name, field_type, default_expr, owned_pointer = parsed_field
                    field_type = _strip_module_prefix_type(field_type)
                    validate_user_type(field_type, f_no, "struct field")
                    if owned_pointer:
                        if ztype_base(field_type) != "ptr" or ptr_inner_type(field_type) is None:
                            raise ZyenError(
                                f"line {f_no}: owned struct field `let *this.{field_name}` "
                                "must use a concrete `ptr<T>` type"
                            )
                        if default_expr is None:
                            raise ZyenError(
                                f"line {f_no}: owned struct field `let *this.{field_name}` needs "
                                "a pointee initializer, for example `= 0;`"
                            )
                        owned_pointer_fields.add(field_name)
                    fields[field_name] = field_type
                    if default_expr is not None:
                        struct_defaults[field_name] = default_expr.strip()
                    i += 1
                    continue
                if re.match(r"(?:let\s+)?[A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?\s*(?:=\s*.+?)?\s*;\s*$", f_line):
                    raise ZyenError(f"line {f_no}: struct field must use `let this.name: type;` or `let this.name: type = default;`, for example `let this.list_len: int;`")
                parsed_method = parse_fn_header_line(f_line)
                if parsed_method and parsed_method[3] == "defn":
                    method_name, params_text, ret_type, _kind = parsed_method
                    params, defaults = parse_params(params_text, f_no)
                    validate_user_type(ret_type, f_no, "return type")
                    methods[method_name] = FunctionDef(name=method_name, ret_type=ret_type, params=params, defaults=defaults, defined=True, defined_line=f_no)
                    c_params = {"this": f"ptrstruct<{struct_name}>"}
                    c_params.update(params)
                    c_defaults = dict(defaults)
                    c_name = f"{struct_name}_{method_name}"
                    merge_function_signature(ctx, FunctionDef(name=c_name, ret_type=ret_type, params=c_params, defaults=c_defaults, defined=True, defined_line=f_no), f_no, True)
                    i = skip_block(lines, i)
                    continue
                raise ZyenError(f"line {f_no}: invalid struct member; expected `let this.name: type;` or `fn method(...) -> type {{`")
            else:
                raise ZyenError(f"line {line_no}: struct `{struct_name}` is missing closing `}}`")
            ctx.structs[struct_name] = StructDef(
                name=struct_name,
                fields=fields,
                defaults=struct_defaults,
                owned_pointer_fields=owned_pointer_fields,
                methods=methods,
            )
            i += 1
            continue

        # Explicit top-level function declaration / prototype, or definition header.
        # Both `fn add(a: int) -> int;` and `fn add(a: int) -> int {` go through
        # parse_fn_header_line so the return type can itself be `fn(...)->T`.
        parsed = parse_fn_header_line(line)
        if parsed:
            name, params_text, ret_type, kind = parsed
            params, defaults = parse_params(params_text, line_no)
            validate_user_type(ret_type, line_no, "return type")
            merge_function_signature(
                ctx,
                FunctionDef(
                    name=name,
                    ret_type=ret_type,
                    params=params,
                    defaults=defaults,
                    defined=(kind == "defn"),
                    declared_line=(line_no if kind == "decl" else 0),
                    defined_line=(line_no if kind == "defn" else 0),
                ),
                line_no,
                kind == "defn",
            )
        i += 1

    for name, fn in ctx.functions.items():
        if not fn.defined:
            raise ZyenError(f"line {fn.declared_line}: function `{name}` was declared but not defined")
    return ctx

def convert_struct_literal(expr: str, ctx: TranspileContext) -> str:
    # v0.1 supports compact one-line struct literals:
    # Point { x: 3.0, y: 4.0 } -> (Point){ .x = 3.0, .y = 4.0 }
    # Also accepts qualified `mod.Point { ... }` — imported structs live in the
    # global namespace, so we strip the alias before lookup.
    pattern = re.compile(r"\b(?:[A-Za-z_]\w*\.)?([A-Z][A-Za-z_]\w*)\s*\{([^{}]*)\}")

    def repl(m: re.Match[str]) -> str:
        typ = m.group(1)
        if typ not in ctx.structs:
            return m.group(0)
        body = m.group(2).strip()
        struct = ctx.structs[typ]
        parts: List[str] = []
        provided: set[str] = set()
        if body:
            for item in split_args(body):
                if ":" not in item:
                    raise ZyenError(f"invalid struct literal item `{item}`, expected `field: value`")
                field_name, value = item.split(":", 1)
                field_name = field_name.strip()
                provided.add(field_name)
                raw_val = value.strip()
                field_type = struct.fields.get(field_name, "")
                if field_name in struct.owned_pointer_fields:
                    value_c = owned_pointer_initializer_expr(
                        field_type, raw_val, f"{typ}.{field_name}", ctx
                    )
                    parts.append(f".{field_name} = {value_c}")
                    continue
                if field_type:
                    ensure_c_module_assignable(
                        field_type,
                        infer_type(raw_val, ctx),
                        ctx,
                        0,
                        f"struct field `{typ}.{field_name}`",
                    )
                # ZEP-0013: fn-typed struct field accepts bare named-fn → fat value.
                coerced = coerce_named_fn_to_fnval(raw_val, field_type, ctx)
                value_c = coerced if coerced is not None else transform_expr(raw_val, ctx)
                if field_type and type_has_managed_value(field_type, ctx) and not expression_produces_owned_value(raw_val, ctx):
                    value_c = retain_expr_for_type(value_c, field_type, ctx)
                parts.append(f".{field_name} = {value_c}")
        # ZEP-0006: fill in declared defaults for any field the literal didn't
        # provide. C99 still zero-fills the rest, so fields with no default
        # keep their previous behaviour.
        for field_name, default_expr in struct.defaults.items():
            if field_name not in provided:
                field_type = struct.fields.get(field_name, "")
                if field_name in struct.owned_pointer_fields:
                    value = owned_pointer_initializer_expr(
                        field_type, default_expr, f"{typ}.{field_name}", ctx
                    )
                    parts.append(f".{field_name} = {value}")
                    continue
                coerced = coerce_named_fn_to_fnval(default_expr, field_type, ctx)
                value = coerced if coerced is not None else transform_expr(default_expr, ctx)
                if type_has_managed_value(field_type, ctx) and not expression_produces_owned_value(default_expr, ctx):
                    value = retain_expr_for_type(value, field_type, ctx)
                parts.append(f".{field_name} = {value}")
        if not parts:
            return f"({typ}){{0}}"
        return f"({typ}){{ " + ", ".join(parts) + " }"

    return pattern.sub(repl, expr)


def ptrstruct_inner_type(ztype: str) -> Optional[str]:
    m = re.match(r"ptrstruct\s*<\s*([A-Za-z_]\w*)\s*>", ztype.strip())
    if m:
        return m.group(1)
    return None


def list_receiver_c(receiver: str, ctx: TranspileContext) -> str:
    recv = receiver.strip()
    if re.match(r"^[A-Za-z_]\w*$", recv) and recv in ctx.list_refs:
        return recv
    return "&" + transform_field_access(recv, ctx)


def coerce_named_fn_to_fnval(arg: str, expected_type: str, ctx: "TranspileContext") -> Optional[str]:
    """ZEP-0013: if `expected_type` is a fn type and `arg` is a bare named
    top-level fn reference, return the fat-value constant `<name>_zlfnval`.
    Otherwise return None and let normal expression transform handle it.
    """
    if not is_fn_type(expected_type):
        return None
    bare = arg.strip()
    if re.fullmatch(r"[A-Za-z_]\w*", bare) and bare in ctx.functions:
        fn = ctx.functions[bare]
        actual_type = f"fn({','.join(fn.params.values())})->{fn.ret_type}"
        ensure_assignable(expected_type, actual_type, 0, "function argument")
        return f"{bare}_zlfnval"
    return None


def any_struct_constructor_name(struct_name: str, take: bool) -> str:
    suffix = "take" if take else "copy"
    return f"zl_any_struct_{struct_name}_{suffix}"


def any_expression_transfers_ownership(expr: str) -> bool:
    raw = strip_outer_parens(expr.strip())
    return re.search(r"\.pop\s*\(\s*\)\s*$", raw) is not None


def wrap_arg_for_expected(arg: str, expected_type: str, ctx: TranspileContext) -> str:
    fnval = coerce_named_fn_to_fnval(arg, expected_type, ctx)
    if fnval is not None:
        return fnval
    expected = expected_type.replace(" ", "")
    expected_base = ztype_base(expected)
    actual = infer_type(arg, ctx)
    base = ztype_base(actual)

    if ptrstruct_inner_type(expected):
        # Struct methods are rewritten to internal C calls with `&receiver`.
        # That address is a native receiver pointer, not a source-level ZL_ptr.
        return arg.strip() if arg.strip().startswith("&") else transform_expr(arg, ctx)

    if expected_base == "List":
        # Internal transformed calls may already pass a List by address, e.g.
        # `zlmod_text_join_lines(&xs)` can be transformed again while nested
        # inside another expression. Treat `&list_value` as already wrapped.
        if arg.strip().startswith("&"):
            inner = arg.strip()[1:].strip()
            if ztype_base(infer_type(inner, ctx)) == "List":
                return arg.strip()
        if base != "List":
            raise ZyenError(f"expected List argument, got `{actual}`")
        return list_receiver_c(arg, ctx)

    if expected != "Any":
        if is_fn_type(expected) or ztype_base(expected) == "ptr" or expected in ctx.structs:
            ensure_assignable(expected, actual, 0, "function argument")
        return transform_expr(arg, ctx)

    transformed = transform_expr(arg, ctx)
    if base == "Any":
        return transformed if any_expression_transfers_ownership(arg) else f"zl_any_retain({transformed})"
    if is_fn_type(actual):
        raise ZyenError("List does not accept function values yet; store the callback in a struct field")
    if actual in ctx.structs:
        if actual in ctx.external_structs:
            raise ZyenError(f"List cannot box external C struct `{display_ztype(actual, ctx)}`; wrap it in a ZyenLang facade struct")
        take = expression_produces_owned_value(arg, ctx)
        return f"{any_struct_constructor_name(actual, take)}({transformed})"
    if base == "float":
        return f"zl_any_float({transformed})"
    if base == "bool":
        return f"zl_any_bool({transformed})"
    if base == "str":
        return f"zl_any_str({transformed})"
    if base == "ptr":
        helper = "zl_any_ptr_take" if expression_produces_owned_value(arg, ctx) else "zl_any_ptr"
        return f"{helper}({transformed})"
    if base == "List":
        return f"zl_any_list({list_receiver_c(arg, ctx)})"
    return f"zl_any_int({transformed})"



def split_named_call_arg(arg: str) -> Optional[Tuple[str, str]]:
    """Return (name, value) for a top-level `name: value` call argument."""
    depth = 0
    in_str = False
    escaped = False
    for idx, ch in enumerate(arg):
        if ch == '"' and not escaped:
            in_str = not in_str
        elif not in_str:
            if ch in "({[":
                depth += 1
            elif ch in ")}]":
                depth -= 1
            elif ch == ":" and depth == 0:
                name = arg[:idx].strip()
                value = arg[idx + 1:].strip()
                if re.fullmatch(r"[A-Za-z_]\w*", name):
                    if not value:
                        raise ZyenError(f"named argument `{name}` is missing a value")
                    return name, value
                return None
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    return None


def complete_args_with_defaults(args: List[str], fn: FunctionDef, call_name: str) -> List[str]:
    param_items = list(fn.params.items())
    param_names = [name for name, _typ in param_items]
    required = len([p for p, _t in param_items if p not in fn.defaults])
    total = len(param_items)
    completed: List[Optional[str]] = [None] * total
    used: Set[str] = set()
    positional_index = 0
    seen_named = False

    for arg in args:
        named = split_named_call_arg(arg)
        if named is None:
            if seen_named:
                raise ZyenError(f"function `{call_name}` positional argument cannot follow named argument")
            if positional_index >= total:
                if required == total:
                    raise ZyenError(f"function `{call_name}` expects {total} args, got {len(args)}")
                raise ZyenError(f"function `{call_name}` expects {required}..{total} args, got {len(args)}")
            pname = param_names[positional_index]
            completed[positional_index] = arg
            used.add(pname)
            positional_index += 1
            continue

        seen_named = True
        pname, value = named
        if pname not in fn.params:
            raise ZyenError(f"function `{call_name}` has no parameter `{pname}`")
        if pname in used:
            raise ZyenError(f"function `{call_name}` got duplicate argument `{pname}`")
        index = param_names.index(pname)
        completed[index] = value
        used.add(pname)

    for index, (pname, _ptype) in enumerate(param_items):
        if completed[index] is not None:
            continue
        if pname not in fn.defaults:
            raise ZyenError(f"function `{call_name}` missing required argument `{pname}`")
        completed[index] = fn.defaults[pname]
    if len(args) < required or len(args) > total:
        # Keep the old arity wording for pure positional calls; named calls have
        # already produced the more specific missing/extra/unknown errors above.
        if not any(split_named_call_arg(arg) is not None for arg in args):
            if required == total:
                raise ZyenError(f"function `{call_name}` expects {total} args, got {len(args)}")
            raise ZyenError(f"function `{call_name}` expects {required}..{total} args, got {len(args)}")
    return [arg for arg in completed if arg is not None]

def find_matching_paren(text: str, open_i: int) -> int:
    depth = 0
    in_str = False
    escaped = False
    for i in range(open_i, len(text)):
        ch = text[i]
        if ch == '"' and not escaped:
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    return -1


def split_trailing_call(text: str) -> Optional[Tuple[str, str]]:
    """Split an expression ending in a postfix call into (callee, args)."""
    source = text.strip()
    if not source.endswith(")"):
        return None
    stack: List[int] = []
    pairs: Dict[int, int] = {}
    in_str = False
    escaped = False
    for index, ch in enumerate(source):
        if ch == '"' and not escaped:
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                stack.append(index)
            elif ch == ")":
                if not stack:
                    return None
                pairs[index] = stack.pop()
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    if stack or len(source) - 1 not in pairs:
        return None
    open_i = pairs[len(source) - 1]
    callee = source[:open_i].strip()
    if not callee:
        return None
    return callee, source[open_i + 1:-1]


def split_trailing_field_access(text: str) -> Optional[Tuple[str, str]]:
    """Split a whole postfix field expression into (receiver, field)."""
    source = text.strip()
    if source.startswith(("*", "&")):
        return None
    match = re.search(r"\.\s*([A-Za-z_]\w*)\s*$", source)
    if match is None:
        return None
    dot_i = match.start()
    mask = string_mask(source)
    depth = 0
    for index, ch in enumerate(source[:dot_i]):
        if mask[index]:
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return None
    if depth != 0 or (dot_i < len(mask) and mask[dot_i]):
        return None
    receiver = source[:dot_i].strip()
    return (receiver, match.group(1)) if receiver else None


def _matching_postfix_group(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    in_str = False
    escaped = False
    for index in range(start, len(text)):
        ch = text[index]
        if ch == '"' and not escaped:
            in_str = not in_str
        elif not in_str:
            if ch == opening:
                depth += 1
            elif ch == closing:
                depth -= 1
                if depth == 0:
                    return index
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    return -1


def is_postfix_chain_expr(text: str) -> bool:
    """Whether text is one primary followed only by `.`, `[]`, or `()` suffixes."""
    source = text.strip()
    if not source:
        return False
    index = 0
    if source[index] == "(":
        close = find_matching_paren(source, index)
        if close < 0:
            return False
        index = close + 1
    else:
        primary = re.match(r"[A-Za-z_]\w*", source)
        if primary is None:
            return False
        index = primary.end()

    while index < len(source):
        while index < len(source) and source[index].isspace():
            index += 1
        if index >= len(source):
            return True
        if source[index] == ".":
            field_match = re.match(r"\.\s*([A-Za-z_]\w*)", source[index:])
            if field_match is None:
                return False
            index += field_match.end()
            continue
        if source[index] == "(":
            close = find_matching_paren(source, index)
            if close < 0:
                return False
            index = close + 1
            continue
        if source[index] == "[":
            close = _matching_postfix_group(source, index, "[", "]")
            if close < 0:
                return False
            index = close + 1
            continue
        return False
    return True


def c_fn_value_call(value_c: str, fn_type: str, args: List[str], ctx: TranspileContext, call_name: str, owns_callee: bool = False) -> str:
    param_types, _ret_type = parse_fn_type(fn_type)
    if any(split_named_call_arg(arg) is not None for arg in args):
        raise ZyenError(f"function value `{call_name}` accepts positional arguments only")
    if len(args) != len(param_types):
        raise ZyenError(f"function value `{call_name}` expects {len(param_types)} args, got {len(args)}")
    converted = [wrap_arg_for_expected(arg, expected, ctx) for arg, expected in zip(args, param_types)]
    suffix = ", " + ", ".join(converted) if converted else ""
    helper = fn_call_variant_name(fn_type, owned_argument_mask(args, param_types, ctx), owns_callee)
    return f"{helper}({value_c}{suffix})"


def transform_postfix_fn_call(expr: str, ctx: TranspileContext) -> str:
    trailing = split_trailing_call(expr)
    if trailing is None:
        return expr
    callee, arg_text = trailing
    # Postfix binds before prefix. `*p(args)` is `*(p(args))`; callers must use
    # `(*p)(args)` when p is a ptr<fn(...)>.
    if callee.lstrip().startswith(("*", "&")):
        return expr
    if re.fullmatch(r"[A-Za-z_]\w*", callee) and callee in ctx.functions:
        return expr
    callee_type = infer_type(callee, ctx)
    if not is_fn_type(callee_type):
        return expr
    args = split_args(arg_text) if arg_text.strip() else []
    callee_c = transform_expr(callee, ctx)
    return c_fn_value_call(callee_c, callee_type, args, ctx, callee, expression_produces_owned_value(callee, ctx))


def receiver_start(text: str, dot_i: int) -> int:
    """Find the start of a postfix receiver, including nested calls.

    A simple identifier scan cannot see the receiver in
    `list.get(i).method()`: after rewriting the inner call it ends in `)`.
    Walk backwards across balanced calls/indexes so chained method dispatch
    remains one expression and every callee is evaluated once.
    """
    j = dot_i - 1
    paren = 0
    bracket = 0
    while j >= 0:
        ch = text[j]
        if ch == ")":
            paren += 1
        elif ch == "(":
            if paren > 0:
                paren -= 1
            elif bracket == 0:
                break
        elif ch == "]":
            bracket += 1
        elif ch == "[":
            if bracket > 0:
                bracket -= 1
            elif paren == 0:
                break
        elif paren == 0 and bracket == 0:
            if not (ch.isalnum() or ch in "_."):
                break
        j -= 1
    return j + 1


def split_method_call_at(text: str, i: int) -> Optional[Tuple[int, int, str, str, str]]:
    """Return (start,end,receiver,method,args_body) for method call at/after i."""
    best: Optional[Tuple[int, int, str, str, str]] = None
    for match in re.finditer(r"\.([A-Za-z_]\w*)\s*\(", text[i:]):
        dot = i + match.start()
        method = match.group(1)
        open_i = i + match.end() - 1
        close_i = find_matching_paren(text, open_i)
        if close_i < 0:
            continue
        start = receiver_start(text, dot)
        if start == dot:
            continue
        receiver = text[start:dot].strip()
        if not receiver:
            continue
        candidate = (start, close_i + 1, receiver, method, text[open_i + 1:close_i])
        # Prefer the leftmost receiver. If calls form one postfix chain, prefer
        # the outermost call (`list.get(i).bark()` chooses `bark`).
        if best is None or start < best[0] or (start == best[0] and close_i + 1 > best[1]):
            best = candidate
    return best


def struct_method_fn_type(fn: FunctionDef) -> str:
    return f"fn({','.join(fn.params.values())})->{fn.ret_type}".replace(" ", "")


def struct_method_defaults(fn: FunctionDef) -> Tuple[Optional[str], ...]:
    return tuple(fn.defaults.get(name) for name in fn.params)


def structural_dispatch_name(method: str, fn: FunctionDef) -> str:
    signature_slug = fn_typedef_name(struct_method_fn_type(fn))[len("ZL_fn_"):]
    return f"zl_any_method_{method}_{signature_slug}"


def resolve_structural_method(
    item_types: Optional[Set[str]], method: str, ctx: TranspileContext
) -> Tuple[FunctionDef, List[str]]:
    if item_types is not None:
        if not item_types:
            raise ZyenError(
                f"cannot call `{method}()` on an empty List with no inferred element types"
            )
        candidates = sorted(item_types)
        methods: List[Tuple[str, FunctionDef]] = []
        for item_type in candidates:
            if item_type not in ctx.structs:
                raise ZyenError(
                    f"List structural call `{method}()` requires struct elements, "
                    f"but the inferred List can contain `{item_type}`"
                )
            fn = ctx.structs[item_type].methods.get(method)
            if fn is None:
                raise ZyenError(
                    f"struct `{display_ztype(item_type, ctx)}` does not provide "
                    f"the common method `{method}()` required by this List"
                )
            methods.append((item_type, fn))
        signatures = {struct_method_fn_type(fn) for _name, fn in methods}
        defaults = {struct_method_defaults(fn) for _name, fn in methods}
        if len(signatures) != 1 or len(defaults) != 1:
            detail = ", ".join(
                f"{name}.{method}: {struct_method_fn_type(fn)}"
                for name, fn in methods
            )
            raise ZyenError(
                f"List method `{method}()` is not structurally compatible; "
                f"all element structs need the same parameter, return, and default values ({detail})"
            )
        return methods[0][1], candidates

    groups: Dict[Tuple[str, Tuple[Optional[str], ...]], List[str]] = {}
    first_by_shape: Dict[Tuple[str, Tuple[Optional[str], ...]], FunctionDef] = {}
    for struct_name, struct in ctx.structs.items():
        if struct_name in ctx.external_structs or method not in struct.methods:
            continue
        fn = struct.methods[method]
        shape = (struct_method_fn_type(fn), struct_method_defaults(fn))
        groups.setdefault(shape, []).append(struct_name)
        first_by_shape.setdefault(shape, fn)
    if not groups:
        raise ZyenError(f"dynamic List value has no known struct method `{method}()`")
    if len(groups) != 1:
        choices = ", ".join(
            f"{signature} defaults={defaults}"
            for signature, defaults in sorted(groups, key=lambda item: (item[0], repr(item[1])))
        )
        raise ZyenError(
            f"dynamic List method `{method}()` is ambiguous across signatures: {choices}; "
            "keep the List local so its element structs can be inferred"
        )
    shape = next(iter(groups))
    return first_by_shape[shape], sorted(groups[shape])

def transform_method_calls(expr: str, ctx: TranspileContext) -> str:
    """Convert method calls with balanced nested arguments.

    The old regex used `[^()]*`, so calls like
        obj.method((str)xs.get(0))
    were not transformed. This scanner handles nested parentheses and receivers
    such as `this.lines.append(x)`.
    """
    i = 0
    out: List[str] = []
    changed = False
    while i < len(expr):
        found = split_method_call_at(expr, i)
        if not found:
            out.append(expr[i:])
            break
        start, end, obj, method, body = found
        out.append(expr[i:start])
        obj_type = infer_type(obj, ctx)
        args = split_args(body)
        repl: Optional[str] = None

        if obj_type == "List":
            recv = list_receiver_c(obj, ctx)
            if method == "append":
                if len(args) != 1:
                    raise ZyenError(f"List.append expects 1 arg, got {len(args)}")
                record_list_item_type(obj, args[0], ctx)
                repl = f"zl_list_append({recv}, {wrap_arg_for_expected(args[0], 'Any', ctx)})"
            elif method == "len":
                if len(args) != 0:
                    raise ZyenError(f"List.len expects 0 args, got {len(args)}")
                repl = f"zl_list_len({recv})"
            elif method == "is_empty":
                if len(args) != 0:
                    raise ZyenError(f"List.is_empty expects 0 args, got {len(args)}")
                repl = f"zl_list_is_empty({recv})"
            elif method == "get":
                if len(args) != 1:
                    raise ZyenError(f"List.get expects 1 arg, got {len(args)}")
                repl = f"zl_list_get({recv}, {transform_expr(args[0], ctx)})"
            elif method == "ptr":
                if len(args) != 1:
                    raise ZyenError(f"List.ptr expects 1 arg, got {len(args)}")
                repl = f"zl_list_ptr({recv}, {transform_expr(args[0], ctx)})"
            elif method == "append_ptr":
                if len(args) != 1:
                    raise ZyenError(f"List.append_ptr expects 1 arg, got {len(args)}")
                record_list_item_type(obj, args[0], ctx)
                repl = f"zl_list_append_ptr({recv}, {wrap_arg_for_expected(args[0], 'Any', ctx)})"
            elif method == "set":
                if len(args) != 2:
                    raise ZyenError(f"List.set expects 2 args, got {len(args)}")
                record_list_item_type(obj, args[1], ctx)
                repl = f"zl_list_set({recv}, {transform_expr(args[0], ctx)}, {wrap_arg_for_expected(args[1], 'Any', ctx)})"
            elif method == "pop":
                if len(args) != 0:
                    raise ZyenError(f"List.pop expects 0 args, got {len(args)}")
                repl = f"zl_list_pop({recv})"
            elif method == "clear":
                if len(args) != 0:
                    raise ZyenError(f"List.clear expects 0 args, got {len(args)}")
                repl = f"zl_list_clear({recv})"
            else:
                raise ZyenError(f"unknown method `{method}` for List")

        elif struct_union_types(obj_type) is not None or ztype_base(obj_type) == "Any":
            union_types = struct_union_types(obj_type)
            fn, _candidate_names = resolve_structural_method(union_types, method, ctx)
            if any(split_named_call_arg(arg) is not None for arg in args):
                raise ZyenError(
                    f"structural List method `{method}()` uses positional arguments; "
                    "method parameter names are not part of the shared shape"
                )
            args = complete_args_with_defaults(args, fn, f"List element.{method}")
            param_types = list(fn.params.values())
            converted = [
                wrap_arg_for_expected(arg, param_type, ctx)
                for arg, param_type in zip(args, param_types)
            ]
            owned_mask = owned_argument_mask(args, param_types, ctx) << 1
            if any_expression_transfers_ownership(obj):
                owned_mask |= 1
            target = structural_dispatch_name(method, fn)
            call_name = direct_owned_args_wrapper_name(target, owned_mask) if owned_mask else target
            rest = ", " + ", ".join(converted) if converted else ""
            repl = f"{call_name}({transform_expr(obj, ctx)}{rest})"

        elif obj_type in ctx.structs:
            struct = ctx.structs[obj_type]
            # ZEP-0013: fn-typed field is a fat value; call dispatches through .call/.env.
            if method in struct.fields and is_fn_type(struct.fields[method]):
                field_type = struct.fields[method]
                base = f"({transform_expr(obj, ctx)}).{method}"
                repl = c_fn_value_call(base, field_type, args, ctx, f"{obj}.{method}")
            elif method not in struct.methods:
                raise ZyenError(f"unknown method `{method}` for struct `{display_ztype(obj_type, ctx)}`")
            else:
                fn = struct.methods[method]
                args = complete_args_with_defaults(args, fn, f"{obj_type}.{method}")
                param_types = list(fn.params.values())
                converted = [wrap_arg_for_expected(arg, param_type, ctx) for arg, param_type in zip(args, param_types)]
                owned_mask = owned_argument_mask(args, param_types, ctx) << 1
                rest = ", " + ", ".join(converted) if converted else ""
                target = f"{obj_type}_{method}"
                call_name = direct_owned_args_wrapper_name(target, owned_mask) if owned_mask else target
                repl = f"{call_name}(&{transform_expr(obj, ctx)}{rest})"

        else:
            owner = ptrstruct_inner_type(obj_type) if obj_type else None
            if owner in ctx.structs:
                struct = ctx.structs[owner]
                # ZEP-0013: fn-typed field call on `this` is a fat dispatch.
                if method in struct.fields and is_fn_type(struct.fields[method]):
                    field_type = struct.fields[method]
                    base = f"{transform_field_access(obj, ctx)}->{method}"
                    repl = c_fn_value_call(base, field_type, args, ctx, f"{obj}.{method}")
                elif method not in struct.methods:
                    raise ZyenError(f"unknown method `{method}` for struct `{display_ztype(owner, ctx)}`")
                else:
                    fn = struct.methods[method]
                    args = complete_args_with_defaults(args, fn, f"{owner}.{method}")
                    param_types = list(fn.params.values())
                    converted = [wrap_arg_for_expected(arg, param_type, ctx) for arg, param_type in zip(args, param_types)]
                    owned_mask = owned_argument_mask(args, param_types, ctx) << 1
                    rest = ", " + ", ".join(converted) if converted else ""
                    target = f"{owner}_{method}"
                    call_name = direct_owned_args_wrapper_name(target, owned_mask) if owned_mask else target
                    repl = f"{call_name}({transform_field_access(obj, ctx)}{rest})"

        if repl is None:
            # Not a Zyen object method; preserve original text and continue after it.
            out.append(expr[start:end])
        else:
            out.append(repl)
            changed = True
        i = end
    new_expr = "".join(out)
    # A replacement can expose another method call inside an argument; iterate a
    # few times but avoid infinite loops.
    if changed and new_expr != expr and "." in new_expr:
        for _ in range(3):
            again = transform_method_calls(new_expr, ctx)
            if again == new_expr:
                break
            new_expr = again
    return new_expr


def transform_function_calls(expr: str, ctx: TranspileContext) -> str:
    """Convert direct Zyen function calls so List params pass by reference.

    Most calls compile unchanged, but `fn add(xs: List, v: str)` must receive a
    `ZL_List*`, otherwise mutation happens on a local copy.
    """
    i = 0
    out: List[str] = []
    changed = False
    mask = string_mask(expr)
    while i < len(expr):
        m = re.search(r"\b([A-Za-z_]\w*)\s*\(", expr[i:])
        if not m:
            out.append(expr[i:])
            break
        name_start = i + m.start()
        name = m.group(1)
        open_i = i + m.end() - 1
        if name_start < len(mask) and mask[name_start]:
            out.append(expr[i:open_i + 1])
            i = open_i + 1
            continue
        prev = expr[name_start - 1] if name_start > 0 else ""
        if prev == ".":
            out.append(expr[i:open_i + 1])
            i = open_i + 1
            continue
        # ZEP-0013: call through a fn-typed local/param `f(args)` → fat dispatch.
        if name not in ctx.functions:
            sym_type = ctx.symbols.get(name)
            if sym_type and is_fn_type(sym_type):
                close_i = find_matching_paren(expr, open_i)
                if close_i < 0:
                    out.append(expr[i:])
                    break
                chain_end = close_i
                cursor = close_i + 1
                while cursor < len(expr):
                    while cursor < len(expr) and expr[cursor].isspace():
                        cursor += 1
                    if cursor >= len(expr) or expr[cursor] != "(":
                        break
                    next_close = find_matching_paren(expr, cursor)
                    if next_close < 0:
                        break
                    chain_end = next_close
                    cursor = next_close + 1
                if chain_end > close_i:
                    chain_text = expr[name_start:chain_end + 1]
                    out.append(expr[i:name_start])
                    out.append(transform_postfix_fn_call(chain_text, ctx))
                    changed = True
                    i = chain_end + 1
                    continue
                arg_text = expr[open_i + 1:close_i]
                raw_args = split_args(arg_text) if arg_text.strip() else []
                out.append(expr[i:name_start])
                out.append(c_fn_value_call(name, sym_type, raw_args, ctx, name))
                changed = True
                i = close_i + 1
                continue
            out.append(expr[i:open_i + 1])
            i = open_i + 1
            continue
        close_i = find_matching_paren(expr, open_i)
        if close_i < 0:
            out.append(expr[i:])
            break
        args = split_args(expr[open_i + 1:close_i])
        fn = ctx.functions[name]
        args = complete_args_with_defaults(args, fn, name)
        param_types = list(fn.params.values())
        converted = [wrap_arg_for_expected(arg, ptype, ctx) for arg, ptype in zip(args, param_types)]
        owned_mask = owned_argument_mask(args, param_types, ctx)
        call_name = direct_owned_args_wrapper_name(name, owned_mask) if owned_mask else name
        out.append(expr[i:name_start])
        out.append(f"{call_name}(" + ", ".join(converted) + ")")
        changed = True
        i = close_i + 1
    return "".join(out) if changed else expr


def is_cast_expr(expr: str) -> Optional[Tuple[str, str]]:
    """Return (target_type, inner_expr) for a whole-expression C-like cast.

    Zyen cast syntax intentionally follows C style:
        (str)i
        (int)text
        (float)x
        (bool)value

    We only treat it as a cast when the entire expression starts with a known
    type cast. Normal parentheses such as `(a + b)` are handled elsewhere.
    """
    source = expr.strip()
    if not source.startswith("("):
        return None
    close_i = find_matching_paren(source, 0)
    if close_i < 0 or close_i == len(source) - 1:
        return None
    target = source[1:close_i].strip().replace(" ", "")
    is_struct_name = re.fullmatch(r"[A-Z][A-Za-z0-9_]*", target) is not None
    if target not in {"int", "float", "bool", "str", "ptr", "char"} and ptr_inner_type(target) is None and not is_struct_name:
        return None
    return target, source[close_i + 1:].strip()


def reject_malformed_pointer_cast(expr: str) -> None:
    """Reject `(ptr<T>value)` before it leaks through as invalid C.

    The canonical C-like spelling is `(ptr<T>)value`. Parenthesized pointer
    comparisons such as `(ptr<int> == value)` are not mistaken for casts.
    """
    source = expr.strip()
    mask = string_mask(source)
    for index, ch in enumerate(source):
        if ch != "(" or mask[index]:
            continue
        close_i = find_matching_paren(source, index)
        if close_i < 0:
            continue
        content = source[index + 1:close_i]
        try:
            target, type_end = parse_type_prefix(content)
        except ZyenError:
            continue
        if ztype_base(target) != "ptr":
            continue
        remainder = content[type_end:].strip()
        if remainder and re.match(r'^(?:[A-Za-z_]\w*|\d|"|&|\*|\()', remainder):
            bad = source[index:close_i + 1]
            raise ZyenError(
                f"malformed pointer cast `{bad}`; write `({target}){remainder}` "
                f"(for dereference: `*(({target}){remainder})`)"
            )


def c_cast_expr(target_type: str, inner: str, ctx: TranspileContext) -> str:
    # convert_struct_literal() emits a C compound literal before nested outer
    # calls are rewritten. It is already a value of the requested struct type,
    # not a source-level cast from the initializer body.
    if target_type in ctx.structs and inner.lstrip().startswith("{"):
        return f"({target_type}){inner}"
    if re.fullmatch(r"[A-Z][A-Za-z0-9_]*", target_type) and target_type not in ctx.structs:
        raise ZyenError(f"unknown struct cast type `{target_type}`")
    source_type = infer_type(inner, ctx)
    source_base = ztype_base(source_type)
    if is_fn_type(source_type):
        raise ZyenError("function values cannot be cast; use the exact fn(...) -> T signature")
    inner_c = transform_expr(inner, ctx)

    if target_type == "Any":
        return wrap_arg_for_expected(inner, "Any", ctx)

    if target_type == source_base:
        return inner_c

    if target_type == "str":
        if source_base == "int":
            return f"zl_cast_str_int({inner_c})"
        if source_base == "float":
            return f"zl_cast_str_float({inner_c})"
        if source_base == "bool":
            return f"zl_cast_str_bool({inner_c})"
        if source_base == "Any":
            return f"zl_cast_str_any({inner_c})"
        if source_base == "ptr":
            release_after = "true" if expression_produces_owned_value(inner, ctx) else "false"
            return f"zl_cast_str_ptr_release({inner_c}, {release_after})"
        if source_base == "List":
            return f"zl_cast_str_list(&{inner_c})"
        return inner_c

    if target_type == "int":
        if source_base == "str":
            return f"zl_cast_int_str({inner_c})"
        if source_base == "float":
            return f"((int){inner_c})"
        if source_base == "bool":
            return f"(({inner_c}) ? 1 : 0)"
        if source_base == "Any":
            return f"zl_cast_int_any({inner_c})"
        return f"((int){inner_c})"

    if target_type == "float":
        if source_base == "str":
            return f"zl_cast_float_str({inner_c})"
        if source_base == "bool":
            return f"(({inner_c}) ? 1.0 : 0.0)"
        if source_base == "Any":
            return f"zl_cast_float_any({inner_c})"
        return f"((double){inner_c})"

    if target_type == "bool":
        if source_base == "str":
            return f"zl_cast_bool_str({inner_c})"
        if source_base == "Any":
            return f"zl_cast_bool_any({inner_c})"
        if source_base == "ptr":
            return f"({inner_c}.addr != NULL)"
        return f"(({inner_c}) != 0)"

    if target_type == "char":
        return f"zl_char_to_str({transform_expr(inner, ctx)})"

    if target_type in ctx.structs:
        if target_type in ctx.external_structs:
            raise ZyenError(
                f"cannot restore external C struct `{display_ztype(target_type, ctx)}` from a List; "
                "wrap it in a ZyenLang facade struct"
            )
        if source_base != "Any":
            raise ZyenError(f"cannot cast `{source_type}` to struct `{target_type}`; only a List value can be restored to its exact struct type")
        suffix = "take" if any_expression_transfers_ownership(inner) else "borrow"
        return f"zl_any_cast_{target_type}_{suffix}({inner_c})"

    if ztype_base(target_type) == "ptr":
        if source_base == "Any":
            return f"zl_cast_ptr_any({inner_c})"
        if source_base == "ptr":
            target_inner = ptr_inner_type(target_type)
            source_inner = ptr_inner_type(source_type)
            if (target_inner and is_fn_type(target_inner)) or (source_inner and is_fn_type(source_inner)):
                if target_inner not in {"void", source_inner} and source_inner not in {"void", target_inner}:
                    raise ZyenError(
                        f"cannot cast `{source_type}` to `{target_type}`; function pointer signatures must match exactly"
                    )
            return inner_c
        raise ZyenError(f"cannot cast `{source_type}` to `{target_type}`; pointer casts require another ptr value")

    return inner_c


def strip_outer_parens(expr: str) -> str:
    source = expr.strip()
    while source.startswith("("):
        close_i = find_matching_paren(source, 0)
        if close_i != len(source) - 1:
            break
        source = source[1:-1].strip()
    return source


def unary_deref_operand(expr: str) -> Optional[str]:
    """Return the complete operand of a pure unary dereference expression."""
    source = expr.strip()
    if not source.startswith("*"):
        return None
    operand = source[1:].strip()
    if not operand:
        return None
    if is_postfix_chain_expr(operand):
        return operand
    cast = is_cast_expr(operand)
    if cast is not None and ztype_base(cast[0]) == "ptr":
        return operand
    if operand.startswith("*") and unary_deref_operand(operand) is not None:
        return operand
    if operand.startswith("(") and find_matching_paren(operand, 0) == len(operand) - 1:
        return operand
    return None


def address_of_operand(expr: str) -> Optional[str]:
    source = expr.strip()
    if not source.startswith("&") or source.startswith("&&"):
        return None
    operand = source[1:].strip()
    if re.fullmatch(r"[A-Za-z_]\w*", operand):
        return operand
    if unary_deref_operand(operand) is not None:
        return operand
    raise ZyenError(
        f"address-of needs a local variable or managed pointer dereference, got `{expr}`"
    )


def deref_info(expr: str, ctx: TranspileContext) -> Optional[Tuple[str, str, str]]:
    """Return (pointer C expression, pointee type, diagnostic label)."""
    raw = expr.strip()
    operand = unary_deref_operand(raw)
    if operand is None:
        return None
    pointer_expr = strip_outer_parens(operand)
    pointer_type = infer_type(pointer_expr, ctx)
    if ztype_base(pointer_type) != "ptr":
        raise ZyenError(f"cannot dereference non-pointer expression `{pointer_expr}`")
    target = ptr_inner_type(pointer_type)
    if target is None:
        raise ZyenError(
            f"cannot infer the pointee type of `{pointer_expr}`; use a concrete `ptr<T>`"
        )
    return transform_expr(pointer_expr, ctx), target, pointer_expr


def transform_address_of(expr: str, ctx: TranspileContext) -> Optional[str]:
    operand = address_of_operand(expr)
    if operand is None:
        return None
    deref_operand = unary_deref_operand(operand)
    if deref_operand is not None:
        pointer_expr = strip_outer_parens(deref_operand)
        pointer_type = infer_type(pointer_expr, ctx)
        if ztype_base(pointer_type) != "ptr":
            raise ZyenError(f"cannot take the address of `{operand}`")
        return transform_expr(pointer_expr, ctx)
    target_type = infer_type(operand, ctx)
    if operand in ctx.functions and operand not in ctx.symbols:
        return f'zl_ptr(&{operand}_zlfnval, {json.dumps(target_type.replace(" ", ""))})'
    return f'zl_ptr(&{operand}, "{type_name_for_runtime(target_type)}")'


def transform_special_equality(expr: str, ctx: TranspileContext) -> Optional[str]:
    source = expr.strip()
    mask = string_mask(source)
    depth = 0
    for index in range(len(source) - 1):
        if mask[index]:
            continue
        ch = source[index]
        if ch in "([{":
            depth += 1
            continue
        if ch in ")]}":
            depth -= 1
            continue
        op = source[index:index + 2]
        if depth != 0 or op not in {"==", "!="}:
            continue
        left = source[:index].strip()
        right = source[index + 2:].strip()
        left_type = infer_type(left, ctx)
        right_type = infer_type(right, ctx)
        if ztype_base(left_type) == "str" and ztype_base(right_type) == "str":
            comparison = f"strcmp({transform_expr(left, ctx)}, {transform_expr(right, ctx)})"
            return f"({comparison} {'==' if op == '==' else '!='} 0)"
        if is_fn_type(left_type) and right_type == "none":
            helper = "zl_fn_is_none_owned" if expression_produces_owned_value(left, ctx) else "zl_fn_is_none"
            test = f"{helper}({transform_expr(left, ctx)})"
            return test if op == "==" else f"(!{test})"
        if left_type == "none" and is_fn_type(right_type):
            helper = "zl_fn_is_none_owned" if expression_produces_owned_value(right, ctx) else "zl_fn_is_none"
            test = f"{helper}({transform_expr(right, ctx)})"
            return test if op == "==" else f"(!{test})"
        return None
    return None


def split_top_level_condition(expr: str) -> Optional[Tuple[str, str, str]]:
    source = expr.strip()
    mask = string_mask(source)
    depth = 0
    candidates: List[Tuple[int, str]] = []
    index = 0
    while index < len(source):
        if mask[index]:
            index += 1
            continue
        ch = source[index]
        if ch in "([{":
            depth += 1
            index += 1
            continue
        if ch in ")]}":
            depth -= 1
            index += 1
            continue
        if depth == 0:
            two = source[index:index + 2]
            if two in {"<<", ">>"}:
                index += 2
                continue
            if two in {"||", "&&", "==", "!=", "<=", ">="}:
                candidates.append((index, two))
                index += 2
                continue
            if ch in {"<", ">"}:
                candidates.append((index, ch))
        index += 1
    if not candidates:
        return None
    for operators in ({"||"}, {"&&"}, {"==", "!=", "<=", ">=", "<", ">"}):
        for position, operator in candidates:
            if operator not in operators:
                continue
            left = source[:position].strip()
            right = source[position + len(operator):].strip()
            if left and right:
                return left, operator, right
    return None

def field_access_type(expr: str, ctx: TranspileContext) -> Optional[str]:
    split = split_trailing_field_access(expr)
    if split is None:
        return None
    receiver, field_name = split
    receiver_type = infer_type(receiver, ctx)
    if receiver_type in ctx.structs:
        return ctx.structs[receiver_type].fields.get(field_name)
    method_owner = ptrstruct_inner_type(receiver_type)
    if method_owner in ctx.structs:
        return ctx.structs[method_owner].fields.get(field_name)
    pointer_owner = ptr_inner_type(receiver_type) if ztype_base(receiver_type) == "ptr" else None
    if pointer_owner in ctx.structs:
        raise ZyenError(
            f"cannot access `{field_name}` directly through `{display_ztype(receiver_type, ctx)}`; "
            f"write `(*{receiver}).{field_name}`"
        )
    return None


def transform_exact_field_access(expr: str, ctx: TranspileContext) -> Optional[str]:
    split = split_trailing_field_access(expr)
    if split is None or expr.strip().endswith(".addr"):
        return None
    receiver, field_name = split
    receiver_type = infer_type(receiver, ctx)
    if receiver_type in ctx.structs and field_name in ctx.structs[receiver_type].fields:
        return f"({transform_expr(receiver, ctx)}).{field_name}"
    method_owner = ptrstruct_inner_type(receiver_type)
    if method_owner in ctx.structs and field_name in ctx.structs[method_owner].fields:
        return f"({transform_expr(receiver, ctx)})->{field_name}"
    pointer_owner = ptr_inner_type(receiver_type) if ztype_base(receiver_type) == "ptr" else None
    if pointer_owner in ctx.structs and field_name in ctx.structs[pointer_owner].fields:
        raise ZyenError(
            f"cannot access `{field_name}` directly through `{display_ztype(receiver_type, ctx)}`; "
            f"write `(*{receiver}).{field_name}`"
        )
    return None


def transform_field_access(expr: str, ctx: TranspileContext) -> str:
    # Convert method-body field access on `this`: this.x -> this->x
    pattern = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b(?!\s*\()")

    def repl(m: re.Match[str]) -> str:
        name, field_name = m.group(1), m.group(2)
        stype = ctx.symbols.get(name)
        owner = ptrstruct_inner_type(stype) if stype else None
        if owner and owner in ctx.structs and field_name in ctx.structs[owner].fields:
            return f"{name}->{field_name}"
        return m.group(0)

    return pattern.sub(repl, expr)

def transform_ptr_index(expr: str, ctx: TranspileContext) -> str:
    """Convert Zyen pointer indexing into C pointer indexing.

    Zyen user code should stay clean:
        p[index]
        set p[index] = value;

    The generated C may still use casts, but those casts should stay inside the
    backend, not inside `.zy` standard-library code.
    """
    pattern = re.compile(r"\b([A-Za-z_]\w*)\s*\[\s*([^\[\]]+)\s*\]")

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        index = m.group(2).strip()
        target = ctx.ptr_targets.get(name)
        if not target:
            return m.group(0)
        return f"(({c_type(target)}*)zl_ptr_checked_addr({name}, \"{name}\"))[{transform_expr(index, ctx)}]"

    return pattern.sub(repl, expr)





def has_top_level_comparison(expr: str) -> bool:
    depth = 0
    in_str = False
    escaped = False
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == '"' and not escaped:
            in_str = not in_str
        elif not in_str:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif depth == 0:
                two = expr[i:i + 2]
                if two in {"<<", ">>"}:
                    i += 2
                    continue
                if two in {"==", "!=", "<=", ">=", "&&", "||"}:
                    return True
                if ch in {"<", ">"}:
                    return True
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
        i += 1
    return False


def find_top_level_int_op(expr: str) -> Optional[Tuple[str, str, str]]:
    # Use forward-scan string_mask so escape sequences like `\"` are detected
    # correctly. The earlier reverse-tracking of in_str/escaped misread
    # literals such as `"start \"x\" /K /D \""` as having out-of-string `/`s.
    in_s = string_mask(expr)
    def scan(ops: Set[str]) -> Optional[Tuple[int, str]]:
        depth = 0
        for i in range(len(expr) - 1, -1, -1):
            if in_s[i]:
                continue
            ch = expr[i]
            if ch in ")]}":
                depth += 1
            elif ch in "([{}":
                depth -= 1
            elif depth == 0 and ch in ops:
                prev = expr[i - 1] if i > 0 else ""
                nxt = expr[i + 1] if i + 1 < len(expr) else ""
                if ch == "+" and (prev == "+" or nxt == "+" or nxt == "=" or prev == "="):
                    continue
                if ch == "-" and (prev == "-" or nxt == "-" or nxt == "=" or prev == "" or prev in "+-*/(<>=!,&|"):
                    continue
                if ch == "*" and (prev == "*" or nxt == "=" or prev == "="):
                    continue
                if ch == "/" and (nxt == "=" or prev == "="):
                    continue
                return i, ch
        return None
    found = scan({"+", "-"}) or scan({"*", "/"})
    if not found:
        return None
    i, op = found
    left = expr[:i].strip()
    right = expr[i + 1:].strip()
    if not left or not right:
        return None
    return left, op, right


def transform_checked_int_arithmetic(expr: str, ctx: TranspileContext) -> str:
    raw = expr.strip()
    if has_top_level_comparison(raw):
        return raw
    found = find_top_level_int_op(raw)
    if not found:
        return raw
    left, op, right = found
    if ztype_base(infer_type(left, ctx)) != "int" or ztype_base(infer_type(right, ctx)) != "int":
        return raw
    left_c = transform_expr(left, ctx)
    right_c = transform_expr(right, ctx)
    fn = {"+": "zl_int_add", "-": "zl_int_sub", "*": "zl_int_mul", "/": "zl_int_div"}[op]
    return f"{fn}({left_c}, {right_c})"


def string_mask(text: str) -> List[bool]:
    """Return True for indexes that are inside a normal string literal.

    This left-to-right scan avoids the reverse-scan escape bug that misread
    expressions such as `out + "\\n"`.
    """
    mask = [False] * len(text)
    in_str = False
    escaped = False
    for i, ch in enumerate(text):
        mask[i] = in_str
        if ch == '"' and not escaped:
            in_str = not in_str
            mask[i] = True
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    return mask


def _split_top_level_arith(expr: str) -> Optional[List[str]]:
    """Split `expr` on top-level `+ - * /` outside strings/parens.
    Returns None if no top-level operator is found. Skips signs like a
    leading `-x` or `+x`. Does not split on `+=`, `-=`, `*=`, `/=`, `+ +`, etc.
    """
    in_s = string_mask(expr)
    depth = 0
    pieces: List[str] = []
    start = 0
    i = 0
    n = len(expr)
    while i < n:
        if in_s[i]:
            i += 1
            continue
        ch = expr[i]
        if ch in "([{":
            depth += 1
            i += 1
            continue
        if ch in ")]}":
            depth -= 1
            i += 1
            continue
        if depth == 0 and ch in "+-*/":
            prev = expr[i - 1] if i > 0 else ""
            nxt = expr[i + 1] if i + 1 < n else ""
            # Skip compound operators (+=, -=, *=, /=, ++ , -- , /*, */, //).
            if nxt == "=" or nxt == ch or prev == ch or prev == "/" or nxt == "/":
                i += 1
                continue
            # Skip unary sign at the start of the expression or right after another operator.
            if ch in "+-":
                # find previous non-space char excluding our own pos
                j = i - 1
                while j >= 0 and expr[j] == " ":
                    j -= 1
                if j < 0 or expr[j] in "+-*/(,":
                    i += 1
                    continue
            piece = expr[start:i].strip()
            if piece:
                pieces.append(piece)
            start = i + 1
            i += 1
            continue
        i += 1
    if not pieces:
        return None
    tail = expr[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces


def find_top_level_plus(expr: str) -> Optional[Tuple[str, str]]:
    """Find the last top-level `+` suitable for string concatenation."""
    in_s = string_mask(expr)
    depth = 0
    positions: List[int] = []
    for i, ch in enumerate(expr):
        if in_s[i]:
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch == "+":
            prev = expr[i - 1] if i > 0 else ""
            nxt = expr[i + 1] if i + 1 < len(expr) else ""
            if prev == "+" or nxt == "+" or prev == "=" or nxt == "=":
                continue
            positions.append(i)
    if not positions:
        return None
    i = positions[-1]
    left = expr[:i].strip()
    right = expr[i + 1:].strip()
    if left and right:
        return left, right
    return None


def transform_string_concat(expr: str, ctx: TranspileContext) -> str:
    """Convert Zyen string concatenation into runtime string join.

    Supported syntax:
        "my age is " + (str)this.age
        "x=" + (str)x + ", y=" + (str)y

    Only expressions where at least one top-level `+` side is `str` are treated
    as string concatenation. Plain int/float addition remains numeric.
    """
    raw = expr.strip()
    if has_top_level_comparison(raw):
        return raw
    found = find_top_level_plus(raw)
    if not found:
        return raw
    left, right = found
    left_type = ztype_base(infer_type(left, ctx))
    right_type = ztype_base(infer_type(right, ctx))
    if left_type != "str" and right_type != "str":
        return raw
    left_c = c_cast_expr("str", left, ctx)
    right_c = c_cast_expr("str", right, ctx)
    return f"zl_str_join(2, {left_c}, {right_c})"

def is_fstring_expr(expr: str) -> bool:
    return re.match(r'^f".*"$', expr.strip()) is not None


def parse_fstring_parts(expr: str) -> List[Tuple[str, str]]:
    """Return parts as (kind, text), where kind is 'lit' or 'expr'.

    Supported syntax is intentionally small and readable:
        f"rpm={rpm} ok={ok}"

    Escapes:
        {{ -> literal {
        }} -> literal }
    """
    text = expr.strip()
    if not is_fstring_expr(text):
        raise ZyenError("internal error: expected f-string")
    body = text[2:-1]
    parts: List[Tuple[str, str]] = []
    lit: List[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "{" and i + 1 < len(body) and body[i + 1] == "{":
            lit.append("{")
            i += 2
            continue
        if ch == "}" and i + 1 < len(body) and body[i + 1] == "}":
            lit.append("}")
            i += 2
            continue
        if ch == "{":
            if lit:
                parts.append(("lit", "".join(lit)))
                lit = []
            depth = 1
            j = i + 1
            in_str = False
            escaped = False
            expr_buf: List[str] = []
            while j < len(body):
                cj = body[j]
                if cj == '"' and not escaped:
                    in_str = not in_str
                if not in_str:
                    if cj == "{":
                        depth += 1
                    elif cj == "}":
                        depth -= 1
                        if depth == 0:
                            break
                expr_buf.append(cj)
                escaped = cj == "\\" and not escaped
                if cj != "\\":
                    escaped = False
                j += 1
            if depth != 0:
                raise ZyenError("unterminated f-string expression")
            inner = "".join(expr_buf).strip()
            if not inner:
                raise ZyenError("empty f-string expression")
            parts.append(("expr", inner))
            i = j + 1
            continue
        if ch == "}":
            raise ZyenError("single `}` in f-string; use `}}` for a literal brace")
        lit.append(ch)
        i += 1
    if lit:
        parts.append(("lit", "".join(lit)))
    return parts


def c_string_literal(text: str) -> str:
    return json.dumps(text)


def transform_fstring(expr: str, ctx: TranspileContext) -> str:
    parts = parse_fstring_parts(expr)
    if not parts:
        return '""'
    c_parts: List[str] = []
    for kind, text in parts:
        if kind == "lit":
            if text:
                c_parts.append(c_string_literal(text))
        else:
            c_parts.append(c_cast_expr("str", text, ctx))
    if not c_parts:
        return '""'
    if len(c_parts) == 1:
        return c_parts[0]
    return f"zl_str_join({len(c_parts)}, " + ", ".join(c_parts) + ")"


def replace_fstrings_in_expr(expr: str, ctx: TranspileContext) -> str:
    """Replace f"..." tokens outside ordinary strings.

    v0.1.41 treated any `f"` substring as an f-string starter, even inside
    normal strings, so `string.eq(x, "f")` failed. This scanner tracks normal
    string state first.
    """
    out: List[str] = []
    i = 0
    changed = False
    in_str = False
    escaped = False
    while i < len(expr):
        ch = expr[i]
        if in_str:
            out.append(ch)
            if ch == '"' and not escaped:
                in_str = False
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            escaped = False
            out.append(ch)
            i += 1
            continue
        if ch == "f" and i + 1 < len(expr) and expr[i + 1] == '"':
            if i > 0 and (expr[i - 1].isalnum() or expr[i - 1] == "_"):
                out.append(ch)
                i += 1
                continue
            j = i + 2
            esc = False
            while j < len(expr):
                cj = expr[j]
                if cj == '"' and not esc:
                    break
                esc = cj == "\\" and not esc
                if cj != "\\":
                    esc = False
                j += 1
            if j >= len(expr):
                raise ZyenError("unterminated f-string")
            out.append(transform_fstring(expr[i:j + 1], ctx))
            changed = True
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out) if changed else expr

def transform_expr(expr: str, ctx: TranspileContext) -> str:
    expr = expr.strip()
    reject_malformed_pointer_cast(expr)
    if is_fstring_expr(expr):
        return transform_fstring(expr, ctx)
    cast = is_cast_expr(expr)
    if cast:
        return c_cast_expr(cast[0], cast[1], ctx)
    unwrapped = strip_outer_parens(expr)
    if unwrapped != expr:
        return f"({transform_expr(unwrapped, ctx)})"
    special_equality = transform_special_equality(expr, ctx)
    if special_equality is not None:
        return special_equality
    condition = split_top_level_condition(expr)
    if condition is not None:
        left, operator, right = condition
        return f"({transform_expr(left, ctx)} {operator} {transform_expr(right, ctx)})"
    exact_field = transform_exact_field_access(expr, ctx)
    if exact_field is not None:
        return exact_field
    postfix_call = transform_postfix_fn_call(expr, ctx)
    if postfix_call != expr:
        return postfix_call
    address = transform_address_of(expr, ctx)
    if address is not None:
        return address
    deref = deref_info(expr, ctx)
    if deref is not None:
        pointer_c, target_type, label = deref
        if target_type == "void":
            raise ZyenError(f"cannot dereference `ptr<void>` `{label}`; cast it to a concrete ptr<T> first")
        if is_fn_type(target_type):
            signature = json.dumps(target_type.replace(" ", ""))
            return f"zl_fn_ptr_load({pointer_c}, {signature}, {json.dumps(label)})"
        return f"(*({c_type(target_type)}*)zl_ptr_checked_addr({pointer_c}, {json.dumps(label)}))"
    if is_array_literal(expr):
        values, _element_type = parse_array_literal(expr, ctx, 0)
        if not values:
            return "zl_list_new()"
        wrapped = [wrap_arg_for_expected(value, "Any", ctx) for value in values]
        return f"zl_list_from_array((Any[]){{{', '.join(wrapped)}}}, {len(wrapped)})"
    expr = convert_struct_literal(expr, ctx)
    expr = replace_fstrings_in_expr(expr, ctx)
    # String concat must run before object/method rewriting, so expressions like
    # `"x=" + (str)list.get(0)` still infer the right side as str.
    expr = transform_string_concat(expr, ctx)
    expr = transform_function_calls(expr, ctx)
    expr = transform_method_calls(expr, ctx)
    expr = transform_field_access(expr, ctx)
    expr = transform_ptr_index(expr, ctx)
    expr = transform_checked_int_arithmetic(expr, ctx)
    return expr


def is_array_literal(expr: str) -> bool:
    return expr.strip().startswith("[") and expr.strip().endswith("]")


def parse_array_literal(expr: str, ctx: TranspileContext, line_no: int) -> Tuple[List[str], str]:
    """Parse `[1, 2, "hi"]` as a List literal.

    List is heterogeneous: each element is stored in an internal dynamic cell.
    `Any` is not a user-facing type; it is only the runtime representation.
    """
    text = expr.strip()
    if not is_array_literal(text):
        raise ZyenError(f"line {line_no}: internal error: expected list literal")
    body = text[1:-1].strip()
    values = split_args(body) if body else []
    return values, "Any"


def named_list_receiver(expr: str) -> Optional[str]:
    receiver = strip_outer_parens(expr.strip())
    return receiver if re.fullmatch(r"[A-Za-z_]\w*", receiver) else None


def list_item_types_for_receiver(expr: str, ctx: TranspileContext) -> Optional[Set[str]]:
    name = named_list_receiver(expr)
    if name is None or name not in ctx.list_item_types:
        return None
    item_types = ctx.list_item_types[name]
    return None if item_types is None else set(item_types)


def record_list_item_type(receiver: str, value_expr: str, ctx: TranspileContext) -> None:
    name = named_list_receiver(receiver)
    if name is None or name not in ctx.list_item_types or ctx.list_item_types[name] is None:
        return
    value_type = infer_type(value_expr, ctx)
    if struct_union_types(value_type) is not None or ztype_base(value_type) == "Any":
        ctx.list_item_types[name] = None
        return
    ctx.list_item_types[name].add(value_type)


def precollect_function_list_item_types(
    lines: List[Tuple[int, str]], start_i: int, ctx: TranspileContext
) -> None:
    """Conservatively collect local List element types before body emission.

    This gives a local literal such as `[Dog {}, Cat {}]` a hidden closed set
    without adding a public union/interface syntax. Plain List parameters keep
    an erased set and therefore use runtime-checked structural dispatch.
    """
    saved_symbols = ctx.symbols
    saved_ptr_targets = ctx.ptr_targets
    scan_symbols = dict(saved_symbols)
    ctx.symbols = scan_symbols
    ctx.ptr_targets = dict(saved_ptr_targets)
    depth = 1
    i = start_i
    try:
        while i < len(lines) and depth > 0:
            _line_no, line = lines[i]
            nested = parse_fn_header_line(line)
            if nested and nested[3] == "defn":
                i = skip_block(lines, i)
                continue

            parsed = parse_variable_declaration_syntax(
                line, trailing_semicolon=True
            ) if line.endswith(";") else None
            if parsed is None and line.endswith(";"):
                parsed = parse_variable_declaration_syntax(
                    line, trailing_semicolon=True, owned_pointer=True
                )
            if parsed is not None:
                _kind, name, explicit_type, expr = parsed
                explicit_type = _strip_module_prefix_type(explicit_type) if explicit_type else None
                inferred = explicit_type or (infer_type(expr, ctx) if expr else "int")
                scan_symbols[name] = ztype_base(inferred)
                pointer_inner = ptr_inner_type(inferred)
                if pointer_inner is not None:
                    ctx.ptr_targets[name] = pointer_inner
                if (explicit_type and ztype_base(explicit_type) == "List") or (expr and is_array_literal(expr)):
                    if expr and is_array_literal(expr):
                        values, _ = parse_array_literal(expr, ctx, 0)
                        item_types: Set[str] = set()
                        unknown = False
                        for value in values:
                            value_type = infer_type(value, ctx)
                            if struct_union_types(value_type) is not None or ztype_base(value_type) == "Any":
                                unknown = True
                                break
                            item_types.add(value_type)
                        ctx.list_item_types[name] = None if unknown else item_types
                    elif expr and named_list_receiver(expr) in ctx.list_item_types:
                        source_types = ctx.list_item_types[named_list_receiver(expr)]
                        ctx.list_item_types[name] = None if source_types is None else set(source_types)
                    elif expr and expr != "List":
                        # A List returned by a function or another opaque
                        # expression has elements, but no local closed set.
                        ctx.list_item_types[name] = None
                    else:
                        ctx.list_item_types[name] = set()

            mutation = re.match(
                r"^([A-Za-z_]\w*)\.(append|append_ptr|set)\s*\((.*)\)\s*;\s*$",
                line,
            )
            if mutation and mutation.group(1) in ctx.list_item_types:
                args = split_args(mutation.group(3))
                value_index = 1 if mutation.group(2) == "set" else 0
                if len(args) > value_index:
                    record_list_item_type(mutation.group(1), args[value_index], ctx)

            # Passing a local List through a plain List parameter erases its
            # hidden element set because the callee may mutate it.
            statement_expr = line[:-1].strip() if line.endswith(";") else ""
            direct_call = split_trailing_call(statement_expr) if statement_expr else None
            if direct_call is not None:
                callee, arg_text = direct_call
                if re.fullmatch(r"[A-Za-z_]\w*", callee) and callee in ctx.functions:
                    fn = ctx.functions[callee]
                    args = split_args(arg_text)
                    for arg, param_type in zip(args, fn.params.values()):
                        list_name = named_list_receiver(arg)
                        if ztype_base(param_type) == "List" and list_name in ctx.list_item_types:
                            ctx.list_item_types[list_name] = None

            depth += line.count("{") - line.count("}")
            i += 1
    finally:
        ctx.symbols = saved_symbols
        ctx.ptr_targets = saved_ptr_targets


def infer_type(expr: str, ctx: TranspileContext) -> str:
    expr = expr.strip()
    reject_malformed_pointer_cast(expr)
    raw = expr
    if is_fstring_expr(raw):
        return "str"
    # Cast expression: (str)i, (int)s, (float)x, (bool)v.
    cast = is_cast_expr(raw)
    if cast:
        return "str" if cast[0] == "char" else cast[0]
    trailing_call = split_trailing_call(raw)
    if trailing_call is not None and not trailing_call[0].lstrip().startswith(("*", "&")):
        callee, _arg_text = trailing_call
        callee_type = infer_type(callee, ctx)
        if is_fn_type(callee_type):
            _params, ret_type = parse_fn_type(callee_type)
            return ret_type
        if ztype_base(callee_type) == "ptr":
            raise ZyenError(
                f"cannot call pointer `{callee}` of type `{display_ztype(callee_type, ctx)}`; "
                f"postfix call binds before `*`, so write `(*{callee})(...)`"
            )
    # Remove outer parentheses for simple cases.
    while raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip()
    if is_array_literal(raw):
        return "List"
    sm_lit = re.match(r"^(?:[A-Za-z_]\w*\.)?([A-Z][A-Za-z_]\w*)\s*\{", raw)
    if sm_lit and sm_lit.group(1) in ctx.structs:
        return sm_lit.group(1)
    # A nested expression may already have passed through
    # convert_struct_literal(), for example the argument of List.append().
    # Preserve its source type while the outer method call is transformed.
    c_sm_lit = re.match(r"^\(([A-Z][A-Za-z_]\w*)\)\s*\{", raw)
    if c_sm_lit and c_sm_lit.group(1) in ctx.structs:
        return c_sm_lit.group(1)
    if ptr_constructor_expr(raw) is not None:
        return "ptr"
    if raw == "List":
        return "List"
    if is_none_literal(raw):
        return "none"
    if re.match(r'^".*"$', raw):
        return "str"
    plus = find_top_level_plus(raw)
    if plus:
        left, right = plus
        if ztype_base(infer_type(left, ctx)) == "str" or ztype_base(infer_type(right, ctx)) == "str":
            return "str"
    if raw in {"true", "false"}:
        return "bool"
    if re.match(r"^-?\d+$", raw):
        return "int"
    if re.match(r"^-?\d+\.\d*([eE][+-]?\d+)?$", raw) or re.match(r"^-?\d+[eE][+-]?\d+$", raw):
        return "float"
    if raw.endswith(".addr"):
        return "ptraddr"
    field_type = field_access_type(raw, ctx)
    if field_type is not None:
        return field_type
    address_operand = address_of_operand(raw)
    if address_operand is not None:
        deref_operand = unary_deref_operand(address_operand)
        if deref_operand is not None:
            pointer_type = infer_type(strip_outer_parens(deref_operand), ctx)
            if ztype_base(pointer_type) != "ptr":
                raise ZyenError(f"cannot take the address of `{address_operand}`")
            return pointer_type
        return f"ptr<{infer_type(address_operand, ctx)}>"
    deref_operand = unary_deref_operand(raw)
    if deref_operand is not None:
        pointer_expr = strip_outer_parens(deref_operand)
        pointer_type = infer_type(pointer_expr, ctx)
        if ztype_base(pointer_type) != "ptr":
            raise ZyenError(f"cannot dereference non-pointer expression `{pointer_expr}`")
        return ptr_inner_type(pointer_type) or "void"
    im = re.match(r"^([A-Za-z_]\w*)\s*\[.*\]$", raw)
    if im and im.group(1) in ctx.ptr_targets:
        return ctx.ptr_targets.get(im.group(1), "int")
    found_mcall = split_method_call_at(raw, 0)
    if found_mcall and found_mcall[0] == 0 and found_mcall[1] == len(raw):
        _start, _end, obj, method, _body = found_mcall
        obj_type = infer_type(obj, ctx)
        if obj_type == "List":
            if method in {"get", "pop"}:
                item_types = list_item_types_for_receiver(obj, ctx)
                if item_types is not None:
                    return make_struct_union_type(item_types)
            return {
                "append": "void",
                "set": "void",
                "clear": "void",
                "len": "int",
                "is_empty": "bool",
                "get": "Any",
                "ptr": "ptr",
                "append_ptr": "ptr",
                "pop": "Any",
            }.get(method, "int")
        union_types = struct_union_types(obj_type)
        if union_types is not None or ztype_base(obj_type) == "Any":
            fn, _candidate_names = resolve_structural_method(union_types, method, ctx)
            return fn.ret_type
        if obj_type in ctx.structs:
            struct = ctx.structs[obj_type]
            if method in struct.fields and is_fn_type(struct.fields[method]):
                _params, ret_type = parse_fn_type(struct.fields[method])
                return ret_type
            if method in struct.methods:
                return struct.methods[method].ret_type
        owner = ptrstruct_inner_type(obj_type) if obj_type else None
        if owner in ctx.structs:
            struct = ctx.structs[owner]
            if method in struct.fields and is_fn_type(struct.fields[method]):
                _params, ret_type = parse_fn_type(struct.fields[method])
                return ret_type
            if method in struct.methods:
                return struct.methods[method].ret_type
    fm = re.match(r"^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$", raw)
    if fm:
        var, field = fm.group(1), fm.group(2)
        stype = ctx.symbols.get(var)
        owner = ptrstruct_inner_type(stype) if stype else None
        if stype in ctx.structs and field in ctx.structs[stype].fields:
            return ctx.structs[stype].fields[field]
        if owner in ctx.structs and field in ctx.structs[owner].fields:
            return ctx.structs[owner].fields[field]
    call = re.match(r"^([A-Za-z_]\w*)\s*\(.*\)$", raw)
    if call and call.group(1) in ctx.functions:
        return ctx.functions[call.group(1)].ret_type
    if call and call.group(1) in ctx.native_function_types:
        _native_params, native_ret = parse_fn_type(ctx.native_function_types[call.group(1)])
        return native_ret
    if call:
        runtime_returns = {
            "zl_mem_alloc_int": "ptr<int>",
            "zl_mem_alloc_float": "ptr<float>",
            "zl_mem_alloc_bool": "ptr<bool>",
            "zl_mem_alloc_str": "ptr<str>",
            "zl_mem_alloc_any": "ptr<Any>",
        }
        if call.group(1) in runtime_returns:
            return runtime_returns[call.group(1)]
    if call:
        # ZEP-0010: call through a fn-typed local variable.
        callee = call.group(1)
        var_type = ctx.symbols.get(callee)
        if var_type and is_fn_type(var_type):
            _ps, ret = parse_fn_type(var_type)
            return ret
    if raw in ctx.symbols:
        symbol_type = ctx.symbols[raw]
        if ztype_base(symbol_type) == "ptr" and raw in ctx.ptr_targets:
            return f"ptr<{ctx.ptr_targets[raw]}>"
        return symbol_type
    generated_fnval = re.fullmatch(r"([A-Za-z_]\w*)_zlfnval", raw)
    if generated_fnval and generated_fnval.group(1) in ctx.functions:
        fn = ctx.functions[generated_fnval.group(1)]
        return f"fn({','.join(fn.params.values())})->{fn.ret_type}"
    # ZEP-0010: bare named-function reference is an fn-typed value.
    if raw in ctx.functions:
        fn = ctx.functions[raw]
        param_types = list(fn.params.values())
        return f"fn({','.join(param_types)})->{fn.ret_type}"
    comparison_probe = raw.replace("<<", "").replace(">>", "")
    if any(op in comparison_probe for op in ["==", "!=", "<=", ">=", "<", ">", "&&", "||"]):
        return "bool"
    # Try top-level arithmetic before falling back to the dot heuristic:
    # if every operand of `+ - * /` independently infers to int, the result
    # is int. This avoids mis-flagging `xs.len() - 1` as float.
    arith_pieces = _split_top_level_arith(raw)
    if arith_pieces is not None and len(arith_pieces) >= 2:
        operand_types = [infer_type(piece, ctx) for piece in arith_pieces]
        if any(ztype_base(operand_type) == "ptr" for operand_type in operand_types):
            raise ZyenError(
                f"pointer arithmetic is not supported for managed `ZL_ptr` in `{raw}`; "
                "keep the same address when casting, use typed indexing for an actual array, "
                "or adapt raw pointer arithmetic inside the C compatibility layer"
            )
        if all(t == "int" for t in operand_types):
            return "int"
        if any(t == "float" for t in operand_types) and all(t in {"int", "float"} for t in operand_types):
            return "float"
    if "." in raw:
        return "float"
    for name, typ in ctx.symbols.items():
        if re.search(rf"\b{re.escape(name)}\b", raw) and typ == "float":
            return "float"
    return "int"


def c_print(expr: str, ctx: TranspileContext, line_no: int) -> str:
    raw_expr = expr.strip()
    typ = infer_type(raw_expr, ctx)
    dm = re.match(r"^\*\s*([A-Za-z_]\w*)$", raw_expr)
    if dm and ctx.ptr_targets.get(dm.group(1)) == "void":
        raise ZyenError(f"cannot dereference `ptr<void>` `{dm.group(1)}`; cast it to a concrete ptr<T> first")
    if typ == "void":
        raise ZyenError(f"line {line_no}: print expects `str`, got `void`")
    if ztype_base(typ) != "str":
        raise ZyenError(
            f"line {line_no}: print expects `str`, got `{typ}`; "
            'use `print((str)value);` or `print(f"{value}");`'
        )
    return f'printf("%s\\n", {transform_expr(raw_expr, ctx)});'



def type_has_managed_value(ztype: str, ctx: TranspileContext, seen: Optional[Set[str]] = None) -> bool:
    normalized = ztype.replace(" ", "")
    if is_fn_type(normalized) or ztype_base(normalized) == "ptr":
        return True
    if normalized not in ctx.structs:
        return False
    visited = set() if seen is None else set(seen)
    if normalized in visited:
        return False
    visited.add(normalized)
    return any(type_has_managed_value(field_type, ctx, visited) for field_type in ctx.structs[normalized].fields.values())


def managed_struct_helper_name(ztype: str, suffix: str) -> str:
    return f"{re.sub(r'[^A-Za-z0-9_]', '_', ztype)}_zl_{suffix}"


def retain_expr_for_type(expr_c: str, ztype: str, ctx: TranspileContext) -> str:
    if is_fn_type(ztype):
        return f"zl_fn_retain({expr_c})"
    if ztype_base(ztype) == "ptr":
        return f"zl_ptr_retain({expr_c})"
    if ztype in ctx.structs and type_has_managed_value(ztype, ctx):
        return f"{managed_struct_helper_name(ztype, 'retain_value')}({expr_c})"
    return expr_c


def release_statement_for_type(expr_c: str, ztype: str, ctx: TranspileContext) -> Optional[str]:
    if is_fn_type(ztype):
        return f"zl_fn_release({expr_c});"
    if ztype_base(ztype) == "ptr":
        return f"zl_ptr_release({expr_c});"
    if ztype in ctx.structs and type_has_managed_value(ztype, ctx):
        return f"{managed_struct_helper_name(ztype, 'release')}(&{expr_c});"
    return None


def cell_drop_function_for_type(ztype: str, ctx: TranspileContext) -> Optional[str]:
    if is_fn_type(ztype):
        return "zl_mem_drop_fn_cell"
    if ztype_base(ztype) == "ptr":
        return "zl_mem_drop_ptr_cell"
    if ztype in ctx.structs and type_has_managed_value(ztype, ctx):
        return managed_struct_helper_name(ztype, "drop_cell")
    return None


def managed_cell_alloc_expr(ztype: str, ctx: TranspileContext) -> str:
    ctyp = c_type(ztype)
    runtime_type = type_name_for_runtime(ztype)
    drop = cell_drop_function_for_type(ztype, ctx)
    if drop:
        return f'zl_mem_alloc_cell_drop(sizeof({ctyp}), "{runtime_type}", {drop})'
    return f'zl_mem_alloc_cell(sizeof({ctyp}), "{runtime_type}")'


def owned_pointer_factory_name(target_type: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]", "_", target_type.replace(" ", ""))
    return f"zl_owned_ptr_from_{slug}"


def owned_pointer_initializer_expr(
    pointer_type: str,
    expr: str,
    label: str,
    ctx: TranspileContext,
    line_no: int = 0,
) -> str:
    """Create an owned ptr<T> expression from a pointee initializer.

    This is the expression form of `let *p: ptr<T> = value`. It is used by
    owned struct-field defaults, where statement-level hidden temporaries are
    unavailable inside a C designated initializer.
    """
    target_type = ptr_inner_type(pointer_type)
    if target_type is None:
        raise ZyenError(f"line {line_no}: owned pointer `{label}` needs a concrete `ptr<T>` type")
    actual_type = infer_type(expr, ctx)
    try:
        ensure_assignable(target_type, actual_type, line_no, f"initializing owned field `{label}`")
    except ZyenError:
        nested_target = ptr_inner_type(target_type)
        if nested_target is None or ztype_base(actual_type) == "ptr":
            raise
        nested_value = owned_pointer_initializer_expr(target_type, expr, label, ctx, line_no)
        return f"{owned_pointer_factory_name(target_type)}({nested_value})"

    assert_expr_literals_fit(expr, ctx, line_no, target_type)
    coerced_fn = coerce_named_fn_to_fnval(expr, target_type, ctx) if is_fn_type(target_type) else None
    value_c = coerced_fn if coerced_fn is not None else transform_expr(expr, ctx)
    if type_has_managed_value(target_type, ctx) and not expression_produces_owned_value(expr, ctx):
        value_c = retain_expr_for_type(value_c, target_type, ctx)
    return f"{owned_pointer_factory_name(target_type)}({value_c})"


def build_recursive_owned_ptr_value(expected_type: str, expr: str, name: str, ctx: TranspileContext, line_no: int) -> Tuple[List[str], str]:
    """Build a value for a managed cell, allocating nested ptr layers as needed."""
    actual_type = infer_type(expr, ctx)
    try:
        ensure_assignable(expected_type, actual_type, line_no, f"initializing `*{name}`")
    except ZyenError:
        inner_type = ptr_inner_type(expected_type)
        if inner_type is None or ztype_base(actual_type) == "ptr":
            raise
        inner_lines, inner_value = build_recursive_owned_ptr_value(inner_type, expr, name, ctx, line_no)
        ctx.auto_ptr_counter += 1
        hidden = f"__zy_ptr_{name}_nested_{ctx.auto_ptr_counter}"
        lines = list(inner_lines)
        lines.append(f"ptr {hidden} = {managed_cell_alloc_expr(inner_type, ctx)};")
        lines.append(f"(*({c_type(inner_type)}*){hidden}.addr) = {inner_value};")
        return lines, hidden

    assert_expr_literals_fit(expr, ctx, line_no, expected_type)
    coerced_fn = coerce_named_fn_to_fnval(expr, expected_type, ctx) if is_fn_type(expected_type) else None
    value_c = coerced_fn if coerced_fn is not None else (wrap_arg_for_expected(expr, "Any", ctx) if ztype_base(expected_type) == "Any" else transform_expr(expr, ctx))
    if type_has_managed_value(expected_type, ctx) and not expression_produces_owned_value(expr, ctx):
        value_c = retain_expr_for_type(value_c, expected_type, ctx)
    return [], value_c


def scope_cleanup_lines(entries: List[Tuple[str, str]], ctx: TranspileContext) -> List[str]:
    lines: List[str] = []
    for name, ztype in reversed(entries):
        statement = release_statement_for_type(name, ztype, ctx)
        if statement:
            lines.append(statement)
    return lines


def all_owned_cleanup_lines(ctx: TranspileContext) -> List[str]:
    lines: List[str] = []
    for entries in reversed(ctx.owned_scope_stack):
        lines.extend(scope_cleanup_lines(entries, ctx))
    return lines


def expression_produces_owned_value(expr: str, ctx: TranspileContext) -> bool:
    """Whether a managed expression follows the owned-return convention."""
    raw = strip_outer_parens(expr.strip())
    cast = is_cast_expr(raw)
    if cast:
        if (ztype_base(cast[0]) == "ptr" or cast[0] in ctx.structs) and re.search(r"\.pop\s*\([^)]*\)\s*$", cast[1]):
            return True
        return expression_produces_owned_value(cast[1], ctx)
    if re.match(r"^(?:[A-Za-z_]\w*\.)?[A-Z][A-Za-z_]\w*\s*\{", raw):
        return True
    if re.match(r"^\([A-Z][A-Za-z_]\w*\)\s*\{", raw):
        return True
    trailing = split_trailing_call(raw)
    if trailing is None:
        return False
    callee, _args = trailing
    if re.fullmatch(r"[A-Za-z_]\w*", callee) and callee in ctx.functions:
        return type_has_managed_value(ctx.functions[callee].ret_type, ctx)
    callee_type = infer_type(callee, ctx)
    if is_fn_type(callee_type):
        _params, ret_type = parse_fn_type(callee_type)
        return type_has_managed_value(ret_type, ctx)
    result_type = infer_type(raw, ctx)
    return type_has_managed_value(result_type, ctx)


def declare_symbol(ctx: TranspileContext, name: str, typ: str, line_no: int, ptr_target: Optional[str] = None, is_const: bool = False, is_list_ref: bool = False, owns_value: bool = True) -> None:
    if not ctx.scope_stack:
        ctx.scope_stack = [set()]
    current = ctx.scope_stack[-1]
    if name in current:
        raise ZyenError(f"line {line_no}: variable `{name}` is already declared in this scope; use a new name or `set {name} = ...;`")
    current.add(name)
    ctx.symbols[name] = typ
    if ptr_target:
        ctx.ptr_targets[name] = ptr_target
    if is_const:
        ctx.consts.add(name)
    if is_list_ref:
        ctx.list_refs.add(name)
    managed_type = f"ptr<{ptr_target}>" if ztype_base(typ) == "ptr" and ptr_target else typ
    if owns_value and type_has_managed_value(managed_type, ctx):
        if not ctx.owned_scope_stack:
            ctx.owned_scope_stack = [[]]
        ctx.owned_scope_stack[-1].append((name, managed_type))


def push_scope(ctx: TranspileContext) -> None:
    ctx.scope_stack.append(set())
    ctx.owned_scope_stack.append([])


def pop_scope(ctx: TranspileContext) -> List[Tuple[str, str]]:
    if not ctx.scope_stack:
        return []
    names = ctx.scope_stack.pop()
    owned = ctx.owned_scope_stack.pop() if ctx.owned_scope_stack else []
    for name in names:
        ctx.symbols.pop(name, None)
        ctx.ptr_targets.pop(name, None)
        ctx.consts.discard(name)
        ctx.list_refs.discard(name)
        ctx.list_item_types.pop(name, None)
    return owned

def parse_owned_ptr_decl(line: str, ctx: TranspileContext, line_no: int, trailing_semicolon: bool = True) -> Optional[str]:
    """Parse pointer declarations with dereference initialization.

    Zyen syntax:
        let *a: ptr<int> = 1;
        set *a = 2;

    Meaning: create pointer `a`, allocate hidden local storage for one int,
    point `a` at that location, then write 1 into `*a`. Conceptually the
    pointer is still a location; the hidden storage only exists so `*a` has a
    valid place to write.

    If declared without an initializer:
        let *a: ptr<int>;
    then `a` is None, identical to `let a: ptr<int>;`.
    """
    ending = ";" if trailing_semicolon else ""
    parsed = parse_variable_declaration_syntax(line, trailing_semicolon=trailing_semicolon, owned_pointer=True)
    if parsed is None:
        return None
    kind, name, explicit_type, expr = parsed
    explicit_type = _strip_module_prefix_type(explicit_type) if explicit_type else None
    if expr is None:
        if not explicit_type:
            raise ZyenError(f"line {line_no}: `let *{name};` needs a concrete `ptr<T>` type")
        if explicit_type.startswith("prt"):
            raise ZyenError(f"line {line_no}: unknown type `{explicit_type}`; did you mean `ptr`?")
        validate_user_type(explicit_type, line_no, "ptr declaration")
        if ztype_base(explicit_type) != "ptr":
            raise ZyenError(f"line {line_no}: pointer declaration must use `ptr<T>`, for example `let *a: ptr<int>;`")
        target_type = ptr_inner_type(explicit_type)
        if not target_type:
            raise ZyenError(f"line {line_no}: bare `ptr` declarations are not allowed; use `ptr<int>`, `ptr<str>`, `ptr<bool>`, etc.")
        declare_symbol(ctx, name, "ptr", line_no, ptr_target=target_type, is_const=(kind == "const"))
        prefix = "const " if kind == "const" else ""
        return f'{prefix}ptr {name} = zl_none_ptr("{type_name_for_runtime(target_type)}"){ending}'
    if is_none_literal(expr):
        raise ZyenError(f"line {line_no}: `let *{name} ... = None;` has no location to write; use `let {name}: ptr<T>;` or `let {name}: ptr<T> = None;`")

    if explicit_type and explicit_type.startswith("prt"):
        raise ZyenError(f"line {line_no}: unknown type `{explicit_type}`; did you mean `ptr`?")

    if explicit_type:
        if ztype_base(explicit_type) != "ptr":
            raise ZyenError(f"line {line_no}: owned pointer declaration must use `ptr` or `ptr<T>`, for example `let *a: ptr = 1;` or `let *a: ptr<int> = 1;`")
        validate_user_type(explicit_type, line_no, "owned ptr")
        target_type = ptr_inner_type(explicit_type)
        # Bare `ptr` with an initializer is allowed, but it is no longer dynamic.
        # The target is inferred from the initializer: `let *a: ptr = 1;` -> ptr<int>.
        if not target_type:
            target_type = infer_type(expr, ctx)
            if ztype_base(target_type) == "Any":
                raise ZyenError(f"line {line_no}: cannot infer a concrete ptr target from dynamic List value; cast first")
        elif target_type == "ptr":
            actual_type = infer_type(expr, ctx)
            if ztype_base(actual_type) == "ptr" and ptr_inner_type(actual_type):
                target_type = actual_type
            elif ztype_base(actual_type) not in {"Any", "none", "void"}:
                target_type = f"ptr<{actual_type}>"
    else:
        target_type = infer_type(expr, ctx)

    hidden_name = f"__zy_ptr_{name}"
    nested_lines, stored_rhs = build_recursive_owned_ptr_value(target_type, expr, name, ctx, line_no)
    storage_lines = list(nested_lines)
    storage_lines.append(f"ptr {hidden_name} = {managed_cell_alloc_expr(target_type, ctx)};")
    storage_lines.append(f"(*({c_type(target_type)}*){hidden_name}.addr) = {stored_rhs};")
    storage_c = "\n".join(storage_lines)
    expr_c = hidden_name

    declare_symbol(ctx, name, "ptr", line_no, ptr_target=target_type, is_const=(kind == "const"))
    prefix = "const " if kind == "const" else ""
    return f"{storage_c}\n{prefix}ptr {name} = {expr_c}{ending}"


def parse_var_decl(line: str, ctx: TranspileContext, line_no: int, trailing_semicolon: bool = True) -> str:
    owned = parse_owned_ptr_decl(line, ctx, line_no, trailing_semicolon=trailing_semicolon)
    if owned is not None:
        return owned
    ending = ";" if trailing_semicolon else ""
    parsed = parse_variable_declaration_syntax(line, trailing_semicolon=trailing_semicolon)
    if parsed is None:
        raise ZyenError(f"line {line_no}: invalid declaration, expected `let name = value;` or `let name: type = value;`")
    kind, name, explicit_type, expr = parsed
    explicit_type = _strip_module_prefix_type(explicit_type) if explicit_type else None

    # Pointer variables may be declared first. They default to None.
    # Example: `let a: ptr<int>;` -> ptr a = None
    # ZEP-0010: also accepts fn types like `let f: fn(int)->int;` (defaults to NULL).
    if expr is None:
        if not explicit_type:
            raise ZyenError(f"line {line_no}: declaration `{name}` needs a type or initializer")
        if ctx.scope_stack and name in ctx.scope_stack[-1]:
            raise ZyenError(f"line {line_no}: variable `{name}` is already declared in this scope; use a new name or `set {name} = ...;`")
        if kind == "const":
            raise ZyenError(f"line {line_no}: const `{name}` needs an initializer; use `const {name}: {explicit_type} = value;`")
        if explicit_type.startswith("prt"):
            raise ZyenError(f"line {line_no}: unknown type `{explicit_type}`; did you mean `ptr`?")
        validate_user_type(explicit_type, line_no, "declaration")
        # ZEP-0010 / ZEP-0013: fn-typed declaration without initializer
        # defaults to a zero fat value (call=NULL, env=NULL).
        if is_fn_type(explicit_type):
            register_fn_typedef(ctx, explicit_type)
            declare_symbol(ctx, name, explicit_type, line_no)
            return f"{c_type(explicit_type)} {name} = zl_fn_none({json.dumps(explicit_type)}){ending}"
        base_type = ztype_base(explicit_type)

        if base_type == "ptr":
            target_type = ptr_inner_type(explicit_type)
            if not target_type:
                raise ZyenError(f"line {line_no}: bare `ptr` declarations are not allowed; use `ptr<int>`, `ptr<str>`, `ptr<bool>`, etc.")
            declare_symbol(ctx, name, "ptr", line_no, ptr_target=target_type)
            return f'ptr {name} = zl_none_ptr("{type_name_for_runtime(target_type)}"){ending}'

        defaults = {
            "int": "0",
            "float": "0.0",
            "bool": "false",
            "str": '""',
            "List": "zl_list_new()",
        }
        if base_type in defaults:
            if base_type == "List":
                ctx.list_item_types.setdefault(name, set())
            declare_symbol(ctx, name, base_type, line_no)
            return f"{c_type(base_type)} {name} = {defaults[base_type]}{ending}"

        if explicit_type in ctx.structs:
            declare_symbol(ctx, name, explicit_type, line_no)
            init = _struct_default_init(explicit_type, ctx)
            return f"{explicit_type} {name} = {init}{ending}"

        raise ZyenError(f"line {line_no}: declaration without value is not supported for `{explicit_type}`")

    if ctx.scope_stack and name in ctx.scope_stack[-1]:
        raise ZyenError(f"line {line_no}: variable `{name}` is already declared in this scope; use a new name or `set {name} = ...;`")

    # Pointer constructor value: `let b = ptr;` or `let b = ptr<int>;`
    # Creates a None pointer object. The target type may be inferred later by
    # the first `set *b = value;`.
    ptr_ctor_target = ptr_constructor_expr(expr)
    if ptr_ctor_target is not None:
        if ptr_ctor_target == "__bare_ptr__" and not explicit_type:
            raise ZyenError(f"line {line_no}: bare `ptr` constructor needs a target type; use `ptr<int>` or `let name: ptr<int> = None;`")
        if explicit_type and ztype_base(explicit_type) != "ptr":
            raise ZyenError(f"line {line_no}: `ptr` constructor can only initialize a ptr variable")
        if explicit_type:
            validate_user_type(explicit_type, line_no, "ptr constructor")
        target_type = ptr_inner_type(explicit_type) if explicit_type else ptr_ctor_target
        if target_type == "__bare_ptr__":
            target_type = ptr_inner_type(explicit_type)
        if not target_type:
            raise ZyenError(f"line {line_no}: cannot infer ptr target type; use `ptr<int>` or another concrete ptr<T>`")
        declare_symbol(ctx, name, "ptr", line_no, ptr_target=target_type, is_const=(kind == "const"))
        prefix = "const " if kind == "const" else ""
        return f'{prefix}ptr {name} = zl_none_ptr("{type_name_for_runtime(target_type)}"){ending}'

    if explicit_type and explicit_type.startswith("prt"):
        raise ZyenError(f"line {line_no}: unknown type `{explicit_type}`; did you mean `ptr`?")
    if explicit_type:
        validate_user_type(explicit_type, line_no, "declaration")
        if is_fn_type(explicit_type):
            register_fn_typedef(ctx, explicit_type)
    ptr_target = ptr_inner_type(explicit_type) if explicit_type else None
    if explicit_type:
        typ = ztype_base(explicit_type)
    else:
        typ = infer_type(expr, ctx)
        if is_fn_type(typ):
            register_fn_typedef(ctx, typ)
    if ztype_base(typ) == "ptr":
        ptr_target = ptr_target or ptr_inner_type(typ)
        typ = "ptr"

    if is_array_literal(expr):
        if explicit_type and ztype_base(explicit_type) != "List":
            raise ZyenError(f"line {line_no}: list literal `[ ... ]` can only initialize List, not `{explicit_type}`")
        values, _elem_type = parse_array_literal(expr, ctx, line_no)
        typ = "List"
        if name not in ctx.list_item_types:
            ctx.list_item_types[name] = {infer_type(value, ctx) for value in values}
        declare_symbol(ctx, name, typ, line_no, is_const=(kind == "const"))
        lines = [f"ZL_List {name} = zl_list_new();"]
        for value in values:
            assert_expr_literals_fit(value, ctx, line_no)
            lines.append(f"zl_list_append(&{name}, {wrap_arg_for_expected(value, 'Any', ctx)});")
        return "\n".join(lines)
    if is_none_literal(expr):
        if explicit_type and is_fn_type(explicit_type):
            typ = explicit_type
            expr_c = f"zl_fn_none({json.dumps(explicit_type)})"
        else:
            if typ not in {"ptr", "none"}:
                raise ZyenError(f"line {line_no}: None can only be assigned to ptr or fn variables")
            typ = "ptr"
            if not ptr_target:
                raise ZyenError(f"line {line_no}: None ptr needs a concrete type, e.g. `let p: ptr<int> = None;`")
            target_type = ptr_target
            ptr_target = target_type
            expr_c = f'zl_none_ptr("{type_name_for_runtime(target_type)}")'
    elif typ == "ptr" and expr.startswith("&"):
        if explicit_type:
            ensure_assignable(explicit_type, infer_type(expr, ctx), line_no, f"declaration of `{name}`")
        expr_c = transform_expr(expr, ctx)
    elif typ == "List" and expr == "List":
        typ = "List"
        ctx.list_item_types.setdefault(name, set())
        expr_c = "zl_list_new()"
    elif explicit_type and explicit_type in ctx.structs and expr == explicit_type:
        typ = explicit_type
        expr_c = _struct_default_init(explicit_type, ctx)
    else:
        if typ == "void":
            raise ZyenError(f"line {line_no}: cannot assign a void result to variable `{name}`")
        if ztype_base(typ) == "Any" and not explicit_type:
            raise ZyenError(f"line {line_no}: dynamic List value cannot be stored without a concrete cast; use `(int)`, `(str)`, `(bool)`, `(float)`, or print it directly")
        if explicit_type:
            actual_type = infer_type(expr, ctx)
            if (
                re.fullmatch(r"[A-Za-z_]\w*", expr)
                and expr in ctx.functions
                and ctx.functions[expr].ret_type.replace(" ", "") == explicit_type.replace(" ", "")
                and actual_type.replace(" ", "") != explicit_type.replace(" ", "")
            ):
                raise ZyenError(
                    f"line {line_no}: `{expr}` is the function itself with type `{actual_type}`; "
                    f"call `{expr}()` to obtain `{explicit_type}`"
                )
            ensure_c_module_assignable(
                explicit_type,
                actual_type,
                ctx,
                line_no,
                f"declaration of `{name}`",
            )
            ensure_assignable(explicit_type, actual_type, line_no, f"declaration of `{name}`")
        assert_expr_literals_fit(expr, ctx, line_no, typ)
        # ZEP-0013: bare named-fn RHS coerces to fat value.
        coerced_fn = coerce_named_fn_to_fnval(expr, explicit_type or "", ctx) if explicit_type else None
        if coerced_fn is None and explicit_type is None and is_fn_type(typ):
            coerced_fn = coerce_named_fn_to_fnval(expr, typ, ctx)
        expr_c = coerced_fn if coerced_fn is not None else transform_expr(expr, ctx)

    # If a ptr variable is initialized from an address, another ptr, or List.ptr(),
    # track the target type for later `*p` operations.
    if ztype_base(typ) == "ptr" and not ptr_target:
        if expr.startswith("&"):
            ptr_target = ptr_inner_type(infer_type(expr, ctx)) or "void"
        elif expr in ctx.ptr_targets:
            ptr_target = ctx.ptr_targets.get(expr, "void")
        elif re.match(r"^[A-Za-z_]\w*\.(ptr|append_ptr)\s*\(", expr):
            ptr_target = "Any"  # internal List cell
    managed_decl_type = f"ptr<{ptr_target}>" if ztype_base(typ) == "ptr" and ptr_target else typ
    initializer_is_owned = expression_produces_owned_value(expr, ctx)
    if typ in ctx.structs and expr == typ:
        initializer_is_owned = True
    if type_has_managed_value(managed_decl_type, ctx) and not initializer_is_owned:
        expr_c = retain_expr_for_type(expr_c, managed_decl_type, ctx)
    declare_symbol(ctx, name, typ, line_no, ptr_target=ptr_target, is_const=(kind == "const"))
    decl_type = c_type(typ)
    # Avoid invalid C like `const const char* z = ...` when Zyen `const`
    # is used with `str`, because `str` already maps to `const char*`.
    # For now `const z` means a read-only Zyen variable tracked by the
    # compiler, while the emitted C keeps the canonical storage type.
    if kind == "const" and decl_type.startswith("const "):
        return f"{decl_type} {name} = {expr_c}{ending}"
    prefix = "const " if kind == "const" else ""
    return f"{prefix}{decl_type} {name} = {expr_c}{ending}"



def ptr_constructor_expr(expr: str) -> Optional[str]:
    """Return inner type for `ptr` constructor expressions.

    Zyen pointer-object syntax:
        let b = ptr;       // b is a None ptr with unknown target type
        let b = ptr<int>;  // b is a None ptr<int>

    `ptr` is allowed as a value constructor here, not only as a type name.
    """
    expr = expr.strip()
    if expr == "ptr":
        return "__bare_ptr__"
    inner = ptr_inner_type(expr.replace(" ", ""))
    if inner is not None:
        return inner
    return None


def auto_storage_for_deref(name: str, target_type: str, rhs_c: str, rhs_is_owned: bool, ctx: TranspileContext) -> str:
    """Emit lazy storage for assigning through an empty pointer.

    This implements the intentionally simple Zyen rule:
        let b = ptr;
        set *b = 5;

    If `b` is None at that assignment, Zyen creates one hidden cell for b and
    then writes into it. This keeps the user-facing pointer syntax intuitive
    while the backend still generates ordinary C storage.
    """
    ctx.auto_ptr_counter += 1
    hidden = f"__zy_auto_ptr_{name}_{ctx.auto_ptr_counter}"
    ctyp = c_type(target_type)
    lhs_c = f"(*({ctyp}*)zl_ptr_checked_addr({name}, \"{name}\"))"
    assignment = managed_assignment_c(lhs_c, target_type, rhs_c, rhs_is_owned, ctx)
    return f"if ({name}.addr == NULL) {{ {name} = {managed_cell_alloc_expr(target_type, ctx)}; }}\n{assignment}"



def c_compound_assignment(lhs: str, op: str, rhs: str, ctx: TranspileContext, line_no: int) -> str:
    """Compile explicit compound assignments such as `set x += 1;`.

    ZyenLang keeps mutation visible. In normal statement position, compound
    assignment must use `set`: `set x += 1;`, `set x -= 1;`, etc. Bare forms
    like `x += 1;` are rejected by transform_statement(). The `for (...)` step
    field remains loop-control syntax and may still use C-like `i++` / `i += 1`.
    """
    lhs = lhs.strip()
    rhs = rhs.strip()
    const_name = re.split(r"[.\[]", lhs, 1)[0].replace("*", "").strip()
    if const_name in ctx.consts:
        raise ZyenError(f"line {line_no}: cannot modify const `{const_name}`")

    lhs_type = infer_type(lhs, ctx)
    rhs_type = infer_type(rhs, ctx)
    base = ztype_base(lhs_type)
    if base == "void":
        raise ZyenError(f"line {line_no}: cannot use compound assignment on void value `{lhs}`")
    if base == "List":
        raise ZyenError(f"line {line_no}: compound assignment is not supported for List")
    if base == "ptr":
        raise ZyenError(f"line {line_no}: compound assignment is not supported for ptr itself; use `set *p = ...` or `set *p += ...`")

    lhs_c = transform_expr(lhs, ctx)

    if base == "str":
        if op != "+=":
            raise ZyenError(f"line {line_no}: only `+=` is supported for str")
        rhs_c = c_cast_expr("str", rhs, ctx)
        return f"{lhs_c} = zl_str_join(2, {lhs_c}, {rhs_c});"

    if base == "int":
        ensure_assignable("int", rhs_type, line_no, f"compound assignment to `{lhs}`")
        assert_expr_literals_fit(rhs, ctx, line_no, "int")
        rhs_c = transform_expr(rhs, ctx)
        fn = {"+=": "zl_int_add", "-=": "zl_int_sub", "*=": "zl_int_mul", "/=": "zl_int_div"}[op]
        return f"{lhs_c} = {fn}({lhs_c}, {rhs_c});"

    if base == "float":
        if ztype_base(rhs_type) not in {"int", "float"}:
            raise ZyenError(f"line {line_no}: float compound assignment needs int or float right side")
        rhs_c = transform_expr(rhs, ctx)
        return f"{lhs_c} {op} {rhs_c};"

    raise ZyenError(f"line {line_no}: compound assignment is not supported for `{lhs_type}`")


def managed_assignment_c(lhs_c: str, lhs_type: str, rhs_c: str, rhs_is_owned: bool, ctx: TranspileContext) -> str:
    if not type_has_managed_value(lhs_type, ctx):
        return f"{lhs_c} = {rhs_c};"
    if not rhs_is_owned:
        if is_fn_type(lhs_type):
            return f"zl_fn_assign(&{lhs_c}, {rhs_c});"
        if ztype_base(lhs_type) == "ptr":
            return f"zl_ptr_assign(&{lhs_c}, {rhs_c});"
        return f"{managed_struct_helper_name(lhs_type, 'assign')}(&{lhs_c}, {rhs_c});"

    ctx.managed_temp_counter += 1
    temp = f"__zl_move_{ctx.managed_temp_counter}"
    release = release_statement_for_type(lhs_c, lhs_type, ctx)
    return f"{c_type(lhs_type)} {temp} = {rhs_c};\n{release}\n{lhs_c} = {temp};"

def parse_set(line: str, ctx: TranspileContext, line_no: int) -> str:
    cm = re.match(r"set\s+(.+?)\s*(\+=|-=|\*=|/=)\s*(.*);\s*$", line)
    if cm:
        return c_compound_assignment(cm.group(1), cm.group(2), cm.group(3), ctx, line_no)
    m = re.match(r"set\s+(.+?)\s*=\s*(.*);\s*$", line)
    if not m:
        raise ZyenError(f"line {line_no}: invalid assignment, expected `set name = value;` or `set name += value;`")
    lhs, rhs = m.group(1).strip(), m.group(2).strip()
    if ":" in lhs:
        declared_name = lhs.split(":", 1)[0].strip()
        raise ZyenError(
            f"line {line_no}: `set` only assigns an existing variable; "
            f"declare `{declared_name}` with `let {lhs} = {rhs};`"
        )
    const_name = re.split(r"[.\[]", lhs, 1)[0].replace("*", "").strip()
    if const_name in ctx.consts:
        raise ZyenError(f"line {line_no}: cannot modify const `{const_name}`")

    if re.fullmatch(r"[A-Za-z_]\w*", lhs):
        fn_type = ctx.symbols.get(lhs, "")
        if is_fn_type(fn_type):
            if is_none_literal(rhs):
                return f"zl_fn_clear(&{lhs});"
            ensure_assignable(fn_type, infer_type(rhs, ctx), line_no, f"assignment to `{lhs}`")
            coerced = coerce_named_fn_to_fnval(rhs, fn_type, ctx)
            rhs_c = coerced if coerced is not None else transform_expr(rhs, ctx)
            return managed_assignment_c(lhs, fn_type, rhs_c, expression_produces_owned_value(rhs, ctx), ctx)

    # Pointer assignment: None or address assignment.
    if re.match(r"^[A-Za-z_]\w*$", lhs) and ctx.symbols.get(lhs) == "ptr":
        target_type = ctx.ptr_targets.get(lhs, "void")
        if is_none_literal(rhs):
            return f'zl_ptr_release({lhs});\n{lhs} = zl_none_ptr("{type_name_for_runtime(target_type)}");'
        if rhs.startswith("&"):
            rhs_type = infer_type(rhs, ctx)
            ensure_assignable(f"ptr<{target_type}>", rhs_type, line_no, f"assignment to `{lhs}`")
            return f"zl_ptr_assign(&{lhs}, {transform_expr(rhs, ctx)});"

    # Dereference assignment. If the pointer is None, Zyen lazily creates one
    # hidden cell for it. This makes this intuitive pattern valid:
    #     let b = ptr;
    #     set *b = 5;
    dm = re.match(r"^\*\s*([A-Za-z_]\w*)$", lhs)
    if dm and dm.group(1) in ctx.ptr_targets:
        name = dm.group(1)
        rhs_type = infer_type(rhs, ctx)
        target_type = ctx.ptr_targets.get(name, "void")
        if target_type == "void":
            raise ZyenError(f"line {line_no}: pointer `{name}` has no target type; declare it as `ptr<int>`, `ptr<str>`, etc.")
        ensure_assignable(target_type, rhs_type, line_no, f"assignment to `*{name}`")
        assert_expr_literals_fit(rhs, ctx, line_no, target_type)
        coerced_fn = coerce_named_fn_to_fnval(rhs, target_type, ctx) if is_fn_type(target_type) else None
        rhs_c = coerced_fn if coerced_fn is not None else (wrap_arg_for_expected(rhs, "Any", ctx) if ztype_base(target_type) == "Any" else transform_expr(rhs, ctx))
        return auto_storage_for_deref(name, target_type, rhs_c, expression_produces_owned_value(rhs, ctx), ctx)

    typed_deref = deref_info(lhs, ctx)
    if typed_deref is not None:
        pointer_c, target_type, label = typed_deref
        if target_type == "void":
            raise ZyenError(f"line {line_no}: cannot assign through `ptr<void>`; cast `{label}` to a concrete ptr<T> first")
        ensure_assignable(target_type, infer_type(rhs, ctx), line_no, f"assignment through `{lhs}`")
        coerced_fn = coerce_named_fn_to_fnval(rhs, target_type, ctx) if is_fn_type(target_type) else None
        rhs_c = coerced_fn if coerced_fn is not None else (wrap_arg_for_expected(rhs, "Any", ctx) if ztype_base(target_type) == "Any" else transform_expr(rhs, ctx))
        lhs_c = f"(*({c_type(target_type)}*)zl_ptr_checked_addr({pointer_c}, {json.dumps(label)}))"
        return managed_assignment_c(lhs_c, target_type, rhs_c, expression_produces_owned_value(rhs, ctx), ctx)

    if lhs.startswith("*"):
        name = lhs[1:].strip()
        raise ZyenError(f"line {line_no}: `{name}` is not a ptr; declare it with `let {name} = ptr;` or `let {name}: ptr<T>;`")

    expected = ctx.symbols.get(const_name, "")
    assert_expr_literals_fit(rhs, ctx, line_no, expected)
    lhs_type = infer_type(lhs, ctx)
    rhs_type = infer_type(rhs, ctx)
    ensure_c_module_assignable(lhs_type, rhs_type, ctx, line_no, f"assignment to `{lhs}`")
    # ZEP-0013: bare named-fn RHS on fn-typed LHS coerces to fat value.
    lhs_type = infer_type(lhs, ctx)
    coerced_fn = coerce_named_fn_to_fnval(rhs, lhs_type, ctx) if lhs_type else None
    rhs_c = coerced_fn if coerced_fn is not None else transform_expr(rhs, ctx)
    return managed_assignment_c(transform_expr(lhs, ctx), lhs_type, rhs_c, expression_produces_owned_value(rhs, ctx), ctx)


def parse_for(line: str, ctx: TranspileContext, line_no: int) -> str:
    m = re.match(r"for\s*\((.*)\)\s*\{\s*$", line)
    if not m:
        raise ZyenError(f"line {line_no}: invalid for loop")
    inside = m.group(1).strip()
    if inside == ";;":
        return "for (;;) {"
    parts = inside.split(";")
    if len(parts) != 3:
        raise ZyenError(f"line {line_no}: for loop must look like `for (init; condition; step) {{`")
    init, cond, step = [p.strip() for p in parts]
    if init.startswith("let ") or init.startswith("const "):
        init_c = parse_var_decl(init, ctx, line_no, trailing_semicolon=False)
    else:
        init_c = transform_expr(init, ctx)
    if step.startswith("set "):
        step_c = parse_set(step + ";", ctx, line_no).rstrip(";")
    else:
        step_c = transform_expr(step, ctx)
    return f"for ({init_c}; {transform_expr(cond, ctx)}; {step_c}) {{"


def transform_statement(line: str, ctx: TranspileContext, line_no: int) -> str:
    if line == "pass;":
        return ";"
    if line == "break;":
        if ctx.loop_depth <= 0:
            raise ZyenError(f"line {line_no}: `break;` can only be used inside `for` loops")
        return "break;"
    if line == "continue;":
        if ctx.loop_depth <= 0:
            raise ZyenError(f"line {line_no}: `continue;` can only be used inside `for` loops")
        return "continue;"
    if line.startswith("while"):
        raise ZyenError(f"line {line_no}: `while` is intentionally not part of ZyenLang v0.1; use `for` instead")
    if line.startswith("let ") or line.startswith("const "):
        return parse_var_decl(line, ctx, line_no)
    if line.startswith("set "):
        return parse_set(line, ctx, line_no)
    if line.startswith("return"):
        m = re.match(r"return(?:\s+(.*))?;\s*$", line)
        if not m:
            raise ZyenError(f"line {line_no}: return statement must end with `;`")
        expr = (m.group(1) or "").strip()
        if not expr:
            cleanup = all_owned_cleanup_lines(ctx)
            return "\n".join(cleanup + ["return;"])
        # ZEP-0013: bare named-fn returned where ret type is fn type → fat value.
        ret_type = ctx.current_return_type
        returned_address = re.fullmatch(r"&\s*([A-Za-z_]\w*)", expr)
        if ztype_base(ret_type) == "ptr" and returned_address:
            addressed_name = returned_address.group(1)
            if addressed_name not in ctx.functions or addressed_name in ctx.symbols:
                raise ZyenError(f"line {line_no}: cannot return a pointer to local stack storage")
        actual_type = infer_type(expr, ctx)
        # The legacy scalar inference pass intentionally falls back to int for
        # some valid unary/module expressions. Managed returns need exact
        # ownership and ABI information, so enforce those here without
        # regressing existing scalar programs.
        if type_has_managed_value(ret_type, ctx) or ret_type in ctx.structs:
            ensure_assignable(ret_type, actual_type, line_no, "return value")
        coerced_fn = coerce_named_fn_to_fnval(expr, ret_type, ctx) if ret_type else None
        value_c = coerced_fn if coerced_fn is not None else transform_expr(expr, ctx)
        if type_has_managed_value(ret_type, ctx) and not expression_produces_owned_value(expr, ctx):
            value_c = retain_expr_for_type(value_c, ret_type, ctx)
        cleanup = all_owned_cleanup_lines(ctx)
        if not cleanup:
            return f"return {value_c};"
        ctx.managed_temp_counter += 1
        temp = f"__zl_return_{ctx.managed_temp_counter}"
        return "\n".join([f"{c_type(ret_type)} {temp} = {value_c};"] + cleanup + [f"return {temp};"])
    pm = re.match(r"print\s*\((.*)\)\s*;\s*$", line)
    if pm:
        print_args = split_args(pm.group(1)) if pm.group(1).strip() else []
        if len(print_args) != 1:
            raise ZyenError(f"line {line_no}: print expects exactly 1 `str` argument, got {len(print_args)}")
        return c_print(print_args[0], ctx, line_no)
    sm = re.match(r"stop\s+(.+);\s*$", line)
    if sm:
        msg = transform_expr(sm.group(1), ctx)
        return f'fprintf(stderr, "%s\\n", {msg}); exit(1);'
    cm = re.match(r"^\s*(\*?\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[[^\]]+\])?)\s*(\+=|-=|\*=|/=)\s*(.*);\s*$", line)
    if cm:
        raise ZyenError(
            f"line {line_no}: bare compound assignment is not allowed; use `set {cm.group(1).strip()} {cm.group(2)} {cm.group(3).strip()};`"
        )
    if re.match(r"^[A-Za-z_]\w*\s*=", line):
        raise ZyenError(f"line {line_no}: bare assignment is not allowed; use `let`, `const`, or `set`")
    if not line.endswith(";"):
        raise ZyenError(f"line {line_no}: statement must end with `;`")
    return transform_expr(line[:-1].strip(), ctx) + ";"


def c_param_type(ztype: str) -> str:
    if ztype_base(ztype) == "List":
        return "ZL_List*"
    return c_type(ztype)


def c_function_signature(name: str, ret_type: str, params: Dict[str, str]) -> str:
    param_c = ", ".join(f"{c_param_type(t)} {n}" for n, t in params.items())
    if not param_c:
        param_c = "void"
    return f"{c_type(ret_type)} {name}({param_c})"


def emit_struct_forward_decls(ctx: TranspileContext) -> List[str]:
    """Emit `typedef struct X X;` for every struct.

    These let fn typedefs reference struct types whose full layout (with maybe
    fn-typed fields) is emitted later.
    """
    out: List[str] = []
    for struct in ctx.structs.values():
        if struct.name in ctx.external_structs:
            continue
        out.append(f"typedef struct {struct.name} {struct.name};")
    if out:
        out.append("")
    return out


def emit_struct_defs(ctx: TranspileContext) -> List[str]:
    out: List[str] = []
    for struct in ctx.structs.values():
        if struct.name in ctx.external_structs:
            continue
        out.append(f"struct {struct.name} {{")
        if struct.fields:
            for field_name, field_type in struct.fields.items():
                out.append(f"    {c_type(field_type)} {field_name};")
        else:
            # ISO C has no empty structs. Keep zero-field Zyen structs portable
            # across gcc, clang, Zig cc, and MSVC-compatible frontends.
            out.append("    unsigned char _zl_empty;")
        out.append(f"}};")
        out.append("")
    return out


def emit_struct_management(ctx: TranspileContext) -> List[str]:
    """Emit recursive ARC helpers for structs that contain managed fields."""
    managed = [struct for struct in ctx.structs.values() if type_has_managed_value(struct.name, ctx)]
    if not managed:
        return []
    out: List[str] = []
    for struct in managed:
        retain_name = managed_struct_helper_name(struct.name, "retain_value")
        release_name = managed_struct_helper_name(struct.name, "release")
        assign_name = managed_struct_helper_name(struct.name, "assign")
        drop_name = managed_struct_helper_name(struct.name, "drop_cell")
        out.append(f"static inline {struct.name} {retain_name}({struct.name} value);")
        out.append(f"static inline void {release_name}({struct.name}* value);")
        out.append(f"static inline void {assign_name}({struct.name}* target, {struct.name} value);")
        out.append(f"static inline void {drop_name}(void* payload);")
    out.append("")
    for struct in managed:
        retain_name = managed_struct_helper_name(struct.name, "retain_value")
        release_name = managed_struct_helper_name(struct.name, "release")
        assign_name = managed_struct_helper_name(struct.name, "assign")
        drop_name = managed_struct_helper_name(struct.name, "drop_cell")
        out.append(f"static inline {struct.name} {retain_name}({struct.name} value) {{")
        for field_name, field_type in struct.fields.items():
            if type_has_managed_value(field_type, ctx):
                out.append(f"    value.{field_name} = {retain_expr_for_type(f'value.{field_name}', field_type, ctx)};")
        out.append("    return value;")
        out.append("}")
        out.append(f"static inline void {release_name}({struct.name}* value) {{")
        out.append("    if (!value) return;")
        for field_name, field_type in reversed(list(struct.fields.items())):
            statement = release_statement_for_type(f"value->{field_name}", field_type, ctx)
            if statement:
                out.append(f"    {statement}")
        out.append(f"    *value = ({struct.name}){{0}};")
        out.append("}")
        out.append(f"static inline void {assign_name}({struct.name}* target, {struct.name} value) {{")
        out.append("    if (!target) return;")
        out.append(f"    {struct.name} retained = {retain_name}(value);")
        out.append(f"    {release_name}(target);")
        out.append("    *target = retained;")
        out.append("}")
        out.append(f"static inline void {drop_name}(void* payload) {{")
        out.append("    if (!payload) return;")
        out.append(f"    {release_name}(({struct.name}*)payload);")
        out.append("    free(payload);")
        out.append("}")
        out.append("")
    return out


def emit_owned_pointer_factories(ctx: TranspileContext) -> List[str]:
    """Emit expression-friendly allocators used by `let *this.field` defaults."""
    target_types: Set[str] = set()
    for struct in ctx.structs.values():
        for field_name in struct.owned_pointer_fields:
            target = ptr_inner_type(struct.fields[field_name])
            while target is not None:
                target_types.add(target)
                target = ptr_inner_type(target)
    if not target_types:
        return []

    ordered = sorted(target_types, key=lambda item: (item.count("ptr<"), item))
    out: List[str] = []
    for target_type in ordered:
        out.append(
            f"static inline ptr {owned_pointer_factory_name(target_type)}"
            f"({c_type(target_type)} value);"
        )
    out.append("")
    for target_type in ordered:
        name = owned_pointer_factory_name(target_type)
        out.append(f"static inline ptr {name}({c_type(target_type)} value) {{")
        out.append(f"    ptr result = {managed_cell_alloc_expr(target_type, ctx)};")
        out.append(f"    *(({c_type(target_type)}*)result.addr) = value;")
        out.append("    return result;")
        out.append("}")
        out.append("")
    return out


def emit_any_struct_box_helpers(ctx: TranspileContext) -> List[str]:
    """Emit ARC boxes used when a struct value enters a heterogeneous List."""
    out: List[str] = []
    for struct_name in sorted(ctx.structs):
        if struct_name in ctx.external_structs:
            continue
        managed = type_has_managed_value(struct_name, ctx)
        copy_value = (
            f"{managed_struct_helper_name(struct_name, 'retain_value')}(value)"
            if managed else "value"
        )
        drop = managed_struct_helper_name(struct_name, "drop_cell") if managed else "zl_ptr_default_drop"
        copy_name = any_struct_constructor_name(struct_name, False)
        take_name = any_struct_constructor_name(struct_name, True)
        for helper_name, stored_value in ((copy_name, copy_value), (take_name, "value")):
            out.append(f"static inline Any {helper_name}({struct_name} value) {{")
            out.append(f"    {struct_name}* box = ({struct_name}*)malloc(sizeof({struct_name}));")
            out.append('    if (!box) { fprintf(stderr, "List struct allocation failed\\n"); exit(1); }')
            out.append(f"    *box = {stored_value};")
            out.append(f"    ZL_ArcControl* owner = zl_arc_new(box, {drop});")
            out.append(
                f'    return (Any){{ .kind = ZL_VALUE_STRUCT, .object = box, '
                f'.object_type = "{struct_name}", .object_owner = owner }};'
            )
            out.append("}")
        out.append(f"static inline {struct_name} zl_any_cast_{struct_name}_borrow(Any value) {{")
        out.append(
            f'    if (value.kind != ZL_VALUE_STRUCT || !value.object || !value.object_type || '
            f'strcmp(value.object_type, "{struct_name}") != 0) '
            f'{{ fprintf(stderr, "List struct cast expected {struct_name}, got %s\\n", '
            f'value.object_type ? value.object_type : "non-struct"); exit(1); }}'
        )
        out.append(f"    return *(({struct_name}*)value.object);")
        out.append("}")
        out.append(f"static inline {struct_name} zl_any_cast_{struct_name}_take(Any value) {{")
        out.append(f"    {struct_name} result = zl_any_cast_{struct_name}_borrow(value);")
        if managed:
            out.append(f"    result = {managed_struct_helper_name(struct_name, 'retain_value')}(result);")
        out.append("    zl_any_release(value);")
        out.append("    return result;")
        out.append("}")
        out.append("")
    return out


def structural_method_groups(ctx: TranspileContext) -> Dict[Tuple[str, str], List[str]]:
    groups: Dict[Tuple[str, str], List[str]] = {}
    for struct_name, struct in ctx.structs.items():
        if struct_name in ctx.external_structs:
            continue
        for method, fn in struct.methods.items():
            groups.setdefault((method, struct_method_fn_type(fn)), []).append(struct_name)
    return groups


def emit_structural_dispatch_helpers(ctx: TranspileContext) -> List[str]:
    out: List[str] = []
    for (method, _signature), struct_names in sorted(structural_method_groups(ctx).items()):
        first_fn = ctx.structs[struct_names[0]].methods[method]
        helper = structural_dispatch_name(method, first_fn)
        params = list(first_fn.params.items())
        helper_params = ["Any value"] + [
            f"{c_param_type(ztype)} __zl_arg_{index}"
            for index, (_name, ztype) in enumerate(params)
        ]
        forwarded = "".join(f", __zl_arg_{index}" for index in range(len(params)))
        out.append(f"static inline {c_type(first_fn.ret_type)} {helper}({', '.join(helper_params)}) {{")
        out.append(
            f'    if (value.kind != ZL_VALUE_STRUCT || !value.object || !value.object_type) '
            f'{{ fprintf(stderr, "List value does not provide {method}()\\n"); exit(1); }}'
        )
        for struct_name in sorted(struct_names):
            out.append(f'    if (strcmp(value.object_type, "{struct_name}") == 0) {{')
            call = f"{struct_name}_{method}(({struct_name}*)value.object{forwarded})"
            if first_fn.ret_type == "void":
                out.append(f"        {call};")
                out.append("        return;")
            else:
                out.append(f"        return {call};")
            out.append("    }")
        out.append(
            f'    fprintf(stderr, "struct %s does not provide compatible {method}()\\n", '
            'value.object_type ? value.object_type : "unknown");'
        )
        out.append("    exit(1);")
        out.append("}")

        managed_mask = 1  # bit 0 releases an owned Any receiver such as List.pop().
        for index, (_name, ztype) in enumerate(params, start=1):
            if type_has_managed_value(ztype, ctx):
                managed_mask |= 1 << index
        subset = managed_mask
        while subset:
            wrapper = direct_owned_args_wrapper_name(helper, subset)
            call_args = ", ".join(["value"] + [f"__zl_arg_{index}" for index in range(len(params))])
            out.append(f"static inline {c_type(first_fn.ret_type)} {wrapper}({', '.join(helper_params)}) {{")
            if first_fn.ret_type == "void":
                out.append(f"    {helper}({call_args});")
            else:
                out.append(f"    {c_type(first_fn.ret_type)} result = {helper}({call_args});")
            for bit in range(len(params), 0, -1):
                if subset & (1 << bit):
                    _param_name, param_type = params[bit - 1]
                    statement = release_statement_for_type(f"__zl_arg_{bit - 1}", param_type, ctx)
                    if statement:
                        out.append(f"    {statement}")
            if subset & 1:
                out.append("    zl_any_release(value);")
            if first_fn.ret_type != "void":
                out.append("    return result;")
            out.append("}")
            subset = (subset - 1) & managed_mask
        out.append("")
    return out


_ZL_KEYWORDS_FOR_CAPTURE = {
    "if", "else", "for", "let", "set", "return", "true", "false", "this",
    "int", "float", "bool", "str", "ptr", "void", "None", "const", "break",
    "continue", "print", "pass", "stop", "fn", "struct", "import", "as",
    "char", "List", "Any", "ptrstruct", "sizeof", "NULL", "main",
}


def scan_nested_fns(ctx: TranspileContext, lines: List[Tuple[int, str]]) -> None:
    """ZEP-0013 pre-pass: discover every nested `fn` definition, compute
    captures by scope analysis, and register a lifted top-level fn for it.

    Run AFTER collect_signatures (we need ctx.functions populated) and BEFORE
    collect_fn_typedefs so the lifted typedef is known to the emit pipeline.
    """
    i = 0
    while i < len(lines):
        line_no, line = lines[i]
        parsed = parse_fn_header_line(line)
        if not (parsed and parsed[3] == "defn"):
            i += 1
            continue
        outer_name = parsed[0]
        outer_params_text = parsed[1]
        try:
            outer_params, _ = parse_params(outer_params_text, line_no)
        except ZyenError:
            i += 1
            continue
        scope: Dict[str, str] = dict(outer_params)
        i += 1
        depth = 1
        while i < len(lines) and depth > 0:
            ln_no, l = lines[i]
            inner_parsed = parse_fn_header_line(l)
            is_nested_defn = bool(inner_parsed and inner_parsed[3] == "defn")
            if is_nested_defn:
                inner_name, inner_params_text, inner_ret, _ = inner_parsed
                inner_params, _ = parse_params(inner_params_text, ln_no)
                # Collect inner body lines from header through matching `}`.
                body_lines: List[Tuple[int, str]] = [(ln_no, l)]
                inner_depth = l.count("{") - l.count("}")
                j = i + 1
                while j < len(lines) and inner_depth > 0:
                    body_lines.append(lines[j])
                    inner_depth += lines[j][1].count("{") - lines[j][1].count("}")
                    j += 1
                # Capture analysis: identifiers used in body that resolve to
                # the outer scope but aren't bound inside the inner fn.
                inner_local_bindings = set(inner_params.keys())
                for _bn, bl in body_lines[1:]:
                    for m_let in re.finditer(r"\blet\s+([A-Za-z_]\w*)\b", bl):
                        inner_local_bindings.add(m_let.group(1))
                    for m_for in re.finditer(r"\bfor\s*\(\s*let\s+([A-Za-z_]\w*)\b", bl):
                        inner_local_bindings.add(m_for.group(1))
                used: List[str] = []
                seen: set[str] = set()
                for _bn, bl in body_lines:
                    # Remove string literals before identifier scan to avoid
                    # capturing names that only appear inside quotes.
                    sl = re.sub(r'"(?:[^"\\]|\\.)*"', '""', bl)
                    for m_id in re.finditer(r"\b([A-Za-z_]\w*)\b", sl):
                        var = m_id.group(1)
                        if var not in seen:
                            seen.add(var)
                            used.append(var)
                captures: List[Tuple[str, str]] = []
                for var in used:
                    if var in inner_local_bindings:
                        continue
                    if var in _ZL_KEYWORDS_FOR_CAPTURE:
                        continue
                    if var in ctx.functions or var in ctx.structs or var in ctx.consts:
                        continue
                    if var in scope:
                        captures.append((var, scope[var]))
                lifted_name = f"{outer_name}_{inner_name}_zllifted"
                env_struct = f"ZL_env_{outer_name}_{inner_name}"
                # Register the lifted fn's *own* fn type as a typedef so the
                # outer fn's fat-value construction can name it.
                inner_param_types = list(inner_params.values())
                fn_type = f"fn({','.join(inner_param_types)})->{inner_ret}"
                register_fn_typedef(ctx, fn_type)
                ctx.lifted_fn_index[(outer_name, inner_name)] = len(ctx.lifted_fns)
                ctx.lifted_fns.append({
                    "outer": outer_name,
                    "inner": inner_name,
                    "lifted_name": lifted_name,
                    "env_struct": env_struct,
                    "captures": captures,
                    "params": inner_params,
                    "ret_type": inner_ret,
                    "header_line_no": ln_no,
                    # body excluding header line and final closing `}` line
                    "body_lines": body_lines[1:-1] if len(body_lines) >= 2 else [],
                })
                # collect_signatures saw `fn back_fn(...)` and registered it
                # as a top-level fn. Remove it: nested fns are local fat-value
                # symbols of the outer fn, never callable by their bare name
                # outside it.
                if inner_name in ctx.functions:
                    del ctx.functions[inner_name]
                # Advance outer walker past the entire nested fn block.
                # The outer-depth counting at the bottom of this loop would
                # otherwise process every line in body_lines and miscount.
                i = j
                continue
            # Track bindings in outer scope for later nested-fn analysis.
            let_m = re.match(r"\s*(?:let|const)\s+\*?\s*([A-Za-z_]\w*)\s*(?::\s*([^=;]+?))?\s*(?:=\s*(.*))?;\s*$", l)
            if let_m:
                var = let_m.group(1)
                explicit = (let_m.group(2) or "").strip().replace(" ", "")
                rhs = (let_m.group(3) or "").strip()
                if explicit:
                    vtype = explicit
                elif rhs:
                    saved_symbols = ctx.symbols
                    ctx.symbols = dict(scope)
                    try:
                        vtype = infer_type(rhs, ctx)
                    finally:
                        ctx.symbols = saved_symbols
                else:
                    vtype = "int"
                scope[var] = vtype
            depth += l.count("{") - l.count("}")
            i += 1


def emit_lifted_env_structs(ctx: TranspileContext) -> List[str]:
    """ZEP-0013: emit one struct per lifted closure to hold its captured state."""
    out: List[str] = []
    for lf in ctx.lifted_fns:
        out.append(f"typedef struct {lf['env_struct']} {{")
        for cap_name, cap_type in lf["captures"]:
            out.append(f"    {c_type(cap_type)} {cap_name};")
        if not lf["captures"]:
            out.append("    int _zl_empty;")
        out.append(f"}} {lf['env_struct']};")
    if ctx.lifted_fns:
        out.append("")
    for lf in ctx.lifted_fns:
        out.append(f"static void {lf['env_struct']}_zldrop(void* payload) {{")
        out.append(f"    {lf['env_struct']}* env = ({lf['env_struct']}*)payload;")
        out.append("    if (!env) return;")
        for cap_name, cap_type in reversed(lf["captures"]):
            statement = release_statement_for_type(f"env->{cap_name}", cap_type, ctx)
            if statement:
                out.append(f"    {statement}")
        out.append("    free(env);")
        out.append("}")
    if out:
        out.append("")
    return out


def emit_lifted_fn_prototypes(ctx: TranspileContext) -> List[str]:
    """ZEP-0013: forward-declare every lifted closure body so outer fns can
    reference it when building the fat value."""
    out: List[str] = []
    for lf in ctx.lifted_fns:
        params = lf["params"]
        param_types = list(params.values())
        param_names = list(params.keys())
        if param_types:
            c_params = "void* env_v, " + ", ".join(
                f"{c_param_type(t)} {n}" for t, n in zip(param_types, param_names)
            )
        else:
            c_params = "void* env_v"
        ret_c = c_type(lf["ret_type"])
        out.append(f"static {ret_c} {lf['lifted_name']}({c_params});")
    if out:
        out.append("")
    return out


def emit_lifted_fn_bodies(ctx: TranspileContext, all_lines: List[Tuple[int, str]]) -> List[str]:
    """ZEP-0013: emit each lifted closure's body. Captures are renamed in the
    source to `env->capture_name` references; the inner fn's own params are
    declared via the standard signature path."""
    out: List[str] = []
    for lf in ctx.lifted_fns:
        captures = lf["captures"]
        params = lf["params"]
        param_types = list(params.values())
        param_names = list(params.keys())
        if param_types:
            c_params = "void* env_v, " + ", ".join(
                f"{c_param_type(t)} {n}" for t, n in zip(param_types, param_names)
            )
        else:
            c_params = "void* env_v"
        ret_c = c_type(lf["ret_type"])
        out.append(f"static {ret_c} {lf['lifted_name']}({c_params}) {{")
        out.append(f"    {lf['env_struct']}* __zl_env = ({lf['env_struct']}*)env_v;")
        if not captures:
            out.append("    (void)__zl_env;")
        # Hoist each capture into a local var so the body can reference it by
        # its original name without any textual rewrite. Side effect: captures
        # are read-only inside the closure (any `set <capture> = ...` mutates
        # the local copy, not the env). That matches the documented semantics.
        # Use the existing emit_function_body to process the body lines. Seed
        # scope with inner params + captures so infer_type sees correct types.
        saved_symbols = dict(ctx.symbols)
        saved_ptr_targets = dict(ctx.ptr_targets)
        saved_consts = set(ctx.consts)
        saved_list_refs = set(ctx.list_refs)
        saved_scope_stack = list(ctx.scope_stack)
        saved_owned_scope_stack = [list(scope) for scope in ctx.owned_scope_stack]
        saved_loop_depth = ctx.loop_depth
        saved_current = ctx.current_function
        saved_return_type = ctx.current_return_type
        ctx.symbols = dict(params)
        ctx.ptr_targets = {}
        ctx.consts = set()
        ctx.list_refs = {name for name, ztype in params.items() if ztype_base(ztype) == "List"}
        for param_name, param_type in params.items():
            inner = ptr_inner_type(param_type)
            if inner:
                ctx.ptr_targets[param_name] = inner
        for cap_name, cap_type in captures:
            ctx.symbols[cap_name] = cap_type
            inner = ptr_inner_type(cap_type)
            if inner:
                ctx.ptr_targets[cap_name] = inner
            if ztype_base(cap_type) == "List":
                ctx.list_refs.add(cap_name)
            out.append(f"    {c_type(cap_type)} {cap_name} = __zl_env->{cap_name};")
        ctx.scope_stack = [set(params.keys()) | {c[0] for c in captures}]
        ctx.owned_scope_stack = [[]]
        ctx.loop_depth = 0
        ctx.current_function = None
        ctx.current_return_type = lf["ret_type"]
        wrapped = list(lf["body_lines"]) + [(lf["header_line_no"], "}")]
        emit_function_body(wrapped, 0, out, ctx)
        ctx.symbols = saved_symbols
        ctx.ptr_targets = saved_ptr_targets
        ctx.consts = saved_consts
        ctx.list_refs = saved_list_refs
        ctx.scope_stack = saved_scope_stack
        ctx.owned_scope_stack = saved_owned_scope_stack
        ctx.loop_depth = saved_loop_depth
        ctx.current_function = saved_current
        ctx.current_return_type = saved_return_type
        out.append("")
    return out


def collect_fn_typedefs(ctx: TranspileContext, lines: List[Tuple[int, str]]) -> None:
    """ZEP-0010: walk every place a fn-type can appear and register the typedef.

    Must run before emit_struct_defs / emit_function_prototypes so the C typedef
    appears before any use.
    """
    for fn in ctx.functions.values():
        register_fn_types_in_type(ctx, fn.ret_type)
        for t in fn.params.values():
            register_fn_types_in_type(ctx, t)
        # Also register the fn's own signature as an fn type so
        # `let h = some_named_fn;` (inferred type) finds the typedef.
        # Skip struct methods — `this` is internal and not user-callable as fn ptr.
        if "this" not in fn.params:
            own_sig = f"fn({','.join(fn.params.values())})->{fn.ret_type}"
            register_fn_typedef(ctx, own_sig)
    for struct in ctx.structs.values():
        for t in struct.fields.values():
            register_fn_types_in_type(ctx, t)
    for _line_no, line in lines:
        parsed = parse_variable_declaration_syntax(line, trailing_semicolon=True)
        if parsed is None:
            parsed = parse_variable_declaration_syntax(line, trailing_semicolon=True, owned_pointer=True)
        if parsed and parsed[2]:
            register_fn_types_in_type(ctx, _strip_module_prefix_type(parsed[2]))


def emit_fn_typedefs(ctx: TranspileContext) -> List[str]:
    """Emit one checked call helper per unique source-level fn signature."""
    out: List[str] = []
    for _tname, ztype in ctx.fn_typedefs.items():
        param_types, ret_type = parse_fn_type(ztype)
        call_type = fn_call_pointer_name(ztype)
        helper = fn_call_helper_name(ztype)
        owned_helper = fn_owned_call_helper_name(ztype)
        call_params = ["void* env"] + [c_param_type(param) for param in param_types]
        helper_params = ["ZL_Function value"] + [
            f"{c_param_type(param)} arg{index}" for index, param in enumerate(param_types)
        ]
        args = ", ".join(f"arg{index}" for index in range(len(param_types)))
        invoke_args = f"value.env, {args}" if args else "value.env"
        signature = json.dumps(ztype.replace(" ", ""))
        out.append(f"typedef {c_type(ret_type)} (*{call_type})({', '.join(call_params)});")
        out.append(f"static inline {c_type(ret_type)} {helper}({', '.join(helper_params)}) {{")
        out.append(f"    zl_fn_require(value, {signature});")
        if ret_type == "void":
            out.append(f"    (({call_type})value.call)({invoke_args});")
        else:
            out.append(f"    return (({call_type})value.call)({invoke_args});")
        out.append("}")
        out.append(f"static inline {c_type(ret_type)} {owned_helper}({', '.join(helper_params)}) {{")
        out.append(f"    zl_fn_require(value, {signature});")
        if ret_type == "void":
            out.append(f"    (({call_type})value.call)({invoke_args});")
            out.append("    zl_fn_release(value);")
        else:
            out.append(f"    {c_type(ret_type)} result = (({call_type})value.call)({invoke_args});")
            out.append("    zl_fn_release(value);")
            out.append("    return result;")
        out.append("}")
        managed_mask = 0
        for index, param_type in enumerate(param_types):
            if type_has_managed_value(param_type, ctx):
                managed_mask |= 1 << index
        subset = managed_mask
        while subset:
            for owns_callee in (False, True):
                variant = fn_call_variant_name(ztype, subset, owns_callee)
                out.append(f"static inline {c_type(ret_type)} {variant}({', '.join(helper_params)}) {{")
                out.append(f"    zl_fn_require(value, {signature});")
                if ret_type == "void":
                    out.append(f"    (({call_type})value.call)({invoke_args});")
                else:
                    out.append(f"    {c_type(ret_type)} result = (({call_type})value.call)({invoke_args});")
                for index in range(len(param_types) - 1, -1, -1):
                    if subset & (1 << index):
                        statement = release_statement_for_type(f"arg{index}", param_types[index], ctx)
                        if statement:
                            out.append(f"    {statement}")
                if owns_callee:
                    out.append("    zl_fn_release(value);")
                if ret_type != "void":
                    out.append("    return result;")
                out.append("}")
            subset = (subset - 1) & managed_mask
    if out:
        out.append("")
    return out


def emit_fn_thunks(ctx: TranspileContext) -> List[str]:
    """ZEP-0013: emit a `<name>_zlthunk` + const fat literal `<name>_zlfnval`
    for every top-level fn whose own signature is registered as a fn type.

    The thunk accepts a leading `void* env` it ignores, so it fits the fat
    call_ptr slot for any caller that holds a fn value.
    """
    out: List[str] = []
    for fn in ctx.functions.values():
        if "this" in fn.params:
            continue
        own_sig = f"fn({','.join(fn.params.values())})->{fn.ret_type}"
        tname = fn_typedef_name(own_sig)
        if tname not in ctx.fn_typedefs:
            continue
        param_types = list(fn.params.values())
        param_names = list(fn.params.keys())
        if param_types:
            c_params = "void* env, " + ", ".join(
                f"{c_param_type(t)} {n}" for t, n in zip(param_types, param_names)
            )
            forward = ", ".join(param_names)
        else:
            c_params = "void* env"
            forward = ""
        ret_c = c_type(fn.ret_type)
        if fn.ret_type == "void":
            out.append(f"static {ret_c} {fn.name}_zlthunk({c_params}) {{ (void)env; {fn.name}({forward}); }}")
        else:
            out.append(f"static {ret_c} {fn.name}_zlthunk({c_params}) {{ (void)env; return {fn.name}({forward}); }}")
        signature = json.dumps(own_sig.replace(" ", ""))
        out.append(
            f"__attribute__((unused)) static ZL_Function {fn.name}_zlfnval = "
            f"{{ (ZL_GenericFn){fn.name}_zlthunk, NULL, NULL, {signature} }};"
        )
    if out:
        out.append("")
    return out


def emit_function_prototypes(ctx: TranspileContext) -> List[str]:
    out: List[str] = []
    for fn in ctx.functions.values():
        out.append(c_function_signature(fn.name, fn.ret_type, fn.params) + ";")
    if out:
        out.append("")
    return out


def emit_owned_argument_wrappers(ctx: TranspileContext) -> List[str]:
    """Release owned temporary arguments after a borrowed-parameter call."""
    out: List[str] = []
    for fn in ctx.functions.values():
        params = list(fn.params.items())
        managed_mask = 0
        for index, (_name, ztype) in enumerate(params):
            if type_has_managed_value(ztype, ctx):
                managed_mask |= 1 << index
        subset = managed_mask
        while subset:
            wrapper = direct_owned_args_wrapper_name(fn.name, subset)
            c_params = ", ".join(f"{c_param_type(ztype)} {name}" for name, ztype in params) or "void"
            args = ", ".join(name for name, _ztype in params)
            out.append(f"static inline {c_type(fn.ret_type)} {wrapper}({c_params}) {{")
            if fn.ret_type == "void":
                out.append(f"    {fn.name}({args});")
            else:
                out.append(f"    {c_type(fn.ret_type)} result = {fn.name}({args});")
            for index in range(len(params) - 1, -1, -1):
                if subset & (1 << index):
                    name, ztype = params[index]
                    statement = release_statement_for_type(name, ztype, ctx)
                    if statement:
                        out.append(f"    {statement}")
            if fn.ret_type != "void":
                out.append("    return result;")
            out.append("}")
            subset = (subset - 1) & managed_mask
    if out:
        out.append("")
    return out


def jump_cleanup_lines(ctx: TranspileContext, block_stack: List[str], for_owned_bases: List[Optional[int]], is_continue: bool) -> List[str]:
    loop_index = -1
    for index in range(len(block_stack) - 1, -1, -1):
        if block_stack[index] == "for":
            loop_index = index
            break
    if loop_index < 0:
        return []
    out: List[str] = []
    for scope_index in range(len(ctx.owned_scope_stack) - 1, loop_index, -1):
        out.extend(scope_cleanup_lines(ctx.owned_scope_stack[scope_index], ctx))
    loop_entries = ctx.owned_scope_stack[loop_index]
    if is_continue:
        base = for_owned_bases[loop_index] or 0
        loop_entries = loop_entries[base:]
    out.extend(scope_cleanup_lines(loop_entries, ctx))
    return out


def emit_function_body(lines: List[Tuple[int, str]], start_i: int, out: List[str], ctx: TranspileContext) -> int:
    block_stack: List[str] = ["fn"]
    for_owned_bases: List[Optional[int]] = [None]
    i = start_i
    while i < len(lines):
        line_no, line = lines[i]
        # ZEP-0013: nested fn defn → look up the pre-scanned lifted record,
        # emit env malloc + capture copies + fat-value local, skip the body.
        inner_parsed = parse_fn_header_line(line)
        if inner_parsed and inner_parsed[3] == "defn":
            outer_name = ctx.current_function or ""
            inner_name = inner_parsed[0]
            key = (outer_name, inner_name)
            if key not in ctx.lifted_fn_index:
                raise ZyenError(
                    f"line {line_no}: internal — nested fn `{inner_name}` "
                    f"not found in lifted index for outer `{outer_name}`"
                )
            lf = ctx.lifted_fns[ctx.lifted_fn_index[key]]
            env_struct = lf["env_struct"]
            env_var = f"__zl_env_{lf['inner']}"
            inner_param_types = list(lf["params"].values())
            fn_type = f"fn({','.join(inner_param_types)})->{lf['ret_type']}"
            out.append(f"{env_struct}* {env_var} = ({env_struct}*)malloc(sizeof({env_struct}));")
            for cap_name, cap_type in lf["captures"]:
                captured_c = retain_expr_for_type(cap_name, cap_type, ctx) if type_has_managed_value(cap_type, ctx) else cap_name
                out.append(f"{env_var}->{cap_name} = {captured_c};")
            owner_var = f"__zl_owner_{lf['inner']}"
            out.append(f"ZL_ArcControl* {owner_var} = zl_arc_new({env_var}, {env_struct}_zldrop);")
            signature = json.dumps(fn_type.replace(" ", ""))
            out.append(
                f"ZL_Function {inner_name} = {{ (ZL_GenericFn){lf['lifted_name']}, "
                f"{env_var}, {owner_var}, {signature} }};"
            )
            declare_symbol(ctx, inner_name, fn_type, line_no)
            # Skip past the nested fn block in `lines`.
            depth_inner = line.count("{") - line.count("}")
            j = i + 1
            while j < len(lines) and depth_inner > 0:
                depth_inner += lines[j][1].count("{") - lines[j][1].count("}")
                j += 1
            i = j
            continue
        if line.startswith("if"):
            m = re.match(r"if\s*\((.*)\)\s*\{\s*$", line)
            if not m:
                raise ZyenError(f"line {line_no}: invalid if statement; use `if (condition) {{`")
            cond = m.group(1).strip()
            out.append(f"if ({transform_expr(cond, ctx)}) {{")
            push_scope(ctx)
            block_stack.append("if")
            for_owned_bases.append(None)
        elif re.match(r"}\s*else\s+if\s*\(.*\)\s*{\s*$", line):
            if not block_stack:
                raise ZyenError(f"line {line_no}: unexpected `else if`")
            closed = block_stack.pop()
            for_owned_bases.pop()
            if closed == "for":
                ctx.loop_depth -= 1
            for cleanup in scope_cleanup_lines(ctx.owned_scope_stack[-1] if ctx.owned_scope_stack else [], ctx):
                out.append("    " + cleanup)
            pop_scope(ctx)
            m = re.match(r"}\s*else\s+if\s*\((.*)\)\s*{\s*$", line)
            cond = m.group(1).strip()
            out.append(f"}} else if ({transform_expr(cond, ctx)}) {{")
            push_scope(ctx)
            block_stack.append("if")
            for_owned_bases.append(None)
        elif line == "} else {":
            if not block_stack:
                raise ZyenError(f"line {line_no}: unexpected `else`")
            closed = block_stack.pop()
            for_owned_bases.pop()
            if closed == "for":
                ctx.loop_depth -= 1
            for cleanup in scope_cleanup_lines(ctx.owned_scope_stack[-1] if ctx.owned_scope_stack else [], ctx):
                out.append("    " + cleanup)
            pop_scope(ctx)
            out.append("} else {")
            push_scope(ctx)
            block_stack.append("else")
            for_owned_bases.append(None)
        elif line == "}":
            if not block_stack:
                raise ZyenError(f"line {line_no}: unexpected `}}`")
            closed = block_stack.pop()
            for_owned_bases.pop()
            if closed == "for":
                ctx.loop_depth -= 1
            for cleanup in scope_cleanup_lines(ctx.owned_scope_stack[-1] if ctx.owned_scope_stack else [], ctx):
                out.append("    " + cleanup)
            pop_scope(ctx)
            out.append("}")
            out.append("")
            i += 1
            if not block_stack:
                return i
            continue
        elif line.startswith("for"):
            push_scope(ctx)
            out.append(parse_for(line, ctx, line_no))
            block_stack.append("for")
            for_owned_bases.append(len(ctx.owned_scope_stack[-1]))
            ctx.loop_depth += 1
        elif line.startswith("class "):
            raise ZyenError(f"line {line_no}: `class` is planned for v0.2; v0.1 supports `struct`")
        else:
            if line in {"break;", "continue;"}:
                for cleanup in jump_cleanup_lines(ctx, block_stack, for_owned_bases, line == "continue;"):
                    out.append("    " + cleanup)
            statement = transform_statement(line, ctx, line_no)
            out.extend("    " + part for part in statement.splitlines())
        i += 1
    raise ZyenError("function is missing closing `}`")


def reset_function_context(ctx: TranspileContext, name: str, params: Dict[str, str]) -> None:
    ctx.current_function = name
    ctx.current_return_type = ctx.functions[name].ret_type if name in ctx.functions else "void"
    ctx.symbols = {param_name: ztype_base(param_type) for param_name, param_type in params.items()}
    # Preserve special pointer-to-struct self type for methods.
    for param_name, param_type in params.items():
        if ptrstruct_inner_type(param_type):
            ctx.symbols[param_name] = param_type.replace(" ", "")
    ctx.consts = set()
    ctx.ptr_targets = {}
    ctx.list_refs = {param_name for param_name, param_type in params.items() if ztype_base(param_type) == "List"}
    ctx.list_item_types = {
        param_name: None
        for param_name, param_type in params.items()
        if ztype_base(param_type) == "List"
    }
    ctx.scope_stack = [set(params.keys())]
    ctx.owned_scope_stack = [[]]
    ctx.loop_depth = 0
    for param_name, param_type in params.items():
        inner = ptr_inner_type(param_type)
        if inner:
            ctx.ptr_targets[param_name] = inner


def collect_c_module_type_labels(source: str) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for raw in source.splitlines():
        match = re.match(r'^\s*//\s*c_module_type:\s*([A-Za-z_]\w*)\s*=\s*(".*")\s*$', raw)
        if not match:
            continue
        try:
            label = json.loads(match.group(2))
        except json.JSONDecodeError:
            continue
        if isinstance(label, str):
            labels[match.group(1)] = label
    return labels


def collect_c_module_external_structs(source: str) -> Set[str]:
    names: Set[str] = set()
    for raw in source.splitlines():
        match = re.match(r'^\s*//\s*c_module_external_struct:\s*([A-Za-z_]\w*)\s*$', raw)
        if match:
            names.add(match.group(1))
    return names


def collect_c_module_native_function_types(source: str) -> Dict[str, str]:
    signatures: Dict[str, str] = {}
    for raw in source.splitlines():
        match = re.match(r"^\s*//\s*c_module_native_fn:\s*([A-Za-z_]\w*)\s*=\s*(fn\(.+\)->.+)\s*$", raw)
        if match:
            signatures[match.group(1)] = match.group(2).replace(" ", "")
    return signatures

def transpile(source: str) -> str:
    lines = clean_lines(source)
    ctx = collect_signatures(lines)
    ctx.c_module_types = collect_c_module_type_labels(source)
    ctx.external_structs = collect_c_module_external_structs(source)
    ctx.native_function_types = collect_c_module_native_function_types(source)
    needs_python_cli = "zl_cv_" in source or "zl_gpu_" in source
    out: List[str] = []
    out.append("// Generated by ZyenLang v0.1.83")
    out.append("#ifndef _WIN32")
    out.append("#ifndef _POSIX_C_SOURCE")
    out.append("#define _POSIX_C_SOURCE 200809L")
    out.append("#endif")
    out.append("#endif")
    out.append("#include <stdio.h>")
    out.append("#include <stdbool.h>")
    out.append("#include <stdlib.h>")
    out.append("#include <string.h>")
    out.append("#include <stdarg.h>")
    out.append("#include <time.h>")
    out.append("#ifdef _WIN32")
    out.append("#include <windows.h>")
    out.append("#else")
    out.append("#include <unistd.h>")
    out.append("#include <sys/time.h>")
    out.append("#endif")
    out.append("#ifdef __APPLE__")
    out.append("extern int sysctlbyname(const char*, void*, size_t*, void*, size_t);")
    out.append("#endif")
    out.append("")
    abi_header = Path(__file__).resolve().parent / "std" / "zyenlang_c_abi.h"
    out.extend(abi_header.read_text(encoding="utf-8").splitlines())
    out.append("")
    out.append("static atomic_int zl_mem_next_id = 1;")
    out.append("static inline ptr zl_ptr(void* addr, const char* type_name) { return zl_ptr_borrow(addr, type_name); }")
    out.append("static inline ptr zl_none_ptr(const char* type_name) { return (ptr){ NULL, type_name, 0, false, NULL }; }")
    out.append("static inline bool zl_ptr_is_none(ptr p) { return p.addr == NULL; }")
    out.append("static inline bool zl_ptr_is_owned(ptr p) { return p.owner != NULL; }")
    out.append("static inline bool zl_ptr_is_valid(ptr p) { if (p.addr == NULL) return false; if (!p.owner) return true; return !atomic_load_explicit(&p.owner->disposed, memory_order_acquire) && p.owner->payload == p.addr; }")
    out.append("static inline void* zl_ptr_checked_addr(ptr p, const char* name) { if (p.addr == NULL) { fprintf(stderr, \"None pointer dereference: %s\\n\", name); exit(1); } if (!zl_ptr_is_valid(p)) { fprintf(stderr, \"Freed pointer dereference: %s\\n\", name); exit(1); } return p.addr; }")
    out.append("static inline ZL_Function zl_fn_ptr_load(ptr p, const char* signature, const char* name) { void* addr = zl_ptr_checked_addr(p, name); if (!p.type_name || strcmp(p.type_name, signature) != 0) { fprintf(stderr, \"function pointer signature mismatch: %s stores %s, requested %s\\n\", name, p.type_name ? p.type_name : \"unknown\", signature); exit(1); } ZL_Function value = *((ZL_Function*)addr); zl_fn_require(value, signature); return value; }")
    out.append("static inline void zl_print_ptr_value(ptr p, bool release_after) { if (p.addr == NULL) printf(\"None\\n\"); else if (!zl_ptr_is_valid(p)) printf(\"Freed\\n\"); else printf(\"%p\\n\", p.addr); if (release_after) zl_ptr_release(p); }")
    out.append("static inline void zl_mem_drop_ptr_cell(void* payload) { if (!payload) return; zl_ptr_release(*((ptr*)payload)); free(payload); }")
    out.append("static inline void zl_mem_drop_fn_cell(void* payload) { if (!payload) return; zl_fn_release(*((ZL_Function*)payload)); free(payload); }")
    out.append("static inline ptr zl_mem_alloc_cell_drop(size_t size, const char* type_name, ZL_ArcDrop drop) { void* raw = calloc(1, size); if (!raw) { fprintf(stderr, \"memory allocation failed\\n\"); exit(1); } int id = atomic_fetch_add_explicit(&zl_mem_next_id, 1, memory_order_relaxed); ZL_ArcControl* owner = zl_arc_new(raw, drop ? drop : zl_ptr_default_drop); return (ptr){ raw, type_name, id, true, owner }; }")
    out.append("static inline ptr zl_mem_alloc_cell(size_t size, const char* type_name) { return zl_mem_alloc_cell_drop(size, type_name, zl_ptr_default_drop); }")
    out.append("static inline int zl_mem_free(ptr p) { if (p.addr == NULL) return -1; if (!p.owner) return -2; return zl_arc_dispose(p.owner) ? 0 : -3; }")
    out.append("static inline bool zl_mem_is_valid(ptr p) { return zl_ptr_is_valid(p); }")
    out.append("static inline bool zl_mem_is_none(ptr p) { return p.addr == NULL; }")
    out.append("static inline bool zl_mem_is_owned(ptr p) { return p.owner != NULL && p.owned; }")
    out.append("static inline const char* zl_mem_type(ptr p) { return p.type_name ? p.type_name : \"ptr\"; }")
    out.append("static inline ptr zl_mem_alloc_int(int v) { ptr p = zl_mem_alloc_cell(sizeof(int), \"int\"); *((int*)p.addr) = v; return p; }")
    out.append("static inline ptr zl_mem_alloc_float(double v) { ptr p = zl_mem_alloc_cell(sizeof(double), \"float\"); *((double*)p.addr) = v; return p; }")
    out.append("static inline ptr zl_mem_alloc_bool(bool v) { ptr p = zl_mem_alloc_cell(sizeof(bool), \"bool\"); *((bool*)p.addr) = v; return p; }")
    out.append("static inline char* zl_mem_strdup(const char* s) { size_t n = strlen(s ? s : \"\") + 1; char* out = (char*)malloc(n); if (!out) { fprintf(stderr, \"string allocation failed\\n\"); exit(1); } memcpy(out, s ? s : \"\", n); return out; }")
    out.append("static inline void zl_mem_drop_str_cell(void* payload) { if (!payload) return; char* inner = *((char**)payload); if (inner) free(inner); free(payload); }")
    out.append("static inline ptr zl_mem_alloc_str(const char* v) { ptr p = zl_mem_alloc_cell(sizeof(char*), \"str\"); p.owner->drop = zl_mem_drop_str_cell; *((char**)p.addr) = zl_mem_strdup(v); return p; }")
    out.append("static inline Any zl_any_int(int v) { return (Any){ .kind = 1, .i = v }; }")
    out.append("static inline Any zl_any_float(double v) { return (Any){ .kind = 2, .f = v }; }")
    out.append("static inline Any zl_any_str(const char* v) { return (Any){ .kind = 3, .s = zl_mem_strdup(v ? v : \"\") }; }")
    out.append("static inline Any zl_any_bool(bool v) { return (Any){ .kind = 4, .b = v }; }")
    out.append("static inline Any zl_any_ptr(ptr v) { return (Any){ .kind = 5, .p = zl_ptr_retain(v) }; }")
    out.append("static inline Any zl_any_ptr_take(ptr v) { return (Any){ .kind = 5, .p = v }; }")
    out.append("static inline Any zl_any_list(ZL_List* v) { return (Any){ .kind = 6, .l = v }; }")
    out.append("static inline Any zl_any_retain(Any v) { if (v.kind == ZL_VALUE_PTR) v.p = zl_ptr_retain(v.p); else if (v.kind == ZL_VALUE_STRUCT) zl_arc_retain(v.object_owner); return v; }")
    out.append("static inline void zl_any_release(Any v) { if (v.kind == ZL_VALUE_PTR) zl_ptr_release(v.p); else if (v.kind == ZL_VALUE_STRUCT) zl_arc_release(v.object_owner); }")
    out.append("static inline void zl_mem_drop_any_cell(void* payload) { if (!payload) return; zl_any_release(*((Any*)payload)); free(payload); }")
    out.append("static inline ptr zl_mem_alloc_any(Any v) { ptr p = zl_mem_alloc_cell_drop(sizeof(Any), \"Any\", zl_mem_drop_any_cell); *((Any*)p.addr) = v; return p; }")
    out.append("static inline int zl_int_checked(long long v, const char* op) { if (v < -2147483648LL || v > 2147483647LL) { fprintf(stderr, \"int overflow in %s: %lld\\n\", op, v); exit(1); } return (int)v; }")
    out.append("static inline int zl_int_add(int a, int b) { return zl_int_checked((long long)a + (long long)b, \"+\"); }")
    out.append("static inline int zl_int_sub(int a, int b) { return zl_int_checked((long long)a - (long long)b, \"-\"); }")
    out.append("static inline int zl_int_mul(int a, int b) { return zl_int_checked((long long)a * (long long)b, \"*\"); }")
    out.append("static inline int zl_int_div(int a, int b) { if (b == 0) { fprintf(stderr, \"int divide by zero\\n\"); exit(1); } if (a == -2147483648 && b == -1) { fprintf(stderr, \"int overflow in /: 2147483648\\n\"); exit(1); } return a / b; }")
    out.append("static inline const char* zl_cast_str_int(int v) { static char buf[16][64]; static int idx = 0; char* out = buf[idx++ & 15]; snprintf(out, 64, \"%d\", v); return out; }")
    out.append("static inline const char* zl_cast_str_float(double v) { static char buf[16][64]; static int idx = 0; char* out = buf[idx++ & 15]; snprintf(out, 64, \"%g\", v); return out; }")
    out.append("static inline const char* zl_cast_str_bool(bool v) { return v ? \"true\" : \"false\"; }")
    out.append("static inline const char* zl_cast_str_ptr(ptr v) { static char buf[16][64]; static int idx = 0; char* out = buf[idx++ & 15]; if (v.addr == NULL) return \"None\"; if (!zl_ptr_is_valid(v)) return \"Freed\"; snprintf(out, 64, \"%p\", (void*)v.addr); return out; }")
    out.append("static inline const char* zl_cast_str_ptr_release(ptr v, bool release_after) { const char* out = zl_cast_str_ptr(v); if (release_after) zl_ptr_release(v); return out; }")
    out.append("static inline int zl_cast_int_str(const char* s) { return atoi(s); }")
    out.append("static inline double zl_cast_float_str(const char* s) { return atof(s); }")
    out.append("static inline bool zl_cast_bool_str(const char* s) { return strcmp(s, \"true\") == 0 || strcmp(s, \"1\") == 0; }")
    out.append("static inline int zl_cast_int_any(Any v) { switch (v.kind) { case 1: return (int)v.i; case 2: return (int)v.f; case 3: return atoi(v.s); case 4: return v.b ? 1 : 0; default: return 0; } }")
    out.append("static inline double zl_cast_float_any(Any v) { switch (v.kind) { case 1: return (double)v.i; case 2: return v.f; case 3: return atof(v.s); case 4: return v.b ? 1.0 : 0.0; default: return 0.0; } }")
    out.append("static inline bool zl_cast_bool_any(Any v) { switch (v.kind) { case 1: return v.i != 0; case 2: return v.f != 0.0; case 3: return strcmp(v.s, \"true\") == 0 || strcmp(v.s, \"1\") == 0; case 4: return v.b; case 5: return zl_ptr_is_valid(v.p); case 6: return v.l != NULL && v.l->len > 0; case 7: return v.object != NULL; default: return false; } }")
    out.append("static inline ptr zl_cast_ptr_any(Any v) { if (v.kind == 5) return v.p; return zl_none_ptr(\"void\"); }")
    out.append("static inline const char* zl_cast_str_any(Any v) { static char buf[16][128]; static int idx = 0; char* out = buf[idx++ & 15]; switch (v.kind) { case 1: snprintf(out, 128, \"%lld\", v.i); return out; case 2: snprintf(out, 128, \"%g\", v.f); return out; case 3: return v.s; case 4: return v.b ? \"true\" : \"false\"; case 5: return zl_cast_str_ptr(v.p); case 6: if (v.l == NULL) return \"List(None)\"; snprintf(out, 128, \"List(len=%d)\", v.l->len); return out; case 7: return v.object_type ? v.object_type : \"struct\"; default: return \"Any(?)\"; } }")
    out.append("static inline const char* zl_cast_str_list(ZL_List* l) { static char buf[16][64]; static int idx = 0; char* out = buf[idx++ & 15]; if (l == NULL) return \"List(None)\"; snprintf(out, 64, \"List(len=%d)\", l->len); return out; }")
    out.append("static inline const char* zl_str_join(int count, ...) { size_t total = 1; va_list ap; va_start(ap, count); for (int i = 0; i < count; i++) { const char* s = va_arg(ap, const char*); if (!s) s = \"\"; total += strlen(s); } va_end(ap); char* out = (char*)malloc(total); if (!out) { fprintf(stderr, \"string join allocation failed\\n\"); exit(1); } out[0] = 0; va_start(ap, count); for (int i = 0; i < count; i++) { const char* s = va_arg(ap, const char*); if (!s) s = \"\"; strcat(out, s); } va_end(ap); return out; }")
    out.append("static inline const char* zl_char_to_str(int c) { char* out = (char*)malloc(2); if (!out) { fprintf(stderr, \"char allocation failed\\n\"); exit(1); } out[0] = (char)c; out[1] = 0; return out; }")
    out.append("static inline const char* zl_str_substring(const char* s, int start, int length) { if (!s) s = \"\"; int n = (int)strlen(s); if (start < 0) start = 0; if (start > n) start = n; if (length < 0 || start + length > n) length = n - start; char* out = (char*)malloc((size_t)length + 1); if (!out) { fprintf(stderr, \"substring allocation failed\\n\"); exit(1); } memcpy(out, s + start, (size_t)length); out[length] = 0; return out; }")
    out.append("static inline int zl_time_clock_ms(void) {")
    out.append("#ifdef _WIN32")
    out.append("    return (int)(GetTickCount64() & 0x7fffffff);")
    out.append("#else")
    out.append("    struct timeval tv; gettimeofday(&tv, NULL);")
    out.append("    long long ms = (long long)tv.tv_sec * 1000LL + (long long)(tv.tv_usec / 1000);")
    out.append("    return (int)(ms & 0x7fffffff);")
    out.append("#endif")
    out.append("}")
    out.append("static inline int zl_thread_sleep_ms(int ms) {")
    out.append("    if (ms < 0) ms = 0;")
    out.append("#ifdef _WIN32")
    out.append("    Sleep((DWORD)ms);")
    out.append("#else")
    out.append("    struct timespec request = { .tv_sec = ms / 1000, .tv_nsec = (long)(ms % 1000) * 1000000L };")
    out.append("    nanosleep(&request, NULL);")
    out.append("#endif")
    out.append("    return 0;")
    out.append("}")
    out.append("static inline int zl_thread_yield_now(void) { return zl_thread_sleep_ms(0); }")
    out.append("static inline int zl_thread_cpu_count(void) {")
    out.append("#ifdef _WIN32")
    out.append("    SYSTEM_INFO info; GetSystemInfo(&info); return (int)info.dwNumberOfProcessors;")
    out.append("#elif defined(__APPLE__)")
    out.append("    int n = 1; size_t size = sizeof(n); if (sysctlbyname(\"hw.logicalcpu\", &n, &size, NULL, 0) != 0) return 1; return n > 0 ? n : 1;")
    out.append("#else")
    out.append("    long n = sysconf(_SC_NPROCESSORS_ONLN); return n > 0 ? (int)n : 1;")
    out.append("#endif")
    out.append("}")
    out.append('static inline int zl_thread_run_cmd(const char* command) { fflush(stdout); return system(command ? command : ""); }')
    out.append("static inline int zl_thread_spawn_cmd(const char* command) {")
    out.append("    if (!command || !command[0]) return -1;")
    out.append("    char cmd[1400];")
    out.append("#ifdef _WIN32")
    out.append('    snprintf(cmd, sizeof(cmd), "start \\"\\" /B cmd /C \\"%s\\"", command);')
    out.append("#else")
    out.append('    snprintf(cmd, sizeof(cmd), "%s &", command);')
    out.append("#endif")
    out.append("    fflush(stdout); return system(cmd);")
    out.append("}")
    out.append('static inline int zl_cmd_run(const char* command) { fflush(stdout); return system(command); }')
    out.append('static inline int zl_term_clear(void) { fflush(stdout); return system("cls || clear"); }')
    out.append('static inline void zl_term_line(void) { printf("------------------------------------------------------------\\n"); }')
    out.append('static inline const char* zl_term_input(const char* prompt) { char buf[1024]; if (prompt) { printf("%s", prompt); fflush(stdout); } if (!fgets(buf, sizeof(buf), stdin)) { buf[0] = 0; return zl_mem_strdup(""); } size_t n = strlen(buf); while (n > 0 && (buf[n-1] == \"\\n\"[0] || buf[n-1] == \"\\r\"[0])) { buf[--n] = 0; } return zl_mem_strdup(buf); }')
    out.append('static inline int zl_term_pause(void) { printf("Press Enter to continue..."); fflush(stdout); char tmp[8]; fgets(tmp, sizeof(tmp), stdin); return 0; }')
    out.append('static inline bool zl_fs_exists(const char* path) { FILE* f = fopen(path, "rb"); if (!f) return false; fclose(f); return true; }')
    out.append('static inline const char* zl_fs_read(const char* path) { FILE* f = fopen(path, "rb"); if (!f) return ""; if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return ""; } long n = ftell(f); if (n < 0) { fclose(f); return ""; } rewind(f); char* buf = (char*)malloc((size_t)n + 1); if (!buf) { fclose(f); fprintf(stderr, "fs.read allocation failed\\n"); exit(1); } size_t got = fread(buf, 1, (size_t)n, f); buf[got] = 0; fclose(f); return buf; }')
    out.append('static inline int zl_fs_write(const char* path, const char* text) { FILE* f = fopen(path, "wb"); if (!f) return -1; fputs(text ? text : "", f); fclose(f); return 0; }')
    out.append('static inline int zl_fs_append(const char* path, const char* text) { FILE* f = fopen(path, "ab"); if (!f) return -1; fputs(text ? text : "", f); fclose(f); return 0; }')
    out.append("static inline const char* zl_path_basename(const char* path) { if (!path) path = \"\"; const char* a = strrchr(path, '/'); const char* b = strrchr(path, '\\\\'); const char* p = a > b ? a : b; return zl_mem_strdup(p ? p + 1 : path); }")
    out.append("static inline const char* zl_path_dirname(const char* path) { if (!path) path = \"\"; const char* a = strrchr(path, '/'); const char* b = strrchr(path, '\\\\'); const char* p = a > b ? a : b; if (!p) return \".\"; return zl_str_substring(path, 0, (int)(p - path)); }")
    out.append("static inline const char* zl_path_ext(const char* path) { const char* base = zl_path_basename(path); const char* dot = strrchr(base, '.'); if (!dot || dot == base) return \"\"; return zl_mem_strdup(dot); }")
    out.append("static inline const char* zl_path_join(const char* a, const char* b) { if (!a || !a[0]) return zl_mem_strdup(b ? b : \"\"); if (!b || !b[0]) return zl_mem_strdup(a); const char* sep = (a[strlen(a)-1] == '/' || a[strlen(a)-1] == '\\\\') ? \"\" : \"/\"; return zl_str_join(3, a, sep, b); }")
    out.append("static inline ZL_List zl_list_new(void) { return (ZL_List){ NULL, 0, 0 }; }")
    out.append("static inline void zl_list_ensure(ZL_List* l, int need) { if (l->cap >= need) return; int cap = l->cap ? l->cap * 2 : 4; while (cap < need) cap *= 2; Any* next = (Any*)realloc(l->items, sizeof(Any) * cap); if (!next) { fprintf(stderr, \"List allocation failed\\n\"); exit(1); } l->items = next; l->cap = cap; }")
    out.append("static inline void zl_list_append(ZL_List* l, Any v) { zl_list_ensure(l, l->len + 1); l->items[l->len++] = v; }")
    out.append("static inline ZL_List zl_list_from_array(Any* items, int count) { ZL_List list = zl_list_new(); if (count <= 0) return list; zl_list_ensure(&list, count); for (int i = 0; i < count; i++) list.items[list.len++] = items[i]; return list; }")
    out.append("static inline int zl_list_len(ZL_List* l) { return l->len; }")
    out.append("static inline bool zl_list_is_empty(ZL_List* l) { return l->len == 0; }")
    out.append("static inline Any zl_list_get(ZL_List* l, int index) { if (index < 0 || index >= l->len) { fprintf(stderr, \"List index out of range: %d\\n\", index); exit(1); } return l->items[index]; }")
    out.append("static inline ptr zl_list_ptr(ZL_List* l, int index) { if (index < 0 || index >= l->len) { fprintf(stderr, \"List index out of range: %d\\n\", index); exit(1); } return zl_ptr(&l->items[index], \"Any\"); }")
    out.append("static inline ptr zl_list_append_ptr(ZL_List* l, Any v) { zl_list_append(l, v); return zl_ptr(&l->items[l->len - 1], \"Any\"); }")
    out.append("static inline void zl_list_set(ZL_List* l, int index, Any v) { if (index < 0 || index >= l->len) { zl_any_release(v); fprintf(stderr, \"List index out of range: %d\\n\", index); exit(1); } zl_any_release(l->items[index]); l->items[index] = v; }")
    out.append("static inline Any zl_list_pop(ZL_List* l) { if (l->len <= 0) { fprintf(stderr, \"List pop from empty list\\n\"); exit(1); } return l->items[--l->len]; }")
    out.append("static inline void zl_list_clear(ZL_List* l) { for (int i = 0; i < l->len; i++) zl_any_release(l->items[i]); l->len = 0; }")
    out.append("static inline ZL_List zl_str_split(const char* s, const char* sep) { ZL_List list = zl_list_new(); if (!s) s = \"\"; if (!sep || sep[0] == 0) { for (int i = 0; s[i]; i++) zl_list_append(&list, zl_any_str(zl_char_to_str((unsigned char)s[i]))); return list; } size_t sepn = strlen(sep); const char* cur = s; const char* hit = NULL; while ((hit = strstr(cur, sep)) != NULL) { int len = (int)(hit - cur); zl_list_append(&list, zl_any_str(zl_str_substring(cur, 0, len))); cur = hit + sepn; } zl_list_append(&list, zl_any_str(cur)); return list; }")
    out.append("#ifdef _WIN32")
    out.append('static inline ZL_List zl_fs_list_dir(const char* path) { ZL_List l = zl_list_new(); if (!path || !path[0]) return l; char pattern[1024]; snprintf(pattern, sizeof(pattern), "%s/*", path); WIN32_FIND_DATAA fd; HANDLE h = FindFirstFileA(pattern, &fd); if (h == INVALID_HANDLE_VALUE) return l; do { if (strcmp(fd.cFileName, ".") == 0) continue; char entry[600]; if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) snprintf(entry, sizeof(entry), "%s/", fd.cFileName); else snprintf(entry, sizeof(entry), "%s", fd.cFileName); zl_list_append(&l, zl_any_str(zl_mem_strdup(entry))); } while (FindNextFileA(h, &fd)); FindClose(h); return l; }')
    out.append('static inline int zl_fs_mkdir(const char* path) { if (!path || !path[0]) return -1; if (CreateDirectoryA(path, NULL)) return 0; DWORD err = GetLastError(); if (err == ERROR_ALREADY_EXISTS) return 0; return -2; }')
    out.append('static inline bool zl_fs_is_dir(const char* path) { if (!path) return false; DWORD a = GetFileAttributesA(path); if (a == INVALID_FILE_ATTRIBUTES) return false; return (a & FILE_ATTRIBUTE_DIRECTORY) != 0; }')
    out.append("#else")
    out.append("#include <dirent.h>")
    out.append("#include <sys/stat.h>")
    out.append("#include <errno.h>")
    out.append('static inline ZL_List zl_fs_list_dir(const char* path) { ZL_List l = zl_list_new(); if (!path || !path[0]) return l; DIR* d = opendir(path); if (!d) return l; struct dirent* e; while ((e = readdir(d)) != NULL) { if (strcmp(e->d_name, ".") == 0) continue; char entry[600]; char full[1100]; snprintf(full, sizeof(full), "%s/%s", path, e->d_name); struct stat st; if (stat(full, &st) == 0 && S_ISDIR(st.st_mode)) snprintf(entry, sizeof(entry), "%s/", e->d_name); else snprintf(entry, sizeof(entry), "%s", e->d_name); zl_list_append(&l, zl_any_str(zl_mem_strdup(entry))); } closedir(d); return l; }')
    out.append('static inline int zl_fs_mkdir(const char* path) { if (!path || !path[0]) return -1; if (mkdir(path, 0755) == 0) return 0; if (errno == EEXIST) return 0; return -2; }')
    out.append('static inline bool zl_fs_is_dir(const char* path) { if (!path) return false; struct stat st; if (stat(path, &st) != 0) return false; return S_ISDIR(st.st_mode); }')
    out.append("#endif")
    out.append("static inline const char* zl_str_lower(const char* s) { if (!s) s = \"\"; size_t n = strlen(s); char* out = (char*)malloc(n + 1); if (!out) { fprintf(stderr, \"lower allocation failed\\n\"); exit(1); } for (size_t i = 0; i < n; i++) { char ch = s[i]; out[i] = (ch >= 'A' && ch <= 'Z') ? (char)(ch + 32) : ch; } out[n] = 0; return out; }")
    out.append("static inline const char* zl_str_upper(const char* s) { if (!s) s = \"\"; size_t n = strlen(s); char* out = (char*)malloc(n + 1); if (!out) { fprintf(stderr, \"upper allocation failed\\n\"); exit(1); } for (size_t i = 0; i < n; i++) { char ch = s[i]; out[i] = (ch >= 'a' && ch <= 'z') ? (char)(ch - 32) : ch; } out[n] = 0; return out; }")
    out.append("static inline const char* zl_str_trim(const char* s) { if (!s) s = \"\"; const char* start = s; while (*start == ' ' || *start == '\\t' || *start == '\\r' || *start == '\\n') start++; const char* end = s + strlen(s); while (end > start && (end[-1] == ' ' || end[-1] == '\\t' || end[-1] == '\\r' || end[-1] == '\\n')) end--; int len = (int)(end - start); return zl_str_substring(start, 0, len); }")
    out.append("static inline const char* zl_str_repeat(const char* s, int count) { if (!s) s = \"\"; if (count <= 0) return \"\"; size_t n = strlen(s); size_t total = n * (size_t)count; char* out = (char*)malloc(total + 1); if (!out) { fprintf(stderr, \"repeat allocation failed\\n\"); exit(1); } char* p = out; for (int i = 0; i < count; i++) { memcpy(p, s, n); p += n; } out[total] = 0; return out; }")
    out.append("static inline const char* zl_str_replace(const char* s, const char* old, const char* repl) { if (!s) s = \"\"; if (!old || old[0] == 0) return zl_mem_strdup(s); if (!repl) repl = \"\"; size_t oldn = strlen(old), repln = strlen(repl); int hits = 0; const char* cur = s; const char* hit; while ((hit = strstr(cur, old)) != NULL) { hits++; cur = hit + oldn; } size_t outn = strlen(s) + (size_t)hits * (repln > oldn ? repln - oldn : 0) + 1; if (repln < oldn) outn = strlen(s) - (size_t)hits * (oldn - repln) + 1; char* out = (char*)malloc(outn); if (!out) { fprintf(stderr, \"replace allocation failed\\n\"); exit(1); } char* dst = out; cur = s; while ((hit = strstr(cur, old)) != NULL) { size_t keep = (size_t)(hit - cur); memcpy(dst, cur, keep); dst += keep; memcpy(dst, repl, repln); dst += repln; cur = hit + oldn; } strcpy(dst, cur); return out; }")
    out.append("static inline const char* zl_str_reverse(const char* s) { if (!s) s = \"\"; size_t n = strlen(s); char* out = (char*)malloc(n + 1); if (!out) { fprintf(stderr, \"reverse allocation failed\\n\"); exit(1); } for (size_t i = 0; i < n; i++) out[i] = s[n - 1 - i]; out[n] = 0; return out; }")
    out.append('static inline void zl_print_any(Any v) { switch (v.kind) { case 1: printf("%lld\\n", v.i); break; case 2: printf("%g\\n", v.f); break; case 3: printf("%s\\n", v.s); break; case 4: printf("%s\\n", v.b ? "true" : "false"); break; case 5: if (v.p.addr == NULL) printf("None\\n"); else if (!zl_ptr_is_valid(v.p)) printf("Freed\\n"); else printf("%p\\n", (void*)v.p.addr); break; case 6: if (v.l == NULL) printf("List(None)\\n"); else printf("List(len=%d)\\n", v.l->len); break; case 7: printf("%s\\n", v.object_type ? v.object_type : "struct"); break; default: printf("Any(?)\\n"); } }')
    out.append('static inline void zl_print_list(ZL_List* l) { printf("List(len=%d)\\n", l->len); }')
    out.append("")
    if needs_python_cli:
        out.append("static inline void zl_quote_arg(char* out_arg, size_t cap, const char* in_arg) { size_t j = 0; if (cap == 0) return; out_arg[j++] = \'\"\'; for (size_t i = 0; in_arg && in_arg[i] && j + 3 < cap; i++) { char ch = in_arg[i]; if (ch == \'\"\') ch = \'_\'; out_arg[j++] = ch; } if (j + 1 < cap) out_arg[j++] = \'\"\'; out_arg[j < cap ? j : cap - 1] = 0; }")
        out.append("static inline int zl_py_cmd0(const char* module, const char* op) { char cmd[512]; snprintf(cmd, sizeof(cmd), \"python -m %s %s\", module, op); return system(cmd); }")
        out.append("static inline int zl_py_cmd2(const char* module, const char* op, const char* a, const char* b) { char qa[512], qb[512], cmd[1600]; zl_quote_arg(qa, sizeof(qa), a); zl_quote_arg(qb, sizeof(qb), b); snprintf(cmd, sizeof(cmd), \"python -m %s %s %s %s\", module, op, qa, qb); return system(cmd); }")
        out.append("static inline int zl_py_cmd3i(const char* module, const char* op, const char* a, const char* b, int x) { char qa[512], qb[512], cmd[1700]; zl_quote_arg(qa, sizeof(qa), a); zl_quote_arg(qb, sizeof(qb), b); snprintf(cmd, sizeof(cmd), \"python -m %s %s %s %s %d\", module, op, qa, qb, x); return system(cmd); }")
        out.append("static inline int zl_py_cmd4ii(const char* module, const char* op, const char* a, const char* b, int x, int y) { char qa[512], qb[512], cmd[1800]; zl_quote_arg(qa, sizeof(qa), a); zl_quote_arg(qb, sizeof(qb), b); snprintf(cmd, sizeof(cmd), \"python -m %s %s %s %s %d %d\", module, op, qa, qb, x, y); return system(cmd); }")
        out.append("static inline int zl_py_cmd3s(const char* module, const char* op, const char* a, const char* b, const char* c) { char qa[512], qb[512], qc[512], cmd[2100]; zl_quote_arg(qa, sizeof(qa), a); zl_quote_arg(qb, sizeof(qb), b); zl_quote_arg(qc, sizeof(qc), c); snprintf(cmd, sizeof(cmd), \"python -m %s %s %s %s %s\", module, op, qa, qb, qc); return system(cmd); }")
        out.append("static inline int zl_py_cmd3sf(const char* module, const char* op, const char* a, double x, const char* b) { char qa[512], qb[512], cmd[2100]; zl_quote_arg(qa, sizeof(qa), a); zl_quote_arg(qb, sizeof(qb), b); snprintf(cmd, sizeof(cmd), \"python -m %s %s %s %.17g %s\", module, op, qa, x, qb); return system(cmd); }")
    if needs_python_cli:
        out.append("static inline int zl_cv_info(void) { return zl_py_cmd0(\"zyenlang.cv_cli\", \"info\"); }")
        out.append("static inline int zl_cv_readable(const char* input) { char q[512], cmd[1200]; zl_quote_arg(q, sizeof(q), input); snprintf(cmd, sizeof(cmd), \"python -m zyenlang.cv_cli readable %s\", q); return system(cmd); }")
        out.append("static inline int zl_cv_gray(const char* input, const char* output) { return zl_py_cmd2(\"zyenlang.cv_cli\", \"gray\", input, output); }")
        out.append("static inline int zl_cv_resize(const char* input, const char* output, int w, int h) { return zl_py_cmd4ii(\"zyenlang.cv_cli\", \"resize\", input, output, w, h); }")
        out.append("static inline int zl_cv_blur(const char* input, const char* output, int k) { return zl_py_cmd3i(\"zyenlang.cv_cli\", \"blur\", input, output, k); }")
        out.append("static inline int zl_cv_canny(const char* input, const char* output, int low, int high) { return zl_py_cmd4ii(\"zyenlang.cv_cli\", \"canny\", input, output, low, high); }")
        out.append("static inline int zl_cv_threshold(const char* input, const char* output, int t) { return zl_py_cmd3i(\"zyenlang.cv_cli\", \"threshold\", input, output, t); }")
        out.append("static inline int zl_gpu_info(void) { return zl_py_cmd0(\"zyenlang.gpu_cli\", \"info\"); }")
        out.append("static inline bool zl_gpu_has_nvidia(void) { return zl_py_cmd0(\"zyenlang.gpu_cli\", \"has-nvidia\") == 0; }")
        out.append("static inline bool zl_gpu_has_torch_cuda(void) { return zl_py_cmd0(\"zyenlang.gpu_cli\", \"has-torch-cuda\") == 0; }")
        out.append("static inline bool zl_gpu_has_opencv_cuda(void) { return zl_py_cmd0(\"zyenlang.gpu_cli\", \"has-opencv-cuda\") == 0; }")
        out.append("static inline int zl_gpu_cv_gray(const char* input, const char* output) { return zl_py_cmd2(\"zyenlang.cv_cli\", \"gpu-gray\", input, output); }")
        out.append("static inline int zl_gpu_vector_add_csv(const char* a, const char* b, const char* output) { return zl_py_cmd3s(\"zyenlang.gpu_cli\", \"vector-add-csv\", a, b, output); }")
        out.append("static inline int zl_gpu_vector_scale_csv(const char* input, double scale, const char* output) { return zl_py_cmd3sf(\"zyenlang.gpu_cli\", \"vector-scale-csv\", input, scale, output); }")
        out.append("static inline int zl_gpu_dot_csv(const char* a, const char* b, const char* output) { return zl_py_cmd3s(\"zyenlang.gpu_cli\", \"dot-csv\", a, b, output); }")
    out.append("")

    # ZL_Function has one uniform C representation, so complete struct bodies
    # can be emitted before signature-specific call helpers. This is required
    # for helpers that return a user struct by value.
    # ZEP-0013: scan for nested fns BEFORE collect_fn_typedefs so the lifted
    # fn-type signatures get registered too.
    scan_nested_fns(ctx, lines)
    collect_fn_typedefs(ctx, lines)
    out.extend(emit_struct_forward_decls(ctx))
    out.extend(emit_struct_defs(ctx))
    out.extend(emit_struct_management(ctx))
    out.extend(emit_owned_pointer_factories(ctx))
    out.extend(emit_any_struct_box_helpers(ctx))
    out.extend(emit_fn_typedefs(ctx))
    out.extend(emit_lifted_env_structs(ctx))
    out.extend(emit_function_prototypes(ctx))
    out.extend(emit_structural_dispatch_helpers(ctx))
    out.extend(emit_owned_argument_wrappers(ctx))
    out.extend(emit_lifted_fn_prototypes(ctx))
    # ZEP-0013: thunks need the prototypes above, fnval constants land here.
    out.extend(emit_fn_thunks(ctx))

    i = 0
    while i < len(lines):
        line_no, line = lines[i]
        sm = re.match(r"struct\s+([A-Za-z_]\w*)\s*\{\s*$", line)
        if sm:
            struct_name = sm.group(1)
            i += 1
            while i < len(lines):
                member_no, member_line = lines[i]
                if member_line == "}":
                    i += 1
                    break
                # Skip fields. They were already emitted in emit_struct_defs().
                # ZEP-0006: also accept `let this.name: type = default;`.
                # ZEP-0010: type may be `fn(...)->T`.
                if parse_struct_field_line(member_line):
                    i += 1
                    continue
                if re.match(r"(?:let\s+)?[A-Za-z_]\w*\s*:\s*" + TYPE_RX + r"\s*(?:=\s*.+?)?\s*;\s*$", member_line):
                    raise ZyenError(f"line {member_no}: struct field must use `let this.name: type;` or `let this.name: type = default;`, for example `let this.list_len: int;`")
                parsed_method = parse_fn_header_line(member_line)
                if parsed_method and parsed_method[3] == "defn":
                    method_name, params_text, ret_type, _kind = parsed_method
                    params, _defaults = parse_params(params_text, member_no)
                    c_name = f"{struct_name}_{method_name}"
                    c_params = {"this": f"ptrstruct<{struct_name}>"}
                    c_params.update(params)
                    reset_function_context(ctx, c_name, c_params)
                    precollect_function_list_item_types(lines, i + 1, ctx)
                    out.append(c_function_signature(c_name, ret_type, c_params) + " {")
                    for param_name in c_params:
                        out.append(f"    (void){param_name};")
                    i = emit_function_body(lines, i + 1, out, ctx)
                    continue
                raise ZyenError(f"line {member_no}: invalid struct member")
            continue

        parsed = parse_fn_header_line(line)
        if parsed:
            name, params_text, ret_type, kind = parsed
            if kind == "decl":
                # Prototype only. collect_signatures() already registered it.
                i += 1
                continue
            params, _defaults = parse_params(params_text, line_no)
            reset_function_context(ctx, name, params)
            precollect_function_list_item_types(lines, i + 1, ctx)
            out.append(c_function_signature(name, ret_type, params) + " {")
            for param_name in params:
                out.append(f"    (void){param_name};")
            i = emit_function_body(lines, i + 1, out, ctx)
            continue

        if line.startswith("class "):
            raise ZyenError(f"line {line_no}: `class` is planned for v0.2; v0.1 supports `struct`")
        if line.startswith("let ") or line.startswith("const ") or line.startswith("set "):
            raise ZyenError(f"line {line_no}: top-level variables are not supported yet; put variables inside `fn main()` or use a zero-argument function for constants")
        if line and line != "}":
            raise ZyenError(f"line {line_no}: top-level statement is not allowed; only import, struct, and fn are allowed at file scope")
        i += 1

    # ZEP-0013: lifted closure bodies last so they see every prototype.
    out.extend(emit_lifted_fn_bodies(ctx, lines))

    return "\n".join(out).rstrip() + "\n"


def resolve_import_path(base_dir: Path, import_name: str) -> Path:
    candidate = (base_dir / import_name).resolve()
    if candidate.exists():
        return candidate
    if candidate.suffix == "":
        candidate_zy = candidate.with_suffix(".zy")
        if candidate_zy.exists():
            return candidate_zy
    raise FileNotFoundError(candidate)


def resolve_std_import_path(import_name: str) -> Path:
    """Resolve `import <...>;` from the bundled standard library.

    Supported forms:
      import <std/math>;
      import <std/math.zy>;
      import <math>;            // shorthand for std/math.zy
      import <std/math> as m;   // alias for calls like m.abs_int(...)

    v0.1.17 supports both source-tree execution and installed-module execution:
      - source tree: <project>/std/math.zy
      - installed package: zyenlang/std/math.zy
    """
    name = import_name.strip().replace("\\", "/")
    roots = [
        Path(__file__).resolve().parent,       # installed package: zyenlang/std
        Path(__file__).resolve().parents[1],  # source tree: project/std
    ]

    checked: List[Path] = []
    for root in roots:
        if name.startswith("std/"):
            candidate = root / name
        else:
            candidate = root / "std" / name
        candidate = candidate.resolve()
        checked.append(candidate)
        if candidate.exists():
            return candidate
        if candidate.suffix == "":
            candidate_zy = candidate.with_suffix(".zy")
            checked.append(candidate_zy)
            if candidate_zy.exists():
                return candidate_zy

    raise FileNotFoundError(checked[-1])


def module_name_from_path(path: Path) -> str:
    return path.stem


def module_prefix(module_name: str) -> str:
    safe = re.sub(r"\W+", "_", module_name.strip())
    if not re.match(r"^[A-Za-z_]", safe):
        safe = "m_" + safe
    return f"zlmod_{safe}_"


def _brace_delta_outside_strings(line: str) -> int:
    delta = 0
    in_str = False
    esc = False
    for ch in line:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            delta += 1
        elif ch == "}":
            delta -= 1
    return delta


def collect_function_names_from_source(source: str) -> List[str]:
    """Collect only top-level function names from a source file.

    Struct methods are intentionally not namespaced. This lets a std module
    expose a struct such as `TestStats` whose methods are called as
    `stats.eq_int(...)` even though top-level module functions are still called
    through `test.new_stats(...)`.
    """
    names: List[str] = []
    depth = 0
    for _line_no, line in clean_lines(source):
        if depth == 0:
            m = re.match(r"fn\s+([A-Za-z_]\w*)\s*\(", line)
            if m and m.group(1) not in names:
                names.append(m.group(1))
        depth += _brace_delta_outside_strings(line)
        if depth < 0:
            depth = 0
    return names

def replace_module_calls(line: str, aliases: Dict[str, str]) -> str:
    """Convert namespace calls like `math.abs_int(x)` to valid backend names.

    This only rewrites dotted function calls, not struct fields like `p.x`.
    """
    for alias, prefix in sorted(aliases.items(), key=lambda kv: len(kv[0]), reverse=True):
        line = re.sub(
            rf"\b{re.escape(alias)}\.([A-Za-z_]\w*)\s*\(",
            lambda m, p=prefix: f"{p}{m.group(1)}(",
            line,
        )
    return line


def prefix_module_body(source: str, module_name: str) -> str:
    """Namespace top-level functions in a module body.

    `fn abs_int(...)` becomes `fn zlmod_math_abs_int(...)`. Struct method
    names are not rewritten; method dispatch uses the struct type and stays
    source-like (`stats.eq_int(...)`).
    """
    prefix = module_prefix(module_name)
    names = collect_function_names_from_source(source)
    lines = source.splitlines()
    out_lines: List[str] = []
    depth = 0
    for line in lines:
        if depth == 0:
            for name in names:
                line = re.sub(rf"\bfn\s+{re.escape(name)}\s*\(", f"fn {prefix}{name}(" , line)
        out_lines.append(line)
        depth += _brace_delta_outside_strings(strip_comment(line))
        if depth < 0:
            depth = 0
    out = "\n".join(out_lines) + ("\n" if source.endswith("\n") else "")
    for name in names:
        # Top-level declarations were already renamed above. Do not mistake a
        # same-named struct method declaration (for example `fn open()`) for a
        # call to the module's top-level `open()` function.
        out = re.sub(rf"(?<![\.\w])(?<!fn ){re.escape(name)}\s*\(", f"{prefix}{name}(" , out)
    return out

def parse_std_import(cleaned: str):
    return re.match(r'^import\s+<([^>]+)>\s*(?:as\s+([A-Za-z_]\w*))?\s*;\s*$', cleaned)


def parse_user_import(cleaned: str):
    return re.match(r'^import\s+"([^"]+)"\s*(?:as\s+([A-Za-z_]\w*))?\s*;\s*$', cleaned)


def parse_legacy_c_module_import(cleaned: str):
    """Recognize the removed pre-v0.1.49 native import for migration errors."""
    return re.match(r'^import\s+c_module\.load\(\s*"([^"]+)"\s*\)\s+as\s+([A-Za-z_]\w*)\s*;\s*$', cleaned)


@dataclass(frozen=True)
class CModuleBinding:
    is_field: bool
    name: str
    type_alias: str
    load_alias: str
    manifest_text: str


def parse_c_module_binding(raw: str) -> Optional[CModuleBinding]:
    """Parse a dependent c_module declaration without resolving its alias."""
    line = strip_comment(raw).strip()
    match = re.match(
        r'^let\s+(?:(this)\.)?([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)\.Module\s*=\s*'
        r'([A-Za-z_]\w*)\.load\(\s*"([^"]+)"\s*\)\s*;\s*$',
        line,
    )
    if not match:
        return None
    return CModuleBinding(
        is_field=match.group(1) == "this",
        name=match.group(2),
        type_alias=match.group(3),
        load_alias=match.group(4),
        manifest_text=match.group(5),
    )


def _c_module_aliases(aliases: Dict[str, str]) -> Set[str]:
    prefix = module_prefix("c_module")
    return {alias for alias, value in aliases.items() if value == prefix}


def _legacy_c_module_error(path: Path, line_no: int) -> ZyenError:
    return ZyenError(
        f'{path}:{line_no}: `import c_module.load("...") as name;` was removed; '
        'use `import <std/c_module> as c_module;` and '
        '`let value: c_module.Module = c_module.load("module.zlcm.h");`'
    )


def _resolve_c_module_manifest(source_path: Path, manifest_text: str, line_no: int) -> Path:
    manifest = Path(manifest_text)
    if not manifest.is_absolute():
        manifest = source_path.parent / manifest
    manifest = manifest.resolve()
    if not manifest.exists():
        raise ZyenError(f'{source_path}:{line_no}: c_module template not found: "{manifest_text}"')
    if not native_c_module.is_native_module_path(manifest):
        raise ZyenError(
            f'{source_path}:{line_no}: c_module.load expects a `.zlcm.h` or `.zlcm.json` template, got "{manifest_text}"'
        )
    return manifest


def expand_c_module_binding(
    raw: str,
    source_path: Path,
    line_no: int,
    aliases: Dict[str, str],
    native_seen: Set[Path],
) -> Tuple[str, str]:
    """Rewrite one dependent declaration and return optional wrapper source."""
    binding = parse_c_module_binding(raw)
    c_aliases = _c_module_aliases(aliases)
    if binding is not None:
        related = binding.type_alias in c_aliases or binding.load_alias in c_aliases
        if binding.type_alias != binding.load_alias and related:
            raise ZyenError(
                f"{source_path}:{line_no}: c_module type alias `{binding.type_alias}` and load alias "
                f"`{binding.load_alias}` must match"
            )
        alias = binding.type_alias
        if alias not in c_aliases:
            raise ZyenError(
                f"{source_path}:{line_no}: {alias}.load requires "
                f"`import <std/c_module> as {alias};` before this declaration"
            )

        manifest = _resolve_c_module_manifest(source_path, binding.manifest_text, line_no)
        try:
            hidden_type = native_c_module.hidden_struct_name(manifest)
            wrapper = ""
            if manifest not in native_seen:
                _module, wrapper = native_c_module.generate_module_text(
                    manifest,
                    struct_name=hidden_type,
                    include_load=False,
                    source_label=binding.manifest_text,
                )
                native_seen.add(manifest)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ZyenError(f"{source_path}:{line_no}: invalid c_module template `{binding.manifest_text}`: {exc}") from exc

        target = f"this.{binding.name}" if binding.is_field else binding.name
        indent = raw[: len(raw) - len(raw.lstrip())]
        initializer = f"{hidden_type} {{}}" if binding.is_field else hidden_type
        rewritten = f"{indent}let {target}: {hidden_type} = {initializer};"
        return rewritten, wrapper

    cleaned = strip_comment(raw).strip()
    for alias in sorted(c_aliases):
        if re.search(rf"\b{re.escape(alias)}\.load\s*\(", cleaned):
            raise ZyenError(
                f"{source_path}:{line_no}: {alias}.load requires a string literal and must directly initialize "
                f"`let value: {alias}.Module = {alias}.load(\"module.zlcm.h\");`"
            )
        if re.search(rf"\b{re.escape(alias)}\.Module\b", cleaned):
            raise ZyenError(
                f"{source_path}:{line_no}: {alias}.Module is only valid in a field or local declaration "
                f"directly initialized by {alias}.load(\"module.zlcm.h\")"
            )
    if re.search(r"\bc_module\.(?:Module\b|load\s*\()", cleaned) and "c_module" not in c_aliases:
        raise ZyenError(
            f"{source_path}:{line_no}: c_module requires `import <std/c_module> as c_module;` before use"
        )
    return replace_module_calls(raw, aliases), ""



def load_user_module_as_alias(
    input_path: Path,
    alias: str,
    seen: Set[Path],
    stack: List[Path],
    seen_aliases: Set[Tuple[Path, str]],
    native_seen: Set[Path],
) -> str:
    """Load a user .zy file as a local namespace.

    Example:
        import "list.zy" as list;
        list.say_hi();

    The backend rewrites this to a stable C symbol such as:
        zlmod_list_say_hi();

    In v0.1.30, namespacing applies to top-level functions in the imported
    file. Struct types remain global for now, which keeps the rule simple and
    avoids renaming data layouts behind the user's back.
    """
    path = input_path.resolve()
    key = (path, alias)
    if path in stack:
        chain = " -> ".join(p.name for p in stack + [path])
        raise ZyenError(f"circular import detected: {chain}")
    if key in seen_aliases:
        return ""
    if not path.exists():
        raise FileNotFoundError(path)

    if native_c_module.is_native_module_path(path):
        raise ZyenError(
            f'cannot import native template `{path.name}` directly; use `import <std/c_module> as c_module;` '
            f'and `let value: c_module.Module = c_module.load("{path.name}");`'
        )

    seen_aliases.add(key)
    stack.append(path)

    local_aliases: Dict[str, str] = {}
    dependency_pieces: List[str] = []
    native_pieces: List[str] = []
    body_lines: List[str] = []

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        cleaned = strip_comment(raw).strip()
        c_module_import = parse_legacy_c_module_import(cleaned)
        user_import = parse_user_import(cleaned)
        std_import = parse_std_import(cleaned)
        if c_module_import:
            raise _legacy_c_module_error(path, line_no)
        if user_import:
            import_path = resolve_import_path(path.parent, user_import.group(1))
            import_alias = user_import.group(2)
            if import_alias:
                local_aliases[import_alias] = module_prefix(import_alias)
                dependency_pieces.append(load_user_module_as_alias(import_path, import_alias, seen, stack, seen_aliases, native_seen))
            else:
                # A plain user import inside a namespaced module is still global,
                # preserving the original v0.1 behavior.
                dependency_pieces.append(load_source_with_imports(import_path, seen=seen, stack=stack, seen_aliases=seen_aliases, native_seen=native_seen))
            continue
        if std_import:
            import_path = resolve_std_import_path(std_import.group(1))
            imported_name = module_name_from_path(import_path)
            std_alias = std_import.group(2) or imported_name
            local_aliases[std_alias] = module_prefix(imported_name)
            if imported_name == "prelude":
                dependency_pieces.append(load_prelude_into_aliases(import_path, local_aliases, seen, stack, native_seen))
            else:
                dependency_pieces.append(load_std_module(import_path, imported_name, seen, stack, native_seen))
            continue
        if cleaned.startswith("import "):
            raise ZyenError(f"{path}:{line_no}: import must use a `.zy` file or std module")
        expanded, wrapper = expand_c_module_binding(raw, path, line_no, local_aliases, native_seen)
        if wrapper:
            native_pieces.append(wrapper)
        body_lines.append(expanded)

    stack.pop()
    body = "\n".join(body_lines) + "\n"
    namespaced_body = prefix_module_body(body, alias)
    all_dependencies = dependency_pieces + native_pieces
    return "\n".join(piece for piece in all_dependencies if piece) + "\n" + namespaced_body

def load_std_module(
    input_path: Path,
    module_name: str,
    seen: Set[Path],
    stack: List[Path],
    native_seen: Set[Path],
) -> str:
    """Load and namespace a standard-library module.

    Standard library functions never enter the global namespace. A caller uses
    `math.abs_int(...)`; the backend compiles that as `zlmod_math_abs_int(...)`.
    """
    path = input_path.resolve()
    if path in stack:
        chain = " -> ".join(p.name for p in stack + [path])
        raise ZyenError(f"circular import detected: {chain}")
    if path in seen:
        return ""
    if not path.exists():
        raise FileNotFoundError(path)

    seen.add(path)
    stack.append(path)

    local_aliases: Dict[str, str] = {}
    dependency_pieces: List[str] = []
    native_pieces: List[str] = []
    body_lines: List[str] = []

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        cleaned = strip_comment(raw).strip()
        c_module_import = parse_legacy_c_module_import(cleaned)
        user_import = parse_user_import(cleaned)
        std_import = parse_std_import(cleaned)
        if c_module_import:
            raise _legacy_c_module_error(path, line_no)
        if user_import:
            import_path = resolve_import_path(path.parent, user_import.group(1))
            # User imports inside std are rare; keep them global for now.
            dependency_pieces.append(load_source_with_imports(import_path, seen=seen, stack=stack, native_seen=native_seen))
            continue
        if std_import:
            import_path = resolve_std_import_path(std_import.group(1))
            imported_name = module_name_from_path(import_path)
            alias = std_import.group(2) or imported_name
            local_aliases[alias] = module_prefix(imported_name)

            # `prelude` is only an aggregator; importing it inside a module simply
            # loads its member modules and exposes their aliases locally.
            if imported_name == "prelude":
                dependency_pieces.append(load_prelude_into_aliases(import_path, local_aliases, seen, stack, native_seen))
            else:
                dependency_pieces.append(load_std_module(import_path, imported_name, seen, stack, native_seen))
            continue
        if cleaned.startswith("import "):
            raise ZyenError(f"{path}:{line_no}: import must look like `import \"file.zy\";` or `import <std/name>;`")
        expanded, wrapper = expand_c_module_binding(raw, path, line_no, local_aliases, native_seen)
        if wrapper:
            native_pieces.append(wrapper)
        body_lines.append(expanded)

    stack.pop()
    body = "\n".join(body_lines) + "\n"
    namespaced_body = prefix_module_body(body, module_name)
    all_dependencies = dependency_pieces + native_pieces
    return "\n".join(piece for piece in all_dependencies if piece) + "\n" + namespaced_body


def load_prelude_into_aliases(
    input_path: Path,
    aliases: Dict[str, str],
    seen: Set[Path],
    stack: List[Path],
    native_seen: Set[Path],
) -> str:
    """Load std/prelude as an alias aggregator.

    `import <std/prelude>;` does not create `prelude.foo()`; it makes the
    bundled modules available as `math.foo()`, `check.foo()`, etc.
    """
    path = input_path.resolve()
    if path in stack:
        chain = " -> ".join(p.name for p in stack + [path])
        raise ZyenError(f"circular import detected: {chain}")
    if not path.exists():
        raise FileNotFoundError(path)

    stack.append(path)
    pieces: List[str] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        cleaned = strip_comment(raw).strip()
        std_import = parse_std_import(cleaned)
        if std_import:
            import_path = resolve_std_import_path(std_import.group(1))
            imported_name = module_name_from_path(import_path)
            if imported_name == "prelude":
                raise ZyenError(f"{path}:{line_no}: prelude cannot import itself")
            alias = std_import.group(2) or imported_name
            aliases[alias] = module_prefix(imported_name)
            pieces.append(load_std_module(import_path, imported_name, seen, stack, native_seen))
            continue
        if cleaned and not cleaned.startswith("//"):
            raise ZyenError(f"{path}:{line_no}: prelude may only contain std imports")
    stack.pop()
    return "\n".join(piece for piece in pieces if piece) + "\n"


def load_source_with_imports(
    input_path: Path,
    seen: Optional[Set[Path]] = None,
    stack: Optional[List[Path]] = None,
    seen_aliases: Optional[Set[Tuple[Path, str]]] = None,
    native_seen: Optional[Set[Path]] = None,
) -> str:
    """Load a .zy file and expand imports.

    - `import "file.zy";` is a user import and remains global.
    - `import "file.zy" as mod;` is namespaced; call functions as `mod.fn(...)`.
    - `import <std/math>;` is namespaced; call functions as `math.abs_int(...)`.
    - `import <std/math> as m;` gives an alias, e.g. `m.abs_int(...)`.
    - `import <std/prelude>;` loads common std modules and exposes their module
      namespaces (`math.*`, `check.*`, `control.*`, `motor.*`).
    """
    seen = seen if seen is not None else set()
    stack = stack if stack is not None else []
    seen_aliases = seen_aliases if seen_aliases is not None else set()
    native_seen = native_seen if native_seen is not None else set()
    path = input_path.resolve()

    if path in stack:
        chain = " -> ".join(p.name for p in stack + [path])
        raise ZyenError(f"circular import detected: {chain}")
    if path in seen:
        return ""
    if not path.exists():
        raise FileNotFoundError(path)

    if native_c_module.is_native_module_path(path):
        raise ZyenError(
            f'cannot compile native template `{path.name}` directly; load it from a `.zy` file with '
            '`import <std/c_module> as c_module;` and `c_module.load("...")`'
        )

    seen.add(path)
    stack.append(path)
    aliases: Dict[str, str] = {}
    pieces: List[str] = []
    native_pieces: List[str] = []

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        cleaned = strip_comment(raw).strip()
        c_module_import = parse_legacy_c_module_import(cleaned)
        user_import = parse_user_import(cleaned)
        std_import = parse_std_import(cleaned)
        if c_module_import:
            raise _legacy_c_module_error(path, line_no)
        if user_import:
            import_path = resolve_import_path(path.parent, user_import.group(1))
            import_alias = user_import.group(2)
            if import_alias:
                aliases[import_alias] = module_prefix(import_alias)
                pieces.append(f"// begin import {import_path} as {import_alias}")
                pieces.append(load_user_module_as_alias(import_path, import_alias, seen, stack, seen_aliases, native_seen))
                pieces.append(f"// end import {import_path} as {import_alias}")
            else:
                pieces.append(f"// begin import {import_path}")
                pieces.append(load_source_with_imports(import_path, seen=seen, stack=stack, seen_aliases=seen_aliases, native_seen=native_seen))
                pieces.append(f"// end import {import_path}")
            continue
        if std_import:
            import_path = resolve_std_import_path(std_import.group(1))
            imported_name = module_name_from_path(import_path)
            alias = std_import.group(2) or imported_name

            if imported_name == "prelude":
                pieces.append(f"// begin std prelude {import_path}")
                pieces.append(load_prelude_into_aliases(import_path, aliases, seen, stack, native_seen))
                pieces.append(f"// end std prelude {import_path}")
            else:
                aliases[alias] = module_prefix(imported_name)
                pieces.append(f"// begin std import {import_path} as {alias}")
                pieces.append(load_std_module(import_path, imported_name, seen, stack, native_seen))
                pieces.append(f"// end std import {import_path}")
            continue
        if cleaned.startswith("import "):
            raise ZyenError(f"{path}:{line_no}: import must use a `.zy` file or std module")
        expanded, wrapper = expand_c_module_binding(raw, path, line_no, aliases, native_seen)
        if wrapper:
            native_pieces.append(wrapper)
        pieces.append(expanded)

    stack.pop()
    return "\n".join(native_pieces + pieces) + "\n"

def build_file(input_path: Path, output_path: Path) -> None:
    source = load_source_with_imports(input_path)
    c_code = transpile(source)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(c_code, encoding="utf-8")


def collect_native_c_metadata(
    input_path: Path,
    seen: Optional[Set[Path]] = None,
    stack: Optional[List[Path]] = None,
) -> dict:
    """Collect native C metadata from .zy imports and .zlcm manifests."""
    seen = seen if seen is not None else set()
    stack = stack if stack is not None else []
    path = input_path.resolve()
    if path in stack:
        chain = " -> ".join(p.name for p in stack + [path])
        raise ZyenError(f"circular import detected while collecting C metadata: {chain}")
    if path in seen:
        return native_c_module.empty_native_metadata()
    if not path.exists():
        raise FileNotFoundError(path)

    if native_c_module.is_native_module_path(path):
        seen.add(path)
        return native_c_module.native_metadata_from_manifest(path)

    seen.add(path)
    stack.append(path)
    try:
        meta = native_c_module.native_metadata_from_zy(path)
        aliases: Dict[str, str] = {}
        for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            cleaned = strip_comment(raw).strip()
            c_module_import = parse_legacy_c_module_import(cleaned)
            user_import = parse_user_import(cleaned)
            std_import = parse_std_import(cleaned)
            if c_module_import:
                raise _legacy_c_module_error(path, line_no)
            elif user_import:
                child = resolve_import_path(path.parent, user_import.group(1))
            elif std_import:
                child = resolve_std_import_path(std_import.group(1))
                imported_name = module_name_from_path(child)
                alias = std_import.group(2) or imported_name
                aliases[alias] = module_prefix(imported_name)
            else:
                binding = parse_c_module_binding(raw)
                if binding is None or binding.type_alias not in _c_module_aliases(aliases):
                    continue
                child = _resolve_c_module_manifest(path, binding.manifest_text, line_no)
            try:
                child_meta = collect_native_c_metadata(child, seen, stack)
            except FileNotFoundError as e:
                raise FileNotFoundError(f"{path}:{line_no}: {e}") from e
            native_c_module.merge_native_metadata(meta, child_meta)
    finally:
        stack.pop()
    return native_c_module.finalize_native_metadata(meta)


def compile_c(c_path: Path, exe_path: Path, native_meta: Optional[dict] = None) -> None:
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    meta = native_meta or native_c_module.empty_native_metadata()
    cmd = native_c_module.gcc_command(native_c_module.finalize_native_metadata(meta), c_path, exe_path)
    subprocess.run(cmd, check=True)


def run_source(input_path: Path) -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as tmp:
        tmp_path = Path(tmp)
        c_path = tmp_path / (input_path.stem + ".c")
        exe_name = input_path.stem + (".exe" if sys.platform.startswith("win") else "")
        exe_path = tmp_path / exe_name
        build_file(input_path, c_path)
        compile_c(c_path, exe_path, collect_native_c_metadata(input_path))
        result = subprocess.run([str(exe_path)], check=False)
        return result.returncode


def main(argv: Optional[List[str]] = None) -> int:
    from . import __version__ as _pkg_version
    parser = argparse.ArgumentParser(prog="zy", description=f"ZyenLang v{_pkg_version} transpiler")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="transpile .zy to .c")
    p_build.add_argument("input")
    p_build.add_argument("-o", "--output", default=None)
    p_build.add_argument("--exe", default=None, help="also compile generated C to executable")

    p_run = sub.add_parser("run", help="transpile, compile, and run .zy")
    p_run.add_argument("input")

    p_check = sub.add_parser("check", help="only validate/transpile in memory")
    p_check.add_argument("input")

    p_c_module = sub.add_parser("c-module", help="generate ZyenLang wrappers for native C modules")
    c_module_sub = p_c_module.add_subparsers(dest="c_module_cmd", required=True)
    p_c_module_gen = c_module_sub.add_parser("gen", help="generate a .zy wrapper from a .zlcm.h template or JSON manifest")
    p_c_module_gen.add_argument("manifest")
    p_c_module_gen.add_argument("-o", "--output", default=None)

    sub.add_parser("version", help="print installed ZyenLang package version")

    # `zy doctor` — ZEP-0007. Defined in zyenlang.cli.doctor so the check
    # logic stays out of the transpiler hot path.
    from .cli import doctor as _doctor_cli
    _doctor_cli.add_subparser(sub)

    args = parser.parse_args(argv)
    if args.cmd == "version":
        print(f"zyenlang {_pkg_version}")
        return 0
    if args.cmd == "doctor":
        return _doctor_cli.handle(args)
    try:
        if args.cmd == "c-module":
            if args.c_module_cmd == "gen":
                out_path = native_c_module.write_module(Path(args.manifest), Path(args.output) if args.output else None)
                print(out_path)
                return 0
            return 1
        input_path = Path(args.input)
        if args.cmd == "build":
            if args.output:
                requested_output = Path(args.output)
            else:
                requested_output = input_path.with_suffix(".c")

            # Simple mode:
            #   -o main.c     -> generate C file
            #   -o main.exe   -> generate temporary C, then compile executable
            #   -o main       -> generate C file named main, unless --exe is also used
            if requested_output.suffix.lower() == ".exe":
                with tempfile.TemporaryDirectory() as tmp:
                    temp_c = Path(tmp) / (input_path.stem + ".c")
                    build_file(input_path, temp_c)
                    compile_c(temp_c, requested_output, collect_native_c_metadata(input_path))
                print(f"compiled: {requested_output}")
                return 0

            output_path = requested_output
            build_file(input_path, output_path)
            print(f"generated: {output_path}")
            if args.exe:
                compile_c(output_path, Path(args.exe), collect_native_c_metadata(input_path))
                print(f"compiled: {args.exe}")
            return 0
        if args.cmd == "run":
            return run_source(input_path)
        if args.cmd == "check":
            transpile(load_source_with_imports(input_path))
            print("OK")
            return 0
    except subprocess.CalledProcessError as e:
        print(f"C compiler failed: {e}", file=sys.stderr)
        return 2
    except ZyenError as e:
        print(f"Zyen error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Zyen error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"file not found: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
