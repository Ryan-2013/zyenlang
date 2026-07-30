from __future__ import annotations

from dataclasses import dataclass, field

from .diagnostics import SourceSpan
from .types import Type


@dataclass(frozen=True)
class IRExpr:
    typ: Type
    span: SourceSpan


@dataclass(frozen=True)
class IRInt(IRExpr):
    value: int


@dataclass(frozen=True)
class IRString(IRExpr):
    value: str


@dataclass(frozen=True)
class IRBool(IRExpr):
    value: bool


@dataclass(frozen=True)
class IRNull(IRExpr):
    pass


@dataclass(frozen=True)
class IROptionalSome(IRExpr):
    value: IRExpr


@dataclass(frozen=True)
class IRName(IRExpr):
    name: str


@dataclass(frozen=True)
class IRUnary(IRExpr):
    operator: str
    operand: IRExpr


@dataclass(frozen=True)
class IRBinary(IRExpr):
    left: IRExpr
    operator: str
    right: IRExpr


@dataclass(frozen=True)
class IRField(IRExpr):
    receiver: IRExpr
    name: str


@dataclass(frozen=True)
class IRCall(IRExpr):
    target: str
    args: tuple[IRExpr, ...]
    throws: Type | None = None


@dataclass(frozen=True)
class IRTuple(IRExpr):
    items: tuple[IRExpr, ...]


@dataclass(frozen=True)
class IRList(IRExpr):
    items: tuple[IRExpr, ...]


@dataclass(frozen=True)
class IRStruct(IRExpr):
    name: str
    fields: tuple[tuple[str, IRExpr], ...]


@dataclass(frozen=True)
class IRCatch(IRExpr):
    value: IRExpr
    error_name: str
    handler: "IRBlock"


@dataclass(frozen=True)
class IRSpawn(IRExpr):
    call: IRCall
    spawn_id: int


@dataclass(frozen=True)
class IRAwait(IRExpr):
    task: IRExpr


@dataclass(frozen=True)
class IRStmt:
    span: SourceSpan


@dataclass(frozen=True)
class IRLet(IRStmt):
    names: tuple[str, ...]
    types: tuple[Type, ...]
    value: IRExpr


@dataclass(frozen=True)
class IRReturn(IRStmt):
    value: IRExpr | None


@dataclass(frozen=True)
class IRStop(IRStmt):
    error: IRExpr


@dataclass(frozen=True)
class IRRecover(IRStmt):
    value: IRExpr | None


@dataclass(frozen=True)
class IRIf(IRStmt):
    condition: IRExpr
    then_block: "IRBlock"
    else_block: "IRBlock | None"


@dataclass(frozen=True)
class IRIfLet(IRStmt):
    name: str
    inner_type: Type
    value: IRExpr
    then_block: "IRBlock"
    else_block: "IRBlock | None"


@dataclass(frozen=True)
class IRWhile(IRStmt):
    condition: IRExpr
    body: "IRBlock"


@dataclass(frozen=True)
class IRAssign(IRStmt):
    target: IRExpr
    value: IRExpr


@dataclass(frozen=True)
class IRBreak(IRStmt):
    pass


@dataclass(frozen=True)
class IRContinue(IRStmt):
    pass


@dataclass(frozen=True)
class IRExprStmt(IRStmt):
    value: IRExpr


@dataclass(frozen=True)
class IRBlock:
    statements: tuple[IRStmt, ...]
    span: SourceSpan


@dataclass(frozen=True)
class IRFieldDef:
    name: str
    typ: Type
    visibility: str
    default: IRExpr | None = None


@dataclass(frozen=True)
class IRStructDef:
    name: str
    fields: tuple[IRFieldDef, ...]
    visibility: str


@dataclass(frozen=True)
class IRParam:
    name: str
    typ: Type
    mutable: bool = False


@dataclass(frozen=True)
class IRFunction:
    name: str
    c_name: str
    params: tuple[IRParam, ...]
    return_type: Type
    body: IRBlock
    visibility: str
    receiver_type: Type | None = None
    throws: Type | None = None


@dataclass(frozen=True)
class IRExternFunction:
    name: str
    c_name: str
    params: tuple[IRParam, ...]
    return_type: Type
    visibility: str
    throws: Type | None = None


@dataclass(frozen=True)
class IRNativeLink:
    platform: str
    library: str


@dataclass(frozen=True)
class IRProgram:
    structs: tuple[IRStructDef, ...] = field(default_factory=tuple)
    functions: tuple[IRFunction, ...] = field(default_factory=tuple)
    extern_functions: tuple[IRExternFunction, ...] = field(default_factory=tuple)
    native_sources: tuple[str, ...] = field(default_factory=tuple)
    native_links: tuple[IRNativeLink, ...] = field(default_factory=tuple)
    features: frozenset[str] = field(default_factory=frozenset)
