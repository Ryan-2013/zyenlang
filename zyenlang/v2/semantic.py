from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from . import ast, ir
from .diagnostics import CompileError, SourceSpan
from .types import (
    BOOL,
    ERROR,
    NULL,
    STR,
    VOID,
    FunctionType,
    NamedType,
    OptionalType,
    PrimitiveType,
    TupleType,
    Type,
    TypeVar,
    assignable,
    common_numeric_type,
    integer_literal_fits,
    is_box,
    is_integer,
    is_list,
    is_numeric,
    is_task,
    resolve_type_node,
    substitute,
)


PROCESS_SPECIAL_VALUES = {"GET_ARGS__", "GET_EXE__"}
COMPILER_SPECIAL_VALUES = PROCESS_SPECIAL_VALUES | {"FILE__"}
STRUCT_METADATA_FIELDS = {
    "__attributes__": "attributes",
    "__methods__": "methods",
}


@dataclass(frozen=True)
class FieldSymbol:
    name: str
    typ: Type
    visibility: str
    node: ast.FieldDef


@dataclass
class StructSymbol:
    name: str
    visibility: str
    type_params: tuple[str, ...]
    node: ast.StructDef
    fields: dict[str, FieldSymbol]


@dataclass
class FunctionSymbol:
    name: str
    c_name: str
    visibility: str
    params: tuple[tuple[str, Type, bool], ...]
    return_type: Type
    throws: Type | None
    receiver: tuple[str, Type, bool] | None
    type_params: tuple[str, ...]
    node: ast.FunctionDef | ast.NativeFunctionDef
    type_mapping: dict[str, Type]
    native_symbol: str | None = None


