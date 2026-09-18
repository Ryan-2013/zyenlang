from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .diagnostics import SourceSpan


Visibility = Literal["public", "private"]


@dataclass(frozen=True)
class TypeNode:
    span: SourceSpan


@dataclass(frozen=True)
class NamedTypeNode(TypeNode):
    name: str
    args: tuple[TypeNode, ...] = ()


@dataclass(frozen=True)
class TupleTypeNode(TypeNode):
    items: tuple[TypeNode, ...] = ()


@dataclass(frozen=True)
class OptionalTypeNode(TypeNode):
    inner: TypeNode | None = None


@dataclass(frozen=True)
class FunctionTypeNode(TypeNode):
    params: tuple[TypeNode, ...] = ()
    return_type: TypeNode | None = None


@dataclass(frozen=True)
class ReferenceTypeNode(TypeNode):
    inner: TypeNode | None = None
    mutable: bool = False


@dataclass(frozen=True)
class Expr:
    span: SourceSpan


@dataclass(frozen=True)
class IntExpr(Expr):
    value: int


@dataclass(frozen=True)
class FloatExpr(Expr):
    value: str


@dataclass(frozen=True)
class StringExpr(Expr):
    value: str


@dataclass(frozen=True)
class FStringExpr(Expr):
    parts: tuple[str | Expr, ...] = ()


@dataclass(frozen=True)
class BoolExpr(Expr):
    value: bool


@dataclass(frozen=True)
class NullExpr(Expr):
    pass


@dataclass(frozen=True)
class NameExpr(Expr):
    name: str


@dataclass(frozen=True)
class PathExpr(Expr):
    parts: tuple[str, ...]


@dataclass(frozen=True)
class TypeApplyExpr(Expr):
    callee: Expr
    type_args: tuple[TypeNode, ...]


@dataclass(frozen=True)
class AssociatedExpr(Expr):
    receiver: Expr
    name: str


@dataclass(frozen=True)
class UnaryExpr(Expr):
    operator: str
    operand: Expr


@dataclass(frozen=True)
class BinaryExpr(Expr):
    left: Expr
    operator: str
    right: Expr


@dataclass(frozen=True)
class FieldExpr(Expr):
    receiver: Expr
    name: str


@dataclass(frozen=True)
class IndexExpr(Expr):
    receiver: Expr
    index: Expr


@dataclass(frozen=True)
class CallExpr(Expr):
    callee: Expr
    args: tuple[Expr, ...] = ()


@dataclass(frozen=True)
class TupleExpr(Expr):
    items: tuple[Expr, ...] = ()


@dataclass(frozen=True)
class ListExpr(Expr):
    items: tuple[Expr, ...] = ()


@dataclass(frozen=True)
class StructFieldValue:
    name: str
    value: Expr
    span: SourceSpan


@dataclass(frozen=True)
class StructExpr(Expr):
    name: str
    fields: tuple[StructFieldValue, ...] = ()


@dataclass(frozen=True)
class CatchExpr(Expr):
    value: Expr
    error_name: str
    handler: "Block"


@dataclass(frozen=True)
class SpawnExpr(Expr):
    call: Expr


@dataclass(frozen=True)
class AwaitExpr(Expr):
    task: Expr


@dataclass(frozen=True)
class TypeOfExpr(Expr):
    value: Expr
    target_type: TypeNode


@dataclass(frozen=True)
class CastExpr(Expr):
    target_type: TypeNode
    value: Expr


@dataclass(frozen=True)
class BorrowExpr(Expr):
    value: Expr
    mutable: bool = False


@dataclass(frozen=True)
class ClosureExpr(Expr):
    params: tuple["Param", ...]
    return_type: TypeNode
    body: "Block"


@dataclass(frozen=True)
class Binding:
    name: str
    type_node: TypeNode | None
    span: SourceSpan


@dataclass(frozen=True)
class Stmt:
    span: SourceSpan


@dataclass(frozen=True)
class LetStmt(Stmt):
    bindings: tuple[Binding, ...]
    value: Expr


@dataclass(frozen=True)
class ReturnStmt(Stmt):
    values: tuple[Expr, ...] = ()


@dataclass(frozen=True)
class StopStmt(Stmt):
    value: Expr


@dataclass(frozen=True)
class RecoverStmt(Stmt):
    values: tuple[Expr, ...] = ()


