from __future__ import annotations

from dataclasses import dataclass

from . import ast
from .diagnostics import CompileError


@dataclass(frozen=True)
class Type:
    def display(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class PrimitiveType(Type):
    name: str

    def display(self) -> str:
        return self.name


@dataclass(frozen=True)
class NamedType(Type):
    name: str
    args: tuple[Type, ...] = ()

    def display(self) -> str:
        if not self.args:
            return self.name
        return f"{self.name}<{', '.join(item.display() for item in self.args)}>"


@dataclass(frozen=True)
class TupleType(Type):
    items: tuple[Type, ...]

    def display(self) -> str:
        return f"({', '.join(item.display() for item in self.items)})"


@dataclass(frozen=True)
class OptionalType(Type):
    inner: Type

    def display(self) -> str:
        return f"{self.inner.display()} | null"


@dataclass(frozen=True)
class TypeVar(Type):
    name: str

    def display(self) -> str:
        return self.name


VOID = PrimitiveType("void")
BOOL = PrimitiveType("bool")
STR = PrimitiveType("str")
ERROR = PrimitiveType("Error")
NULL = PrimitiveType("null")

INTEGER_TYPES = {
    "i8": (-128, 127),
    "i16": (-32768, 32767),
    "i32": (-2147483648, 2147483647),
    "i64": (-9223372036854775808, 9223372036854775807),
    "u8": (0, 255),
    "u16": (0, 65535),
    "u32": (0, 4294967295),
    "u64": (0, 18446744073709551615),
    "isize": (-9223372036854775808, 9223372036854775807),
    "usize": (0, 18446744073709551615),
}

FLOAT_TYPES = {"f32", "f64"}
BUILTIN_NAMES = set(INTEGER_TYPES) | FLOAT_TYPES | {"bool", "str", "void", "Error"}


def is_integer(typ: Type) -> bool:
    return isinstance(typ, PrimitiveType) and typ.name in INTEGER_TYPES


def is_float(typ: Type) -> bool:
    return isinstance(typ, PrimitiveType) and typ.name in FLOAT_TYPES


def is_numeric(typ: Type) -> bool:
    return is_integer(typ) or is_float(typ)


def is_list(typ: Type) -> bool:
    return isinstance(typ, NamedType) and typ.name == "List" and len(typ.args) == 1


def is_task(typ: Type) -> bool:
    return isinstance(typ, NamedType) and typ.name == "Task" and len(typ.args) == 1


def resolve_type_node(
    node: ast.TypeNode,
    known_structs: set[str],
    type_vars: set[str] | None = None,
    source_name: str = "<source>",
) -> Type:
    type_vars = type_vars or set()
    if isinstance(node, ast.OptionalTypeNode):
        if node.inner is None:
            raise CompileError("optional type is missing its value type", node.span, source_name)
        inner = resolve_type_node(node.inner, known_structs, type_vars, source_name)
        if inner in {VOID, NULL} or isinstance(inner, OptionalType):
            raise CompileError(f"invalid optional type `{inner.display()} | null`", node.span, source_name)
        return OptionalType(inner)
    if isinstance(node, ast.TupleTypeNode):
        return TupleType(tuple(resolve_type_node(item, known_structs, type_vars, source_name) for item in node.items))
    if not isinstance(node, ast.NamedTypeNode):
        raise CompileError("unsupported type syntax", node.span, source_name)
    if node.name in type_vars:
        if node.args:
            raise CompileError("generic type parameters cannot take type arguments", node.span, source_name)
        return TypeVar(node.name)
    if node.name in BUILTIN_NAMES:
        if node.args:
            raise CompileError(f"builtin type `{node.name}` is not generic", node.span, source_name)
        return PrimitiveType(node.name)
    args = tuple(resolve_type_node(item, known_structs, type_vars, source_name) for item in node.args)
    if node.name == "List":
        if len(args) != 1:
            raise CompileError("List requires exactly one element type", node.span, source_name)
        return NamedType("List", args)
    if node.name in {"Box", "Ref", "Raw", "Task", "Channel"} or node.name.startswith("ptr."):
        if len(args) != 1:
            raise CompileError(f"{node.name} requires exactly one type argument", node.span, source_name)
        return NamedType(node.name, args)
    if node.name in known_structs:
        return NamedType(node.name, args)
    raise CompileError(f"unknown type `{node.name}`", node.span, source_name)


def assignable(expected: Type, actual: Type) -> bool:
    if expected == actual:
        return True
    if isinstance(expected, OptionalType):
        return actual == NULL or assignable(expected.inner, actual)
    return False


def integer_literal_fits(value: int, typ: Type) -> bool:
    if not is_integer(typ):
        return False
    low, high = INTEGER_TYPES[typ.name]
    return low <= value <= high


def substitute(typ: Type, mapping: dict[str, Type]) -> Type:
    if isinstance(typ, TypeVar):
        return mapping.get(typ.name, typ)
    if isinstance(typ, NamedType):
        return NamedType(typ.name, tuple(substitute(arg, mapping) for arg in typ.args))
    if isinstance(typ, TupleType):
        return TupleType(tuple(substitute(item, mapping) for item in typ.items))
    if isinstance(typ, OptionalType):
        return OptionalType(substitute(typ.inner, mapping))
    return typ