class Lowerer:
    def __init__(self, program: ast.Program, source_name: str = "<source>", *, require_main: bool = True) -> None:
        self.program = program
        self.source_name = source_name
        self.structs: dict[str, StructSymbol] = {}
        self.functions: dict[str, FunctionSymbol] = {}
        self.methods: dict[tuple[str, str], FunctionSymbol] = {}
        self.scopes: list[dict[str, Type]] = []
        self.task_scopes: list[dict[str, tuple[bool, SourceSpan]]] = []
        self.current_function: FunctionSymbol | None = None
        self.current_receiver: str | None = None
        self.catch_result_types: list[Type] = []
        self.require_main = require_main
        self.generic_instances: dict[tuple[str, tuple[Type, ...]], FunctionSymbol] = {}
        self.pending_functions: list[FunctionSymbol] = []
        self.spawn_counter = 0
        self.features: set[str] = set()
        self.loop_depth = 0

    def error(self, message: str, span: SourceSpan) -> CompileError:
        return CompileError(message, span, self.source_name)

    def lower(self) -> ir.IRProgram:
        self.collect_struct_names()
        self.collect_struct_fields()
        self.validate_struct_layouts()
        self.validate_managed_struct_cycles()
        self.collect_functions()

        lowered_structs = tuple(self.lower_struct(symbol) for symbol in self.structs.values() if not symbol.type_params)
        all_functions = list(self.functions.values()) + list(self.methods.values())
        self.validate_native_defaults(all_functions)
        if self.require_main:
            self.validate_generic_templates(all_functions)
        self.pending_functions = [
            symbol
            for symbol in all_functions
            if symbol.native_symbol is None and (not symbol.type_params or not self.require_main)
        ]
        lowered_functions: list[ir.IRFunction] = []
        index = 0
        while index < len(self.pending_functions):
            lowered_functions.append(self.lower_function(self.pending_functions[index]))
            index += 1

        if self.require_main:
            if "main" not in self.functions:
                raise CompileError("program must define `fn main()`", source_name=self.source_name)
            main = self.functions["main"]
            if main.params or main.receiver is not None:
                raise self.error("main cannot have parameters or a receiver", main.node.span)
            if main.return_type != PrimitiveType("i32"):
                raise self.error("main must return i32", main.node.return_type.span)
        extern_functions = tuple(
            ir.IRExternFunction(
                symbol.name,
                symbol.native_symbol or symbol.c_name,
                tuple(ir.IRParam(name, typ, mutable) for name, typ, mutable in symbol.params),
                symbol.return_type,
                symbol.visibility,
                symbol.throws,
            )
            for symbol in all_functions
            if symbol.native_symbol is not None
        )
        native_sources, native_links = self.collect_native_metadata()
        if "threads" in self.features:
            native_links = native_links + (
                ir.IRNativeLink("linux", "pthread"),
                ir.IRNativeLink("macos", "pthread"),
            )
        return ir.IRProgram(
            structs=lowered_structs,
            functions=tuple(lowered_functions),
            extern_functions=extern_functions,
            native_sources=native_sources,
            native_links=native_links,
            features=frozenset(self.features),
        )

    def validate_native_defaults(self, functions: list[FunctionSymbol]) -> None:
        saved_function = self.current_function
        saved_receiver = self.current_receiver
        saved_pending = self.pending_functions
        saved_instances = self.generic_instances
        saved_features = set(self.features)
        saved_spawn_counter = self.spawn_counter
        self.pending_functions = []
        self.generic_instances = {}
        try:
            for symbol in functions:
                if symbol.native_symbol is None:
                    continue
                self.current_function = symbol
                self.current_receiver = None
                self.push_scope()
                try:
                    for param_node, (_, typ, _) in zip(symbol.node.params, symbol.params):
                        if param_node.default is not None:
                            self.lower_expr(param_node.default, typ)
                finally:
                    self.pop_scope()
        finally:
            self.current_function = saved_function
            self.current_receiver = saved_receiver
            self.pending_functions = saved_pending
            self.generic_instances = saved_instances
            self.features = saved_features
            self.spawn_counter = saved_spawn_counter

    def validate_generic_templates(self, functions: list[FunctionSymbol]) -> None:
        saved_pending = self.pending_functions
        saved_instances = self.generic_instances
        saved_features = set(self.features)
        saved_spawn_counter = self.spawn_counter
        self.pending_functions = []
        self.generic_instances = {}
        try:
            for symbol in functions:
                if symbol.type_params and symbol.native_symbol is None:
                    self.lower_function(symbol)
        finally:
            self.pending_functions = saved_pending
            self.generic_instances = saved_instances
            self.features = saved_features
            self.spawn_counter = saved_spawn_counter

    def collect_struct_names(self) -> None:
        for definition in self.program.definitions:
            if not isinstance(definition, ast.StructDef):
                continue
            if definition.name in self.structs:
                raise self.error(f"duplicate struct `{definition.name}`", definition.span)
            self.structs[definition.name] = StructSymbol(
                definition.name,
                definition.visibility,
                definition.type_params,
                definition,
                {},
            )

    def collect_struct_fields(self) -> None:
        known = set(self.structs)
        for symbol in self.structs.values():
            type_vars = set(symbol.type_params)
            for field in symbol.node.fields:
                if field.name in STRUCT_METADATA_FIELDS:
                    raise self.error(f"`{field.name}` is reserved for struct metadata", field.span)
                if field.name in symbol.fields:
                    raise self.error(f"duplicate field `{field.name}` in `{symbol.name}`", field.span)
                typ = resolve_type_node(field.type_node, known, type_vars, self.source_name)
                self.validate_function_types(typ, field.span)
                if self.contains_box(typ):
                    raise self.error(
                        "Box<T> fields require managed aggregate destructors and are not enabled yet",
                        field.span,
                    )
                if is_task(typ):
                    raise self.error("Task<T> is linear and cannot be stored in a struct field", field.span)
                symbol.fields[field.name] = FieldSymbol(field.name, typ, field.visibility, field)
        for symbol in self.structs.values():
            for field in symbol.fields.values():
                self.validate_box_position(field.typ, field.node.span, "struct field")

    def validate_struct_layouts(self) -> None:
        graph: dict[str, list[tuple[str, ast.FieldDef]]] = {name: [] for name in self.structs}
        for symbol in self.structs.values():
            for field in symbol.fields.values():
                for dependency in self.by_value_struct_dependencies(field.typ):
                    graph[symbol.name].append((dependency, field.node))

        visiting: list[str] = []
        complete: set[str] = set()

        def visit(name: str) -> None:
            if name in complete:
                return
            visiting.append(name)
            for dependency, field in graph[name]:
                if dependency in visiting:
                    start = visiting.index(dependency)
                    cycle = visiting[start:] + [dependency]
                    raise self.error(
                        "recursive by-value struct layout: "
                        + " -> ".join(cycle)
                        + "; use List<T> now, or Box<T> after std/ptr is implemented",
                        field.span,
                    )
                visit(dependency)
            visiting.pop()
            complete.add(name)

        for name in graph:
            visit(name)

    def validate_managed_struct_cycles(self) -> None:
        graph: dict[str, list[tuple[str, ast.FieldDef]]] = {name: [] for name in self.structs}
        for symbol in self.structs.values():
            for field in symbol.fields.values():
                for dependency in self.struct_dependencies_anywhere(field.typ):
                    graph[symbol.name].append((dependency, field.node))

        visiting: list[str] = []
        complete: set[str] = set()

        def visit(name: str) -> None:
            if name in complete:
                return
            visiting.append(name)
            for dependency, field in graph[name]:
                if dependency in visiting:
                    start = visiting.index(dependency)
                    cycle = visiting[start:] + [dependency]
                    raise self.error(
                        "recursive managed struct ownership: "
                        + " -> ".join(cycle)
                        + "; self-referential List fields are not enabled yet",
                        field.span,
                    )
                visit(dependency)
            visiting.pop()
            complete.add(name)

        for name in graph:
            visit(name)

    def struct_dependencies_anywhere(self, typ: Type) -> tuple[str, ...]:
        if isinstance(typ, FunctionType):
            return ()
        if isinstance(typ, NamedType):
            if typ.name in self.structs:
                return (typ.name,)
            found: list[str] = []
            for arg in typ.args:
                found.extend(self.struct_dependencies_anywhere(arg))
            return tuple(found)
        if isinstance(typ, TupleType):
            found = []
            for item in typ.items:
                found.extend(self.struct_dependencies_anywhere(item))
            return tuple(found)
        if isinstance(typ, OptionalType):
            return self.struct_dependencies_anywhere(typ.inner)
        return ()

    def by_value_struct_dependencies(self, typ: Type) -> tuple[str, ...]:
        if isinstance(typ, FunctionType):
            return ()
        if isinstance(typ, NamedType):
            if typ.name in self.structs:
                return (typ.name,)
            if is_list(typ) or typ.name in {"Box", "Ref", "Raw"} or typ.name.startswith("ptr."):
                return ()
            found: list[str] = []
            for arg in typ.args:
                found.extend(self.by_value_struct_dependencies(arg))
            return tuple(found)
        if isinstance(typ, TupleType):
            found = []
            for item in typ.items:
                found.extend(self.by_value_struct_dependencies(item))
            return tuple(found)
        if isinstance(typ, OptionalType):
            return self.by_value_struct_dependencies(typ.inner)
        return ()

    def collect_functions(self) -> None:
        known = set(self.structs)
        native_signatures: dict[str, tuple[tuple[Type, ...], Type]] = {}
        for definition in self.program.definitions:
            if not isinstance(definition, (ast.FunctionDef, ast.NativeFunctionDef)):
                continue
            type_params = definition.type_params if isinstance(definition, ast.FunctionDef) else ()
            type_vars = set(type_params)
            params: list[tuple[str, Type, bool]] = []
            seen: set[str] = set()
            for param in definition.params:
                if param.name in seen:
                    raise self.error(f"duplicate parameter `{param.name}`", param.span)
                seen.add(param.name)
                params.append(
                    (
                        param.name,
                        resolve_type_node(param.type_node, known, type_vars, self.source_name),
                        param.mutable,
                    )
                )
                self.validate_box_position(params[-1][1], param.span, "parameter")
                if is_task(params[-1][1]):
                    raise self.error("Task<T> cannot be passed as a function parameter", param.span)
            return_type = resolve_type_node(definition.return_type, known, type_vars, self.source_name)
            self.validate_box_position(return_type, definition.return_type.span, "return type")
            if is_task(return_type):
                raise self.error("Task<T> cannot be returned; await it in the creating scope", definition.return_type.span)
            throws = None
            if definition.throws is not None:
                throws = resolve_type_node(definition.throws, known, type_vars, self.source_name)
                if throws != ERROR:
                    raise self.error("the first v2 error effect must be written `throws Error`", definition.throws.span)

            receiver = None
            receiver_name = None
            if isinstance(definition, ast.FunctionDef) and definition.receiver is not None:
                receiver_type = resolve_type_node(definition.receiver.type_node, known, type_vars, self.source_name)
                if not isinstance(receiver_type, NamedType) or receiver_type.name not in self.structs:
                    raise self.error("method receiver must be a struct type", definition.receiver.span)
                if receiver_type.args:
                    raise self.error("generic receiver methods are not executable in the bootstrap milestone", definition.receiver.span)
                receiver = (definition.receiver.name, receiver_type, definition.receiver.mutable)
                receiver_name = receiver_type.name
                if definition.receiver.name in seen:
                    raise self.error("receiver name conflicts with a parameter", definition.receiver.span)

            native_symbol = definition.symbol if isinstance(definition, ast.NativeFunctionDef) else None
            if native_symbol is not None and len(native_symbol) > 128:
                raise self.error("native C symbols may not exceed 128 characters", definition.span)
            if native_symbol is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", native_symbol):
                raise self.error(f"invalid native C symbol `{native_symbol}`", definition.span)
            if native_symbol is not None and definition.throws is not None:
                raise self.error("native functions cannot declare throws until the Result ABI is stabilized", definition.span)
            if native_symbol is not None:
                native_types = tuple(typ for _, typ, _ in params)
                if any(not isinstance(typ, PrimitiveType) or typ in {VOID, ERROR} for typ in native_types):
                    raise self.error("native parameters currently support only scalar and str ABI types", definition.span)
                if not isinstance(return_type, PrimitiveType) or return_type == ERROR:
                    raise self.error("native return values currently support only scalar, str, or void ABI types", definition.span)
                signature = (native_types, return_type)
                previous = native_signatures.get(native_symbol)
                if previous is not None and previous != signature:
                    raise self.error(f"native C symbol `{native_symbol}` was declared with conflicting signatures", definition.span)
                native_signatures[native_symbol] = signature
                if definition.name == "main":
                    raise self.error("main must be a ZyenLang function, not a native declaration", definition.span)
            safe_name = re.sub(r"[^A-Za-z0-9_]", "__", definition.name)
            safe_receiver = re.sub(r"[^A-Za-z0-9_]", "__", receiver_name or "")
            c_name = native_symbol or (
                f"zy2_method_{safe_receiver}_{safe_name}" if receiver_name else f"zy2_fn_{safe_name}"
            )
            symbol = FunctionSymbol(
                definition.name,
                c_name,
                definition.visibility,
                tuple(params),
                return_type,
                throws,
                receiver,
                type_params,
                definition,
                {},
                native_symbol,
            )
            if receiver_name:
                if definition.name in STRUCT_METADATA_FIELDS:
                    raise self.error(f"`{definition.name}` is reserved for struct metadata", definition.span)
                key = (receiver_name, definition.name)
                if key in self.methods:
                    raise self.error(f"duplicate method `{receiver_name}.{definition.name}`", definition.span)
                self.methods[key] = symbol
            else:
                if definition.name in self.functions:
                    raise self.error(f"duplicate function `{definition.name}`", definition.span)
                self.functions[definition.name] = symbol

    def collect_native_metadata(self) -> tuple[tuple[str, ...], tuple[ir.IRNativeLink, ...]]:
        sources: list[str] = []
        links: list[ir.IRNativeLink] = []
        seen_sources: set[str] = set()
        seen_links: set[tuple[str, str]] = set()
        for definition in self.program.definitions:
            if isinstance(definition, ast.NativeSourceDef):
                raw = Path(definition.path)
                if raw.is_absolute():
                    raise self.error("native source paths must be relative to their .zy module", definition.span)
                owner = Path(definition.span.source_name)
                if definition.span.source_name.startswith("<"):
                    raise self.error("native sources require file-based compilation", definition.span)
                resolved = (owner.parent / raw).resolve()
                if resolved.suffix.lower() != ".c":
                    raise self.error("native source must be a .c file", definition.span)
                if not resolved.is_file():
                    raise self.error(f"native source not found: {resolved}", definition.span)
                value = str(resolved)
                if value not in seen_sources:
                    seen_sources.add(value)
                    sources.append(value)
            elif isinstance(definition, ast.NativeLinkDef):
                if definition.platform not in {"all", "windows", "linux", "macos"}:
                    raise self.error(f"unsupported native link platform `{definition.platform}`", definition.span)
                if len(definition.library) > 128:
                    raise self.error("native library names may not exceed 128 characters", definition.span)
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.+-]*", definition.library):
                    raise self.error("native library names may not contain compiler flags", definition.span)
                key = (definition.platform, definition.library)
                if key not in seen_links:
                    seen_links.add(key)
                    links.append(ir.IRNativeLink(*key))
        return tuple(sources), tuple(links)

    def lower_struct(self, symbol: StructSymbol) -> ir.IRStructDef:
        fields: list[ir.IRFieldDef] = []
        for field in symbol.fields.values():
            default = None
            if field.node.default is not None:
                self.push_scope()
                try:
                    default = self.lower_expr(field.node.default, field.typ)
                finally:
                    self.pop_scope()
            fields.append(ir.IRFieldDef(field.name, field.typ, field.visibility, default))
        return ir.IRStructDef(symbol.name, tuple(fields), symbol.visibility)

    def lower_function(self, symbol: FunctionSymbol) -> ir.IRFunction:
        self.current_function = symbol
        self.current_receiver = symbol.receiver[1].name if symbol.receiver and isinstance(symbol.receiver[1], NamedType) else None
        self.push_scope()
        params: list[ir.IRParam] = []
        try:
            for name, typ, _ in symbol.params:
                param_node = next(item for item in symbol.node.params if item.name == name)
                if param_node.default is not None:
                    self.lower_expr(param_node.default, typ)
            if symbol.receiver is not None:
                name, typ, mutable = symbol.receiver
                self.define_local(name, typ, symbol.node.receiver.span if symbol.node.receiver else symbol.node.span)
                params.append(ir.IRParam(name, typ, mutable))
            for name, typ, mutable in symbol.params:
                param_node = next(item for item in symbol.node.params if item.name == name)
                self.define_local(name, typ, param_node.span)
                params.append(ir.IRParam(name, typ, mutable))
            body = self.lower_block(symbol.node.body, push_scope=False)
            self.validate_current_task_scope()
            if symbol.return_type != VOID and not self.block_terminates(body):
                raise self.error(
                    f"function `{symbol.name}` must return `{symbol.return_type.display()}` on every path",
                    symbol.node.body.span,
                )
        finally:
            self.pop_scope()
            self.current_function = None
            self.current_receiver = None
        return ir.IRFunction(
            symbol.name,
            symbol.c_name,
            tuple(params),
            symbol.return_type,
            body,
            symbol.visibility,
            symbol.receiver[1] if symbol.receiver else None,
            symbol.throws,
        )

    def push_scope(self) -> None:
        self.scopes.append({})
        self.task_scopes.append({})

    def pop_scope(self) -> None:
        self.scopes.pop()
        self.task_scopes.pop()

    def validate_current_task_scope(self) -> None:
        for name, (consumed, span) in self.task_scopes[-1].items():
            if not consumed:
                raise self.error(f"task `{name}` must be awaited exactly once before leaving this scope", span)

    def consume_task(self, name: str, span: SourceSpan) -> Type:
        if name not in self.task_scopes[-1]:
            for scope in self.task_scopes[:-1]:
                if name in scope:
                    raise self.error("await must occur in the same lexical scope that created the task", span)
            typ = self.lookup_local(name, span)
            raise self.error(f"`{name}` has type `{typ.display()}`, not Task<T>", span)
        consumed, created_at = self.task_scopes[-1][name]
        if consumed:
            raise self.error(f"task `{name}` has already been awaited", span)
        typ = self.scopes[-1][name]
        self.task_scopes[-1][name] = (True, created_at)
        return typ

    def define_local(self, name: str, typ: Type, span: SourceSpan) -> None:
        if name in PROCESS_SPECIAL_VALUES:
            raise self.error(f"`{name}` is a reserved process value and cannot be shadowed", span)
        if name in COMPILER_SPECIAL_VALUES:
            raise self.error(f"`{name}` is a reserved compiler value and cannot be shadowed", span)
        scope = self.scopes[-1]
        if name in scope:
            raise self.error(f"duplicate local `{name}`", span)
        scope[name] = typ

    def lookup_local(self, name: str, span: SourceSpan) -> Type:
        typ = self.find_local(name)
        if typ is not None:
            return typ
        raise self.error(f"unknown name `{name}`", span)

    def find_local(self, name: str) -> Type | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def lower_block(self, block: ast.Block, *, push_scope: bool = True) -> ir.IRBlock:
        if push_scope:
            self.push_scope()
        try:
            statements = tuple(self.lower_stmt(statement) for statement in block.statements)
            if push_scope:
                self.validate_current_task_scope()
            return ir.IRBlock(statements, block.span)
        finally:
            if push_scope:
                self.pop_scope()

    def lower_stmt(self, statement: ast.Stmt) -> ir.IRStmt:
        if isinstance(statement, ast.LetStmt):
            return self.lower_let(statement)
        if isinstance(statement, ast.ReturnStmt):
            assert self.current_function is not None
            value = self.lower_value_sequence(statement.values, self.current_function.return_type, statement.span)
            return ir.IRReturn(statement.span, value)
        if isinstance(statement, ast.StopStmt):
            if self.current_function is None or self.current_function.throws is None:
                raise self.error("`stop` is only valid in a function declared `throws Error`", statement.span)
            error_value = self.lower_expr(statement.value)
            if error_value.typ == STR:
                source_name = statement.span.source_name or self.source_name
                error_value = ir.IRStruct(
                    ERROR,
                    error_value.span,
                    "Error",
                    (
                        ("message", error_value),
                        ("source_file", ir.IRString(STR, statement.span, source_name)),
                        ("line", ir.IRInt(PrimitiveType("u32"), statement.span, statement.span.line)),
                        ("column", ir.IRInt(PrimitiveType("u32"), statement.span, statement.span.column)),
                    ),
                )
            elif error_value.typ != ERROR:
                raise self.error("stop expects a str message or Error value", statement.value.span)
            return ir.IRStop(statement.span, error_value)
        if isinstance(statement, ast.RecoverStmt):
            if not self.catch_result_types:
                raise self.error("`recover` is only valid inside a catch block", statement.span)
            expected = self.catch_result_types[-1]
            value = self.lower_value_sequence(statement.values, expected, statement.span)
            return ir.IRRecover(statement.span, value)
        if isinstance(statement, ast.IfStmt):
            condition = self.lower_expr(statement.condition, BOOL)
            then_block = self.lower_block(statement.then_block)
            else_block = self.lower_block(statement.else_block) if statement.else_block else None
            return ir.IRIf(statement.span, condition, then_block, else_block)
        if isinstance(statement, ast.IfLetStmt):
            value = self.lower_expr(statement.value)
            if not isinstance(value.typ, OptionalType):
                raise self.error("if let requires a `T | null` value", statement.value.span)
            self.push_scope()
            try:
                self.define_local(statement.binding, value.typ.inner, statement.span)
                then_block = self.lower_block(statement.then_block, push_scope=False)
            finally:
                self.pop_scope()
            else_block = self.lower_block(statement.else_block) if statement.else_block else None
            return ir.IRIfLet(statement.span, statement.binding, value.typ.inner, value, then_block, else_block)
        if isinstance(statement, ast.WhileStmt):
            condition = self.lower_expr(statement.condition, BOOL)
            self.loop_depth += 1
            try:
                body = self.lower_block(statement.body)
            finally:
                self.loop_depth -= 1
            return ir.IRWhile(statement.span, condition, body)
        if isinstance(statement, ast.AssignStmt):
            return self.lower_assignment(statement)
        if isinstance(statement, ast.BreakStmt):
            if self.loop_depth == 0:
                raise self.error("`break` is only valid inside a loop", statement.span)
            return ir.IRBreak(statement.span)
        if isinstance(statement, ast.ContinueStmt):
            if self.loop_depth == 0:
                raise self.error("`continue` is only valid inside a loop", statement.span)
            return ir.IRContinue(statement.span)
        if isinstance(statement, ast.ExprStmt):
            return ir.IRExprStmt(statement.span, self.lower_expr(statement.value))
        raise self.error("unsupported statement", statement.span)

    def lower_assignment(self, statement: ast.AssignStmt) -> ir.IRAssign:
        if isinstance(statement.target, ast.NameExpr):
            if statement.target.name in PROCESS_SPECIAL_VALUES:
                raise self.error("process special values cannot be assigned", statement.target.span)
            target_type = self.lookup_local(statement.target.name, statement.target.span)
            if is_task(target_type):
                raise self.error("Task<T> variables cannot be reassigned", statement.target.span)
            target: ir.IRExpr = ir.IRName(target_type, statement.target.span, statement.target.name)
        else:
            root = statement.target
            while isinstance(root, ast.FieldExpr):
                root = root.receiver
            if not isinstance(root, ast.NameExpr):
                raise self.error("field assignment must be rooted in a local variable", statement.target.span)
            target = self.lower_field(statement.target)
            if is_task(target.typ):
                raise self.error("Task<T> fields are not assignable", statement.target.span)
        value = self.lower_expr(statement.value, target.typ)
        return ir.IRAssign(statement.span, target, value)

    def lower_let(self, statement: ast.LetStmt) -> ir.IRLet:
        if len(statement.bindings) == 1:
            binding = statement.bindings[0]
            expected = self.resolve_optional_annotation(binding.type_node)
            value = self.lower_expr(statement.value, expected)
            typ = expected or value.typ
            value = self.coerce(value, typ, statement.value.span)
            self.validate_box_position(typ, binding.span, "local type")
            if is_task(typ) and not isinstance(value, ir.IRSpawn):
                raise self.error("Task<T> is linear and cannot be copied", statement.value.span)
            self.define_local(binding.name, typ, binding.span)
            if is_task(typ):
                self.task_scopes[-1][binding.name] = (False, binding.span)
            return ir.IRLet(statement.span, (binding.name,), (typ,), value)

        annotations = [self.resolve_optional_annotation(binding.type_node) for binding in statement.bindings]
        tuple_expected = None
        if all(item is not None for item in annotations):
            tuple_expected = TupleType(tuple(item for item in annotations if item is not None))
        value = self.lower_expr(statement.value, tuple_expected)
        if not isinstance(value.typ, TupleType):
            raise self.error("destructuring requires a tuple value", statement.value.span)
        if len(value.typ.items) != len(statement.bindings):
            raise self.error(
                f"destructuring expects {len(statement.bindings)} values, got {len(value.typ.items)}",
                statement.span,
            )
        final_types: list[Type] = []
        for binding, annotation, actual in zip(statement.bindings, annotations, value.typ.items):
            typ = annotation or actual
            self.validate_box_position(typ, binding.span, "destructured local type")
            if not assignable(typ, actual):
                raise self.error(
                    f"cannot bind `{binding.name}: {typ.display()}` from `{actual.display()}`",
                    binding.span,
                )
            self.define_local(binding.name, typ, binding.span)
            final_types.append(typ)
        return ir.IRLet(statement.span, tuple(item.name for item in statement.bindings), tuple(final_types), value)

    def resolve_optional_annotation(self, node: ast.TypeNode | None) -> Type | None:
        if node is None:
            return None
        if self.current_function is None:
            return resolve_type_node(node, set(self.structs), set(), self.source_name)
        type_vars = set(self.current_function.node.type_params)
        resolved = resolve_type_node(node, set(self.structs), type_vars, self.source_name)
        return substitute(resolved, self.current_function.type_mapping)

    def lower_value_sequence(self, values: tuple[ast.Expr, ...], expected: Type, span: SourceSpan) -> ir.IRExpr | None:
        if expected == VOID:
            if values:
                raise self.error("void context cannot produce a value", span)
            return None
        if isinstance(expected, TupleType):
            if len(values) == 1:
                return self.lower_expr(values[0], expected)
            if len(values) != len(expected.items):
                raise self.error(
                    f"expected {len(expected.items)} values, got {len(values)}",
                    span,
                )
            items = tuple(self.lower_expr(value, item_type) for value, item_type in zip(values, expected.items))
            return ir.IRTuple(expected, span, items)
        if len(values) != 1:
            raise self.error(f"expected one `{expected.display()}` value", span)
        return self.lower_expr(values[0], expected)

    def lower_expr(self, expression: ast.Expr, expected: Type | None = None) -> ir.IRExpr:
        if isinstance(expression, ast.IntExpr):
            literal_expected = expected.inner if isinstance(expected, OptionalType) else expected
            typ = literal_expected if literal_expected is not None and is_integer(literal_expected) else PrimitiveType("i32")
            if not integer_literal_fits(expression.value, typ):
                raise self.error(
                    f"integer literal {expression.value} does not fit `{typ.display()}`",
                    expression.span,
                )
            return self.coerce(ir.IRInt(typ, expression.span, expression.value), expected, expression.span)
        if isinstance(expression, ast.StringExpr):
            return self.coerce(ir.IRString(STR, expression.span, expression.value), expected, expression.span)
        if isinstance(expression, ast.FStringExpr):
            parts: list[str | ir.IRExpr] = []
            for item in expression.parts:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                value = self.lower_expr(item)
                if not (
                    value.typ in {STR, BOOL}
                    or is_numeric(value.typ)
                    or (isinstance(value.typ, OptionalType) and value.typ.inner == STR)
                ):
                    raise self.error(
                        f"f-string interpolation does not support `{value.typ.display()}`; use str, numeric, bool, or str | null",
                        item.span,
                    )
                parts.append(value)
            return self.coerce(ir.IRFString(STR, expression.span, tuple(parts)), expected, expression.span)
        if isinstance(expression, ast.BoolExpr):
            return self.coerce(ir.IRBool(BOOL, expression.span, expression.value), expected, expression.span)
        if isinstance(expression, ast.NullExpr):
            if not isinstance(expected, OptionalType):
                raise self.error("`null` needs an explicit optional type such as `i32 | null`", expression.span)
            return ir.IRNull(expected, expression.span)
        if isinstance(expression, ast.NameExpr):
            if expression.name == "FILE__":
                source_name = expression.span.source_name
                if not source_name.startswith("<"):
                    source_name = str(Path(source_name).resolve())
                return self.coerce(
                    ir.IRString(STR, expression.span, source_name),
                    expected,
                    expression.span,
                )
            if expression.name == "GET_ARGS__":
                self.require_main_process_value(expression.name, expression.span)
                args_type = NamedType("List", (STR,))
                return self.coerce(
                    ir.IRCall(args_type, expression.span, "__zy2_get_args", ()),
                    expected,
                    expression.span,
                )
            if expression.name == "GET_EXE__":
                self.require_main_process_value(expression.name, expression.span)
                return self.coerce(
                    ir.IRCall(STR, expression.span, "__zy2_get_exe", ()),
                    expected,
                    expression.span,
                )
            local_type = self.find_local(expression.name)
            if local_type is not None:
                if is_task(local_type):
                    raise self.error("Task<T> can only be consumed with `await task`", expression.span)
                return self.coerce(ir.IRName(local_type, expression.span, expression.name), expected, expression.span)
            symbol = self.functions.get(expression.name)
            if symbol is not None:
                return self.coerce(self.lower_function_ref(symbol, expression.span), expected, expression.span)
            raise self.error(f"unknown name `{expression.name}`", expression.span)
        if isinstance(expression, ast.UnaryExpr):
            operand = self.lower_expr(expression.operand, expected if expression.operator == "-" else None)
            if expression.operator == "-" and not is_numeric(operand.typ):
                raise self.error("unary `-` requires a numeric value", expression.span)
            if expression.operator == "!" and operand.typ != BOOL:
                raise self.error("unary `!` requires bool", expression.span)
            typ = BOOL if expression.operator == "!" else operand.typ
            return self.coerce(ir.IRUnary(typ, expression.span, expression.operator, operand), expected, expression.span)
        if isinstance(expression, ast.BinaryExpr):
            return self.lower_binary(expression, expected)
        if isinstance(expression, ast.CastExpr):
            value = self.lower_expr(expression.value)
            target_type = self.resolve_optional_annotation(expression.target_type)
            assert target_type is not None
            if not self.can_explicitly_cast(value.typ, target_type):
                raise self.error(
                    f"cannot cast `{value.typ.display()}` to `{target_type.display()}`",
                    expression.span,
                )
            return self.coerce(ir.IRCast(target_type, expression.span, value), expected, expression.span)
        if isinstance(expression, ast.FieldExpr):
            qualified = self.qualified_name(expression)
            symbol = self.functions.get(qualified) if qualified is not None else None
            if symbol is not None:
                return self.coerce(self.lower_function_ref(symbol, expression.span), expected, expression.span)
            return self.coerce(self.lower_field(expression), expected, expression.span)
        if isinstance(expression, ast.IndexExpr):
            receiver = self.lower_expr(expression.receiver)
            if not is_list(receiver.typ):
                raise self.error(
                    f"indexing with `[]` requires `List<T>`, got `{receiver.typ.display()}`",
                    expression.receiver.span,
                )
            index = self.lower_expr(expression.index, PrimitiveType("i32"))
            assert isinstance(receiver.typ, NamedType)
            value = ir.IRCall(
                receiver.typ.args[0],
                expression.span,
                "__zy2_list_get",
                (receiver, index),
                ERROR,
            )
            return self.coerce(value, expected, expression.span)
        if isinstance(expression, ast.CallExpr):
            return self.coerce(self.lower_call(expression, expected), expected, expression.span)
        if isinstance(expression, ast.TupleExpr):
            expected_items = expected.items if isinstance(expected, TupleType) else (None,) * len(expression.items)
            if len(expected_items) != len(expression.items):
                raise self.error("tuple arity does not match expected type", expression.span)
            items = tuple(self.lower_expr(item, wanted) for item, wanted in zip(expression.items, expected_items))
            typ = TupleType(tuple(item.typ for item in items))
            return self.coerce(ir.IRTuple(typ, expression.span, items), expected, expression.span)
        if isinstance(expression, ast.ListExpr):
            return self.lower_list(expression, expected)
        if isinstance(expression, ast.StructExpr):
            return self.coerce(self.lower_struct_expr(expression), expected, expression.span)
        if isinstance(expression, ast.CatchExpr):
            value = self.lower_expr(expression.value)
            if not isinstance(value, ir.IRCall) or value.throws is None:
                raise self.error("catch must be attached directly to a throwing function or method call", expression.value.span)
            self.push_scope()
            self.catch_result_types.append(value.typ)
            try:
                self.define_local(expression.error_name, ERROR, expression.span)
                handler = self.lower_block(expression.handler, push_scope=False)
            finally:
                self.catch_result_types.pop()
                self.pop_scope()
            if not self.block_catch_completes(handler):
                raise self.error("catch must end with recover, return, or stop on every path", expression.handler.span)
            return self.coerce(ir.IRCatch(value.typ, expression.span, value, expression.error_name, handler), expected, expression.span)
        if isinstance(expression, ast.SpawnExpr):
            call = self.lower_expr(expression.call)
            if not isinstance(call, ir.IRCall):
                raise self.error("spawn requires a direct function or method call", expression.span)
            if call.args:
                raise self.error("the first safe spawn milestone accepts only zero-argument calls", expression.span)
            if call.throws is not None:
                raise self.error("spawn of a throwing function is not supported yet", expression.span)
            if not isinstance(call.typ, PrimitiveType) or call.typ in {STR, ERROR}:
                raise self.error("spawn currently returns only numeric, bool, or void values", expression.span)
            self.spawn_counter += 1
            self.features.add("threads")
            task_type = NamedType("Task", (call.typ,))
            return self.coerce(ir.IRSpawn(task_type, expression.span, call, self.spawn_counter), expected, expression.span)
        if isinstance(expression, ast.AwaitExpr):
            if not isinstance(expression.task, ast.NameExpr):
                raise self.error("await requires the local name returned by spawn", expression.task.span)
            task_type = self.consume_task(expression.task.name, expression.task.span)
            if not is_task(task_type):
                raise self.error("await requires Task<T>", expression.task.span)
            task = ir.IRName(task_type, expression.task.span, expression.task.name)
            return self.coerce(ir.IRAwait(task_type.args[0], expression.span, task), expected, expression.span)
        if isinstance(expression, ast.TypeOfExpr):
            value = self.lower_expr(expression.value)
            target_type = self.resolve_optional_annotation(expression.target_type)
            assert target_type is not None
            return self.coerce(
                ir.IRBool(BOOL, expression.span, value.typ == target_type),
                expected,
                expression.span,
            )
        raise self.error("unsupported expression", expression.span)

    @staticmethod
    def can_explicitly_cast(source: Type, target: Type) -> bool:
        if source == target:
            return True
        if isinstance(source, TypeVar) or isinstance(target, TypeVar):
            return True
        if is_numeric(source) and is_numeric(target):
            return True
        if (source == BOOL and is_numeric(target)) or (is_numeric(source) and target == BOOL):
            return True
        if target == STR and (source == BOOL or is_numeric(source)):
            return True
        if target == STR and isinstance(source, OptionalType) and source.inner == STR:
            return True
        return False

    def require_main_process_value(self, name: str, span: SourceSpan) -> None:
        if self.current_function is None or self.current_function.name != "main" or self.current_function.receiver is not None:
            raise self.error(
                f"`{name}` is only available inside `fn main()`; pass its value to helper functions explicitly",
                span,
            )

    def lower_binary(self, expression: ast.BinaryExpr, expected: Type | None) -> ir.IRExpr:
        if expression.operator in {"==", "!="} and (
            isinstance(expression.left, ast.NullExpr) or isinstance(expression.right, ast.NullExpr)
        ):
            other_ast = expression.right if isinstance(expression.left, ast.NullExpr) else expression.left
            other = self.lower_expr(other_ast)
            if not isinstance(other.typ, OptionalType):
                raise self.error("only optional values can be compared with null", expression.span)
            null_value = ir.IRNull(other.typ, expression.span)
            left = null_value if isinstance(expression.left, ast.NullExpr) else other
            right = other if isinstance(expression.left, ast.NullExpr) else null_value
            return ir.IRBinary(BOOL, expression.span, left, expression.operator, right)

        left = self.lower_expr(expression.left)
        right = self.lower_expr(expression.right)
        if expression.operator in {"+", "-", "*", "/", "%"}:
            if isinstance(left.typ, TypeVar) and is_numeric(right.typ):
                self.coerce(left, right.typ, expression.left.span)
            if is_numeric(left.typ) and isinstance(right.typ, TypeVar):
                self.coerce(right, left.typ, expression.right.span)
            if isinstance(left.typ, TypeVar) or isinstance(right.typ, TypeVar):
                raise self.error("generic arithmetic operands require explicit numeric casts", expression.span)
            common_type = common_numeric_type(left.typ, right.typ)
            if common_type is None:
                if is_numeric(left.typ) and is_numeric(right.typ):
                    raise self.error(
                        f"no lossless common numeric type for `{left.typ.display()}` and `{right.typ.display()}`; use an explicit cast",
                        expression.span,
                    )
                raise self.error("arithmetic operands must be numeric", expression.span)
            if expression.operator == "%" and not is_integer(common_type):
                raise self.error("modulo operands must be integers", expression.span)
            if left.typ != common_type:
                left = ir.IRCast(common_type, expression.left.span, left)
            if right.typ != common_type:
                right = ir.IRCast(common_type, expression.right.span, right)
            return self.coerce(
                ir.IRBinary(common_type, expression.span, left, expression.operator, right),
                expected,
                expression.span,
            )
        if expression.operator in {"<", "<=", ">", ">="}:
            if not is_numeric(left.typ) or not is_numeric(right.typ):
                raise self.error("comparison operands must be numeric", expression.span)
            return ir.IRBinary(BOOL, expression.span, left, expression.operator, right)
        if expression.operator in {"==", "!="}:
            if is_numeric(left.typ) and is_numeric(right.typ):
                return ir.IRBinary(BOOL, expression.span, left, expression.operator, right)
            if left.typ != right.typ:
                raise self.error("equality operands must have the same type", expression.span)
            if not self.supports_equality(left.typ):
                raise self.error(
                    f"equality is not defined for `{left.typ.display()}`; compare its values explicitly",
                    expression.span,
                )
            return ir.IRBinary(BOOL, expression.span, left, expression.operator, right)
        if expression.operator in {"&&", "||"}:
            if left.typ != BOOL or right.typ != BOOL:
                raise self.error("logical operands must be bool", expression.span)
            return ir.IRBinary(BOOL, expression.span, left, expression.operator, right)
        raise self.error(f"unsupported binary operator `{expression.operator}`", expression.span)

    def lower_field(self, expression: ast.FieldExpr) -> ir.IRExpr:
        receiver = self.lower_expr(expression.receiver)
        if is_box(receiver.typ):
            assert isinstance(receiver.typ, NamedType)
            if expression.name == "value":
                return ir.IRBoxValue(receiver.typ.args[0], expression.span, receiver)
            if expression.name == "__strong_count__":
                return ir.IRArcCount(PrimitiveType("usize"), expression.span, receiver)
            raise self.error(f"`{receiver.typ.display()}` has no field `{expression.name}`", expression.span)
        if receiver.typ == ERROR:
            error_fields = {
                "message": STR,
                "file": STR,
                "line": PrimitiveType("u32"),
                "column": PrimitiveType("u32"),
            }
            field_type = error_fields.get(expression.name)
            if field_type is not None:
                runtime_name = "source_file" if expression.name == "file" else expression.name
                return ir.IRField(field_type, expression.span, receiver, runtime_name)
        if not isinstance(receiver.typ, NamedType) or receiver.typ.name not in self.structs:
            raise self.error(f"`{receiver.typ.display()}` has no field `{expression.name}`", expression.span)
        if category := STRUCT_METADATA_FIELDS.get(expression.name):
            metadata_type = NamedType("List", (STR,))
            return ir.IRStructMetadata(metadata_type, expression.span, receiver, receiver.typ.name, category)
        symbol = self.structs[receiver.typ.name]
        field = symbol.fields.get(expression.name)
        if field is None:
            raise self.error(f"struct `{symbol.name}` has no field `{expression.name}`", expression.span)
        if field.visibility == "private" and self.current_receiver != symbol.name:
            raise self.error(f"field `{symbol.name}.{field.name}` is private", expression.span)
        return ir.IRField(field.typ, expression.span, receiver, field.name)

    def lower_call(self, expression: ast.CallExpr, expected: Type | None = None) -> ir.IRExpr:
        if isinstance(expression.callee, ast.NameExpr):
            name = expression.callee.name
            if name == "LIST_LEN__":
                if len(expression.args) != 1:
                    raise self.error("LIST_LEN__ expects exactly one List<T> value", expression.span)
                value = self.lower_expr(expression.args[0])
                if not is_list(value.typ):
                    raise self.error(
                        f"LIST_LEN__ expects `List<T>`, got `{value.typ.display()}`",
                        expression.args[0].span,
                    )
                return ir.IRCall(PrimitiveType("usize"), expression.span, "zy2_list_len", (value,))
            if name == "LIST_PUSH__":
                if len(expression.args) != 2:
                    raise self.error("LIST_PUSH__ expects a List<T> variable and one value", expression.span)
                self.require_mutable_list_receiver(expression.args[0], "LIST_PUSH__")
                receiver = self.lower_expr(expression.args[0])
                if not is_list(receiver.typ):
                    raise self.error(
                        f"LIST_PUSH__ expects `List<T>`, got `{receiver.typ.display()}`",
                        expression.args[0].span,
                    )
                assert isinstance(receiver.typ, NamedType)
                value = self.lower_expr(expression.args[1], receiver.typ.args[0])
                return ir.IRCall(VOID, expression.span, "__zy2_list_push", (receiver, value))
            if name == "LIST_SET__":
                if len(expression.args) != 3:
                    raise self.error("LIST_SET__ expects a List<T> variable, i32 index, and one value", expression.span)
                self.require_mutable_list_receiver(expression.args[0], "LIST_SET__")
                receiver = self.lower_expr(expression.args[0])
                if not is_list(receiver.typ):
                    raise self.error(
                        f"LIST_SET__ expects `List<T>`, got `{receiver.typ.display()}`",
                        expression.args[0].span,
                    )
                assert isinstance(receiver.typ, NamedType)
                index = self.lower_expr(expression.args[1], PrimitiveType("i32"))
                value = self.lower_expr(expression.args[2], receiver.typ.args[0])
                return ir.IRCall(VOID, expression.span, "__zy2_list_set", (receiver, index, value), ERROR)
            if name == "Box":
                if len(expression.args) != 1:
                    raise self.error("Box expects exactly one value", expression.span)
                expected_inner = expected.args[0] if is_box(expected) else None
                value = self.lower_expr(expression.args[0], expected_inner)
                box_type = NamedType("Box", (value.typ,))
                self.validate_box_payload(box_type, expression.span)
                return ir.IRBox(box_type, expression.span, value)
            if name in {"__runtime_write_line", "__runtime_write_error"}:
                if len(expression.args) != 1:
                    raise self.error(f"{name} expects exactly one str", expression.span)
                arg = self.lower_expr(expression.args[0], STR)
                target = "zy2_print" if name == "__runtime_write_line" else "zy2_eprint"
                return ir.IRCall(VOID, expression.span, target, (arg,))
            local_type = self.find_local(name)
            if local_type is not None:
                callee = ir.IRName(local_type, expression.callee.span, name)
                return self.lower_indirect_call(callee, expression.args, expression.span)
            symbol = self.functions.get(name)
            if symbol is None:
                raise self.error(f"unknown function `{name}`", expression.span)
            if symbol.type_params:
                return self.lower_generic_call(symbol, expression.args, (), expression.span)
            return self.lower_symbol_call(symbol, expression.args, (), expression.span)

        if isinstance(expression.callee, ast.FieldExpr):
            qualified = self.qualified_name(expression.callee)
            imported = self.functions.get(qualified) if qualified is not None else None
            if imported is not None:
                if imported.visibility == "private" and self.module_name(self.current_function.name if self.current_function else "") != self.module_name(qualified):
                    raise self.error(f"function `{qualified}` is private", expression.span)
                if imported.type_params:
                    return self.lower_generic_call(imported, expression.args, (), expression.span)
                return self.lower_symbol_call(imported, expression.args, (), expression.span)
            receiver = self.lower_expr(expression.callee.receiver)
            method_name = expression.callee.name
            if is_list(receiver.typ) and method_name == "len":
                if expression.args:
                    raise self.error("List.len takes no arguments", expression.span)
                return ir.IRCall(PrimitiveType("usize"), expression.span, "zy2_list_len", (receiver,))
            if is_list(receiver.typ) and method_name == "capacity":
                if expression.args:
                    raise self.error("List.capacity takes no arguments", expression.span)
                return ir.IRCall(PrimitiveType("usize"), expression.span, "zy2_list_capacity", (receiver,))
            if is_list(receiver.typ) and method_name == "is_empty":
                if expression.args:
                    raise self.error("List.is_empty takes no arguments", expression.span)
                return ir.IRCall(BOOL, expression.span, "zy2_list_is_empty", (receiver,))
            if is_list(receiver.typ) and method_name == "get":
                if len(expression.args) != 1:
                    raise self.error("List.get expects exactly one i32 index", expression.span)
                index = self.lower_expr(expression.args[0], PrimitiveType("i32"))
                assert isinstance(receiver.typ, NamedType)
                return ir.IRCall(
                    receiver.typ.args[0],
                    expression.span,
                    "__zy2_list_get",
                    (receiver, index),
                    ERROR,
                )
            if is_list(receiver.typ) and method_name in {"push", "add"}:
                self.require_mutable_list_receiver(expression.callee.receiver, method_name)
                if len(expression.args) != 1:
                    raise self.error(f"List.{method_name} expects exactly one value", expression.span)
                assert isinstance(receiver.typ, NamedType)
                value = self.lower_expr(expression.args[0], receiver.typ.args[0])
                return ir.IRCall(VOID, expression.span, "__zy2_list_push", (receiver, value))
            if is_list(receiver.typ) and method_name == "set":
                self.require_mutable_list_receiver(expression.callee.receiver, method_name)
                if len(expression.args) != 2:
                    raise self.error("List.set expects an i32 index and one value", expression.span)
                assert isinstance(receiver.typ, NamedType)
                index = self.lower_expr(expression.args[0], PrimitiveType("i32"))
                value = self.lower_expr(expression.args[1], receiver.typ.args[0])
                return ir.IRCall(VOID, expression.span, "__zy2_list_set", (receiver, index, value), ERROR)
            if is_list(receiver.typ) and method_name == "pop":
                self.require_mutable_list_receiver(expression.callee.receiver, method_name)
                if expression.args:
                    raise self.error("List.pop takes no arguments", expression.span)
                assert isinstance(receiver.typ, NamedType)
                return ir.IRCall(receiver.typ.args[0], expression.span, "__zy2_list_pop", (receiver,), ERROR)
            if is_list(receiver.typ) and method_name == "remove":
                self.require_mutable_list_receiver(expression.callee.receiver, method_name)
                if len(expression.args) != 1:
                    raise self.error("List.remove expects exactly one i32 index", expression.span)
                assert isinstance(receiver.typ, NamedType)
                index = self.lower_expr(expression.args[0], PrimitiveType("i32"))
                return ir.IRCall(receiver.typ.args[0], expression.span, "__zy2_list_remove", (receiver, index), ERROR)
            if is_list(receiver.typ) and method_name == "clear":
                self.require_mutable_list_receiver(expression.callee.receiver, method_name)
                if expression.args:
                    raise self.error("List.clear takes no arguments", expression.span)
                return ir.IRCall(VOID, expression.span, "__zy2_list_clear", (receiver,))
            if not isinstance(receiver.typ, NamedType) or receiver.typ.name not in self.structs:
                raise self.error(f"`{receiver.typ.display()}` has no method `{method_name}`", expression.span)
            symbol = self.methods.get((receiver.typ.name, method_name))
            if symbol is None:
                struct = self.structs[receiver.typ.name]
                field = struct.fields.get(method_name)
                if field is None:
                    raise self.error(f"struct `{receiver.typ.name}` has no method `{method_name}`", expression.span)
                if field.visibility == "private" and self.current_receiver != receiver.typ.name:
                    raise self.error(f"field `{receiver.typ.name}.{field.name}` is private", expression.span)
                callee = ir.IRField(field.typ, expression.callee.span, receiver, field.name)
                return self.lower_indirect_call(callee, expression.args, expression.span)
            if symbol.visibility == "private" and self.current_receiver != receiver.typ.name:
                raise self.error(f"method `{receiver.typ.name}.{method_name}` is private", expression.span)
            return self.lower_symbol_call(symbol, expression.args, (receiver,), expression.span)
        callee = self.lower_expr(expression.callee)
        return self.lower_indirect_call(callee, expression.args, expression.span)

    def lower_function_ref(self, symbol: FunctionSymbol, span: SourceSpan) -> ir.IRFunctionRef:
        current_module = self.module_name(self.current_function.name if self.current_function else "")
        if symbol.visibility == "private" and current_module != self.module_name(symbol.name):
            raise self.error(f"function `{symbol.name}` is private", span)
        if symbol.type_params:
            raise self.error(
                f"generic function `{symbol.name}` cannot become a function value until its type arguments are explicit",
                span,
            )
        if symbol.throws is not None:
            raise self.error(
                f"throwing function `{symbol.name}` cannot become `fn(...) R`; callback error effects are not enabled yet",
                span,
            )
        typ = FunctionType(tuple(item for _, item, _ in symbol.params), symbol.return_type)
        return ir.IRFunctionRef(typ, span, symbol.c_name)

    def lower_indirect_call(
        self,
        callee: ir.IRExpr,
        args: tuple[ast.Expr, ...],
        span: SourceSpan,
    ) -> ir.IRIndirectCall:
        if not isinstance(callee.typ, FunctionType):
            raise self.error(f"value of type `{callee.typ.display()}` is not callable", span)
        if len(args) != len(callee.typ.params):
            raise self.error(
                f"function value `{callee.typ.display()}` expects {len(callee.typ.params)} arguments, got {len(args)}",
                span,
            )
        lowered = tuple(self.lower_expr(value, typ) for value, typ in zip(args, callee.typ.params))
        return ir.IRIndirectCall(callee.typ.return_type, span, callee, lowered)

    def validate_box_position(self, typ: Type, span: SourceSpan, context: str) -> None:
        self.validate_function_types(typ, span)
        if is_list(typ):
            self.validate_list_element(typ.args[0], span)
            return
        if self.contains_list(typ):
            raise self.error(f"List<T> cannot be nested in a {context} until managed aggregate destructors are enabled", span)
        if not self.contains_box(typ):
            if isinstance(typ, NamedType) and typ.name in self.structs:
                return
            if self.type_requires_management(typ):
                raise self.error(
                    f"managed value `{typ.display()}` cannot be nested in a {context} until tuple and optional destructors are enabled",
                    span,
                )
            return
        if not is_box(typ):
            raise self.error(f"Box<T> cannot be nested in a {context} yet", span)
        self.validate_box_payload(typ, span)

    def validate_function_types(self, typ: Type, span: SourceSpan) -> None:
        if isinstance(typ, FunctionType):
            for item in (*typ.params, typ.return_type):
                if isinstance(item, FunctionType):
                    self.validate_function_types(item, span)
                    continue
                if isinstance(item, TypeVar):
                    continue
                if isinstance(item, PrimitiveType) and item not in {ERROR, NULL}:
                    continue
                if isinstance(item, NamedType) and item.name in self.structs and not item.args:
                    continue
                raise self.error(
                    f"function value ABI does not support `{item.display()}` yet; use scalar, str, struct, or another fn type",
                    span,
                )
            return
        if isinstance(typ, NamedType):
            for item in typ.args:
                self.validate_function_types(item, span)
        elif isinstance(typ, TupleType):
            for item in typ.items:
                self.validate_function_types(item, span)
        elif isinstance(typ, OptionalType):
            self.validate_function_types(typ.inner, span)

    def validate_box_payload(self, typ: Type, span: SourceSpan) -> None:
        assert isinstance(typ, NamedType) and is_box(typ)
        inner = typ.args[0]
        if inner == VOID or is_task(inner) or self.contains_box(inner):
            raise self.error(
                f"Box payload `{inner.display()}` needs a managed destructor that is not implemented yet",
                span,
            )
        if isinstance(inner, (OptionalType, TupleType)) or is_list(inner):
            raise self.error(f"Box payload `{inner.display()}` is not supported in the first ARC milestone", span)
        if self.type_requires_management(inner):
            raise self.error(
                f"Box payload `{inner.display()}` contains managed fields and needs a recursive Box destructor",
                span,
            )

    def validate_list_element(self, element: Type, span: SourceSpan) -> None:
        if is_task(element):
            raise self.error("List<Task<T>> is not allowed because Task is linear", span)
        if is_box(element):
            self.validate_box_payload(element, span)
            return
        if is_list(element):
            self.validate_list_element(element.args[0], span)
            return
        if self.contains_box(element) or self.contains_list(element):
            raise self.error(
                f"List element `{element.display()}` needs a managed aggregate destructor that is not implemented yet",
                span,
            )

    @classmethod
    def contains_box(cls, typ: Type) -> bool:
        if is_box(typ):
            return True
        if isinstance(typ, NamedType):
            return any(cls.contains_box(arg) for arg in typ.args)
        if isinstance(typ, TupleType):
            return any(cls.contains_box(item) for item in typ.items)
        if isinstance(typ, OptionalType):
            return cls.contains_box(typ.inner)
        return False

    @classmethod
    def contains_list(cls, typ: Type) -> bool:
        if is_list(typ):
            return True
        if isinstance(typ, NamedType):
            return any(cls.contains_list(arg) for arg in typ.args)
        if isinstance(typ, TupleType):
            return any(cls.contains_list(item) for item in typ.items)
        if isinstance(typ, OptionalType):
            return cls.contains_list(typ.inner)
        return False

    @classmethod
    def contains_type_var(cls, typ: Type) -> bool:
        if isinstance(typ, TypeVar):
            return True
        if isinstance(typ, NamedType):
            return any(cls.contains_type_var(arg) for arg in typ.args)
        if isinstance(typ, TupleType):
            return any(cls.contains_type_var(item) for item in typ.items)
        if isinstance(typ, OptionalType):
            return cls.contains_type_var(typ.inner)
        if isinstance(typ, FunctionType):
            return any(cls.contains_type_var(item) for item in typ.params) or cls.contains_type_var(typ.return_type)
        return False

    def supports_equality(self, typ: Type, visiting: set[str] | None = None) -> bool:
        if isinstance(typ, TypeVar):
            return True
        if typ in {BOOL, STR} or is_numeric(typ) or is_box(typ):
            return True
        if isinstance(typ, FunctionType):
            return True
        if isinstance(typ, OptionalType):
            return self.supports_equality(typ.inner, visiting)
        if isinstance(typ, TupleType):
            return all(self.supports_equality(item, visiting) for item in typ.items)
        if isinstance(typ, NamedType) and typ.name in self.structs:
            visiting = set() if visiting is None else set(visiting)
            if typ.name in visiting:
                return True
            visiting.add(typ.name)
            return all(
                self.supports_equality(field.typ, visiting)
                for field in self.structs[typ.name].fields.values()
            )
        return False

    def type_requires_management(self, typ: Type, visiting: set[str] | None = None) -> bool:
        if is_box(typ) or is_list(typ):
            return True
        if isinstance(typ, NamedType) and typ.name in self.structs:
            visiting = set() if visiting is None else set(visiting)
            if typ.name in visiting:
                return False
            visiting.add(typ.name)
            return any(
                self.type_requires_management(field.typ, visiting)
                for field in self.structs[typ.name].fields.values()
            )
        if isinstance(typ, TupleType):
            return any(self.type_requires_management(item, visiting) for item in typ.items)
        if isinstance(typ, OptionalType):
            return self.type_requires_management(typ.inner, visiting)
        return False

    def require_mutable_list_receiver(self, receiver: ast.Expr, method_name: str) -> None:
        root = receiver
        while isinstance(root, ast.FieldExpr):
            root = root.receiver
        if not isinstance(root, ast.NameExpr):
            operation = method_name if method_name.endswith("__") else f"List.{method_name}"
            raise self.error(
                f"{operation} requires a local List variable or local-rooted List field as its receiver",
                receiver.span,
            )

    @staticmethod
    def qualified_name(expression: ast.Expr) -> str | None:
        parts: list[str] = []
        current = expression
        while isinstance(current, ast.FieldExpr):
            parts.append(current.name)
            current = current.receiver
        if not isinstance(current, ast.NameExpr):
            return None
        parts.append(current.name)
        return ".".join(reversed(parts))

    def lower_generic_call(
        self,
        symbol: FunctionSymbol,
        args: tuple[ast.Expr, ...],
        prefix: tuple[ir.IRExpr, ...],
        span: SourceSpan,
    ) -> ir.IRCall:
        if symbol.receiver is not None:
            raise self.error("generic receiver methods are not part of the bootstrap milestone", span)
        bound_args = self.bind_call_arguments(symbol, args, span)
        raw_args = [
            self.lower_expr(value, None if self.contains_type_var(template) else template)
            for value, (_, template, _) in zip(bound_args, symbol.params)
        ]
        mapping: dict[str, Type] = {}
        for (_, template, _), actual in zip(symbol.params, raw_args):
            self.unify_generic(template, actual.typ, mapping, span)
        missing = [name for name in symbol.type_params if name not in mapping]
        if missing:
            raise self.error(
                f"cannot infer generic type parameter(s) {', '.join(missing)} for `{symbol.name}`",
                span,
            )
        type_args = tuple(mapping[name] for name in symbol.type_params)
        key = (symbol.c_name, type_args)
        instance = self.generic_instances.get(key)
        if instance is None:
            suffix = "__" + "__".join(self.mangle_type(item) for item in type_args)
            concrete_params = tuple(
                (name, substitute(typ, mapping), mutable) for name, typ, mutable in symbol.params
            )
            for _, concrete_type, _ in concrete_params:
                self.validate_box_position(concrete_type, span, "generic parameter")
            concrete_receiver = None
            if symbol.receiver:
                name, typ, mutable = symbol.receiver
                concrete_receiver = (name, substitute(typ, mapping), mutable)
            concrete_return = substitute(symbol.return_type, mapping)
            self.validate_box_position(concrete_return, span, "generic return type")
            instance = FunctionSymbol(
                symbol.name,
                symbol.c_name + suffix,
                symbol.visibility,
                concrete_params,
                concrete_return,
                substitute(symbol.throws, mapping) if symbol.throws else None,
                concrete_receiver,
                (),
                symbol.node,
                mapping,
            )
            self.generic_instances[key] = instance
            self.pending_functions.append(instance)
        lowered = list(prefix)
        for value, (_, typ, _) in zip(raw_args, instance.params):
            lowered.append(self.coerce(value, typ, span))
        return ir.IRCall(instance.return_type, span, instance.c_name, tuple(lowered), instance.throws)

    def unify_generic(self, template: Type, actual: Type, mapping: dict[str, Type], span: SourceSpan) -> None:
        if isinstance(template, TypeVar):
            existing = mapping.get(template.name)
            if existing is not None and existing != actual:
                raise self.error(
                    f"generic type `{template.name}` was inferred as both `{existing.display()}` and `{actual.display()}`",
                    span,
                )
            mapping[template.name] = actual
            return
        if isinstance(template, NamedType) and isinstance(actual, NamedType):
            if template.name != actual.name or len(template.args) != len(actual.args):
                raise self.error(
                    f"generic argument expected `{template.display()}`, got `{actual.display()}`",
                    span,
                )
            for template_arg, actual_arg in zip(template.args, actual.args):
                self.unify_generic(template_arg, actual_arg, mapping, span)
            return
        if isinstance(template, TupleType) and isinstance(actual, TupleType) and len(template.items) == len(actual.items):
            for template_item, actual_item in zip(template.items, actual.items):
                self.unify_generic(template_item, actual_item, mapping, span)
            return
        if isinstance(template, OptionalType) and isinstance(actual, OptionalType):
            self.unify_generic(template.inner, actual.inner, mapping, span)
            return
        if (
            isinstance(template, FunctionType)
            and isinstance(actual, FunctionType)
            and len(template.params) == len(actual.params)
        ):
            for template_param, actual_param in zip(template.params, actual.params):
                self.unify_generic(template_param, actual_param, mapping, span)
            self.unify_generic(template.return_type, actual.return_type, mapping, span)
            return
        if template != actual:
            raise self.error(
                f"generic argument expected `{template.display()}`, got `{actual.display()}`",
                span,
            )

    @staticmethod
    def mangle_type(typ: Type) -> str:
        value = typ.display()
        return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_")

    @staticmethod
    def module_name(name: str) -> str:
        return name.rsplit(".", 1)[0] if "." in name else ""

    def lower_symbol_call(
        self,
        symbol: FunctionSymbol,
        args: tuple[ast.Expr, ...],
        prefix: tuple[ir.IRExpr, ...],
        span: SourceSpan,
    ) -> ir.IRCall:
        bound_args = self.bind_call_arguments(symbol, args, span)
        lowered = list(prefix)
        for value, (_, typ, _) in zip(bound_args, symbol.params):
            lowered.append(self.lower_expr(value, typ))
        return ir.IRCall(symbol.return_type, span, symbol.c_name, tuple(lowered), symbol.throws)

    def bind_call_arguments(
        self,
        symbol: FunctionSymbol,
        args: tuple[ast.Expr, ...],
        span: SourceSpan,
    ) -> tuple[ast.Expr, ...]:
        param_nodes = symbol.node.params
        minimum = sum(1 for param in param_nodes if param.default is None)
        maximum = len(param_nodes)
        if len(args) < minimum or len(args) > maximum:
            expected = str(maximum) if minimum == maximum else f"{minimum} to {maximum}"
            raise self.error(f"`{symbol.name}` expects {expected} arguments, got {len(args)}", span)

        bound: list[ast.Expr] = []
        supplied = 0
        for index, param in enumerate(param_nodes):
            required_after = sum(1 for later in param_nodes[index + 1 :] if later.default is None)
            remaining = len(args) - supplied
            if param.default is not None and remaining <= required_after:
                bound.append(param.default)
                continue
            if supplied < len(args):
                bound.append(args[supplied])
                supplied += 1
                continue
            assert param.default is not None
            bound.append(param.default)
        return tuple(bound)

    def lower_list(self, expression: ast.ListExpr, expected: Type | None) -> ir.IRList:
        element_expected = expected.args[0] if is_list(expected) else None
        if not expression.items:
            if element_expected is None:
                raise self.error("empty List literal needs an explicit `List<T>` type", expression.span)
            self.validate_list_element(element_expected, expression.span)
            typ = NamedType("List", (element_expected,))
            return ir.IRList(typ, expression.span, ())
        first = self.lower_expr(expression.items[0], element_expected)
        element_type = element_expected or first.typ
        items = [self.coerce(first, element_type, expression.items[0].span)]
        for item in expression.items[1:]:
            items.append(self.lower_expr(item, element_type))
        typ = NamedType("List", (element_type,))
        self.validate_list_element(element_type, expression.span)
        return self.coerce(ir.IRList(typ, expression.span, tuple(items)), expected, expression.span)

    def lower_struct_expr(self, expression: ast.StructExpr) -> ir.IRStruct:
        symbol = self.structs.get(expression.name)
        if symbol is None:
            raise self.error(f"unknown struct `{expression.name}`", expression.span)
        current_module = self.module_name(self.current_function.name if self.current_function else "")
        if symbol.visibility == "private" and current_module != self.module_name(symbol.name):
            raise self.error(f"struct `{symbol.name}` is private", expression.span)
        if symbol.type_params:
            raise self.error("generic struct construction is scheduled after the bootstrap milestone", expression.span)
        seen: set[str] = set()
        fields: list[tuple[str, ir.IRExpr]] = []
        for item in expression.fields:
            if item.name in seen:
                raise self.error(f"duplicate initializer for `{expression.name}.{item.name}`", item.span)
            seen.add(item.name)
            field = symbol.fields.get(item.name)
            if field is None:
                raise self.error(f"struct `{expression.name}` has no field `{item.name}`", item.span)
            if field.visibility == "private" and self.current_receiver != expression.name:
                raise self.error(f"field `{expression.name}.{item.name}` is private", item.span)
            fields.append((item.name, self.lower_expr(item.value, field.typ)))
        for field in symbol.fields.values():
            if field.name not in seen and field.node.default is not None:
                fields.append((field.name, self.lower_expr(field.node.default, field.typ)))
        return ir.IRStruct(NamedType(expression.name), expression.span, expression.name, tuple(fields))

    def coerce(self, value: ir.IRExpr, expected: Type | None, span: SourceSpan) -> ir.IRExpr:
        if expected is None or value.typ == expected:
            return value
        if isinstance(expected, OptionalType) and value.typ == expected.inner:
            return ir.IROptionalSome(expected, span, value)
        if assignable(expected, value.typ):
            return value
        if isinstance(value.typ, TypeVar):
            label = f"`{value.name}: {value.typ.display()}`" if isinstance(value, ir.IRName) else f"`{value.typ.display()}`"
            raise self.error(
                f"generic value {label} must be explicitly cast to `{expected.display()}`",
                span,
            )
        if isinstance(expected, TypeVar):
            raise self.error(
                f"value of type `{value.typ.display()}` must be explicitly cast to generic type `{expected.display()}`",
                span,
            )
        raise self.error(
            f"type mismatch: expected `{expected.display()}`, got `{value.typ.display()}`",
            span,
        )

    @staticmethod
    def block_terminates(block: ir.IRBlock) -> bool:
        if not block.statements:
            return False
        final = block.statements[-1]
        if isinstance(final, (ir.IRReturn, ir.IRStop)):
            return True
        if isinstance(final, ir.IRIf):
            return final.else_block is not None and Lowerer.block_terminates(final.then_block) and Lowerer.block_terminates(final.else_block)
        if isinstance(final, ir.IRIfLet):
            return final.else_block is not None and Lowerer.block_terminates(final.then_block) and Lowerer.block_terminates(final.else_block)
        return False

    @staticmethod
    def block_catch_completes(block: ir.IRBlock) -> bool:
        if not block.statements:
            return False
        final = block.statements[-1]
        if isinstance(final, (ir.IRRecover, ir.IRReturn, ir.IRStop)):
            return True
        if isinstance(final, ir.IRIf):
            return final.else_block is not None and Lowerer.block_catch_completes(final.then_block) and Lowerer.block_catch_completes(final.else_block)
        if isinstance(final, ir.IRIfLet):
            return final.else_block is not None and Lowerer.block_catch_completes(final.then_block) and Lowerer.block_catch_completes(final.else_block)
        return False


def lower(program: ast.Program, source_name: str = "<source>", *, require_main: bool = True) -> ir.IRProgram:
    return Lowerer(program, source_name, require_main=require_main).lower()