@dataclass(frozen=True)
class IfStmt(Stmt):
    condition: Expr
    then_block: "Block"
    else_block: "Block | None" = None


@dataclass(frozen=True)
class IfLetStmt(Stmt):
    binding: str
    value: Expr
    then_block: "Block"
    else_block: "Block | None" = None


@dataclass(frozen=True)
class WhileStmt(Stmt):
    condition: Expr
    body: "Block"


@dataclass(frozen=True)
class AssignStmt(Stmt):
    target: Expr
    value: Expr


@dataclass(frozen=True)
class BreakStmt(Stmt):
    pass


@dataclass(frozen=True)
class ContinueStmt(Stmt):
    pass


@dataclass(frozen=True)
class DropStmt(Stmt):
    name: str


@dataclass(frozen=True)
class DeferStmt(Stmt):
    call: CallExpr


@dataclass(frozen=True)
class ExprStmt(Stmt):
    value: Expr


@dataclass(frozen=True)
class Block:
    statements: tuple[Stmt, ...]
    span: SourceSpan


@dataclass(frozen=True)
class FieldDef:
    name: str
    type_node: TypeNode
    visibility: Visibility
    span: SourceSpan
    default: Expr | None = None


@dataclass(frozen=True)
class StructDef:
    name: str
    fields: tuple[FieldDef, ...]
    visibility: Visibility
    type_params: tuple[str, ...]
    span: SourceSpan
    native_name: str | None = None


@dataclass(frozen=True)
class Param:
    name: str
    type_node: TypeNode
    span: SourceSpan
    mutable: bool = False
    default: Expr | None = None


@dataclass(frozen=True)
class ClassMethodDef:
    name: str
    params: tuple["Param", ...]
    return_type: TypeNode
    body: Block
    visibility: Visibility
    span: SourceSpan
    mutable: bool = False
    static: bool = False
    throws: TypeNode | None = None


@dataclass(frozen=True)
class ClassInitDef:
    params: tuple["Param", ...]
    body: Block
    visibility: Visibility
    span: SourceSpan
    throws: TypeNode | None = None


@dataclass(frozen=True)
class ClassDeinitDef:
    body: Block
    span: SourceSpan


@dataclass(frozen=True)
class ClassDef:
    name: str
    fields: tuple[FieldDef, ...]
    methods: tuple[ClassMethodDef, ...]
    initializer: ClassInitDef | None
    deinitializer: ClassDeinitDef | None
    visibility: Visibility
    type_params: tuple[str, ...]
    span: SourceSpan


@dataclass(frozen=True)
class FunctionDef:
    name: str
    params: tuple[Param, ...]
    return_type: TypeNode
    body: Block
    visibility: Visibility
    span: SourceSpan
    receiver: Param | None = None
    type_params: tuple[str, ...] = ()
    throws: TypeNode | None = None
    exported: bool = False


@dataclass(frozen=True)
class NativeSourceDef:
    path: str
    span: SourceSpan


@dataclass(frozen=True)
class NativeLinkDef:
    platform: str
    library: str
    span: SourceSpan


@dataclass(frozen=True)
class NativeBuildDef:
    """Compiler-generated native build metadata.

    Source programs cannot spell this declaration directly.  c_module uses it
    after validating a .zlcm.h template so no command line text is reparsed by
    the backend or shell.
    """

    headers: tuple[str, ...]
    sources: tuple[str, ...]
    include_dirs: tuple[str, ...]
    lib_dirs: tuple[str, ...]
    libraries: tuple[str, ...]
    cflags: tuple[str, ...]
    ldflags: tuple[str, ...]
    span: SourceSpan


@dataclass(frozen=True)
class NativeFunctionDef:
    name: str
    params: tuple[Param, ...]
    return_type: TypeNode
    symbol: str
    visibility: Visibility
    span: SourceSpan
    throws: TypeNode | None = None


Definition = (
    ClassDef
    | StructDef
    | FunctionDef
    | NativeSourceDef
    | NativeLinkDef
    | NativeBuildDef
    | NativeFunctionDef
)


@dataclass(frozen=True)
class ImportDef:
    path: str
    alias: str
    is_angle: bool
    span: SourceSpan


@dataclass(frozen=True)
class Program:
    imports: tuple[ImportDef, ...] = field(default_factory=tuple)
    definitions: tuple[Definition, ...] = field(default_factory=tuple)
