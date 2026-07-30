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
    NamedType,
    OptionalType,
    PrimitiveType,
    TupleType,
    Type,
    TypeVar,
    assignable,
    integer_literal_fits,
    is_integer,
    is_list,
    is_numeric,
    is_task,
    resolve_type_node,
    substitute,
)


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
        self.collect_functions()

        lowered_structs = tuple(self.lower_struct(symbol) for symbol in self.structs.values() if not symbol.type_params)
        all_functions = list(self.functions.values()) + list(self.methods.values())
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
                if field.name in symbol.fields:
                    raise self.error(f"duplicate field `{field.name}` in `{symbol.name}`", field.span)
                typ = resolve_type_node(field.type_node, known, type_vars, self.source_name)
                if is_task(typ):
                    raise self.error("Task<T> is linear and cannot be stored in a struct field", field.span)
                symbol.fields[field.name] = FieldSymbol(field.name, typ, field.visibility, field)

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

    def by_value_struct_dependencies(self, typ: Type) -> tuple[str, ...]:
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
                if is_task(params[-1][1]):
                    raise self.error("Task<T> cannot be passed as a function parameter", param.span)
            return_type = resolve_type_node(definition.return_type, known, type_vars, self.source_name)
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
        if name in {"GET_ARGS", "GET_EXE"}:
            raise self.error(f"`{name}` is a reserved process value and cannot be shadowed", span)
        scope = self.scopes[-1]
        if name in scope:
            raise self.error(f"duplicate local `{name}`", span)
        scope[name] = typ

    def lookup_local(self, name: str, span: SourceSpan) -> Type:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise self.error(f"unknown name `{name}`", span)

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
            if statement.target.name in {"GET_ARGS", "GET_EXE"}:
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
            typ = expected if expected is not None and is_integer(expected) else PrimitiveType("i32")
            if not integer_literal_fits(expression.value, typ):
                raise self.error(
                    f"integer literal {expression.value} does not fit `{typ.display()}`",
                    expression.span,
                )
            return ir.IRInt(typ, expression.span, expression.value)
        if isinstance(expression, ast.StringExpr):
            return self.coerce(ir.IRString(STR, expression.span, expression.value), expected, expression.span)
        if isinstance(expression, ast.BoolExpr):
            return self.coerce(ir.IRBool(BOOL, expression.span, expression.value), expected, expression.span)
        if isinstance(expression, ast.NullExpr):
            if not isinstance(expected, OptionalType):
                raise self.error("`null` needs an explicit optional type such as `i32 | null`", expression.span)
            return ir.IRNull(expected, expression.span)
        if isinstance(expression, ast.NameExpr):
            if expression.name == "GET_ARGS":
                args_type = NamedType("List", (STR,))
                return self.coerce(
                    ir.IRCall(args_type, expression.span, "__zy2_get_args", ()),
                    expected,
                    expression.span,
                )
            if expression.name == "GET_EXE":
                return self.coerce(
                    ir.IRCall(STR, expression.span, "__zy2_get_exe", ()),
                    expected,
                    expression.span,
                )
            local_type = self.lookup_local(expression.name, expression.span)
            if is_task(local_type):
                raise self.error("Task<T> can only be consumed with `await task`", expression.span)
            return self.coerce(ir.IRName(local_type, expression.span, expression.name), expected, expression.span)
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
        if isinstance(expression, ast.FieldExpr):
            return self.coerce(self.lower_field(expression), expected, expression.span)
        if isinstance(expression, ast.CallExpr):
            return self.coerce(self.lower_call(expression), expected, expression.span)
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
        right = self.lower_expr(expression.right, left.typ)
        if expression.operator in {"+", "-", "*", "/", "%"}:
            if not is_numeric(left.typ) or left.typ != right.typ:
                raise self.error("arithmetic operands must have the same numeric type", expression.span)
            return self.coerce(ir.IRBinary(left.typ, expression.span, left, expression.operator, right), expected, expression.span)
        if expression.operator in {"<", "<=", ">", ">="}:
            if not is_numeric(left.typ) or left.typ != right.typ:
                raise self.error("comparison operands must have the same numeric type", expression.span)
            return ir.IRBinary(BOOL, expression.span, left, expression.operator, right)
        if expression.operator in {"==", "!="}:
            if left.typ != right.typ:
                raise self.error("equality operands must have the same type", expression.span)
            return ir.IRBinary(BOOL, expression.span, left, expression.operator, right)
        if expression.operator in {"&&", "||"}:
            if left.typ != BOOL or right.typ != BOOL:
                raise self.error("logical operands must be bool", expression.span)
            return ir.IRBinary(BOOL, expression.span, left, expression.operator, right)
        raise self.error(f"unsupported binary operator `{expression.operator}`", expression.span)

    def lower_field(self, expression: ast.FieldExpr) -> ir.IRField:
        receiver = self.lower_expr(expression.receiver)
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
        symbol = self.structs[receiver.typ.name]
        field = symbol.fields.get(expression.name)
        if field is None:
            raise self.error(f"struct `{symbol.name}` has no field `{expression.name}`", expression.span)
        if field.visibility == "private" and self.current_receiver != symbol.name:
            raise self.error(f"field `{symbol.name}.{field.name}` is private", expression.span)
        return ir.IRField(field.typ, expression.span, receiver, field.name)

    def lower_call(self, expression: ast.CallExpr) -> ir.IRCall:
        if isinstance(expression.callee, ast.NameExpr):
            name = expression.callee.name
            if name in {"__runtime_write_line", "__runtime_write_error"}:
                if len(expression.args) != 1:
                    raise self.error(f"{name} expects exactly one str", expression.span)
                arg = self.lower_expr(expression.args[0], STR)
                target = "zy2_print" if name == "__runtime_write_line" else "zy2_eprint"
                return ir.IRCall(VOID, expression.span, target, (arg,))
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
            if not isinstance(receiver.typ, NamedType) or receiver.typ.name not in self.structs:
                raise self.error(f"`{receiver.typ.display()}` has no method `{method_name}`", expression.span)
            symbol = self.methods.get((receiver.typ.name, method_name))
            if symbol is None:
                raise self.error(f"struct `{receiver.typ.name}` has no method `{method_name}`", expression.span)
            if symbol.visibility == "private" and self.current_receiver != receiver.typ.name:
                raise self.error(f"method `{receiver.typ.name}.{method_name}` is private", expression.span)
            return self.lower_symbol_call(symbol, expression.args, (receiver,), expression.span)
        raise self.error("call target must be a function or method", expression.span)

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
        if len(args) != len(symbol.params):
            raise self.error(
                f"`{symbol.name}` expects {len(symbol.params)} arguments, got {len(args)}",
                span,
            )
        raw_args = [self.lower_expr(value) for value in args]
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
            concrete_receiver = None
            if symbol.receiver:
                name, typ, mutable = symbol.receiver
                concrete_receiver = (name, substitute(typ, mapping), mutable)
            instance = FunctionSymbol(
                symbol.name,
                symbol.c_name + suffix,
                symbol.visibility,
                concrete_params,
                substitute(symbol.return_type, mapping),
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
        if len(args) != len(symbol.params):
            raise self.error(
                f"`{symbol.name}` expects {len(symbol.params)} arguments, got {len(args)}",
                span,
            )
        lowered = list(prefix)
        for value, (_, typ, _) in zip(args, symbol.params):
            lowered.append(self.lower_expr(value, typ))
        return ir.IRCall(symbol.return_type, span, symbol.c_name, tuple(lowered), symbol.throws)

    def lower_list(self, expression: ast.ListExpr, expected: Type | None) -> ir.IRList:
        element_expected = expected.args[0] if is_list(expected) else None
        if not expression.items:
            if element_expected is None:
                raise self.error("empty List literal needs an explicit `List<T>` type", expression.span)
            typ = NamedType("List", (element_expected,))
            return ir.IRList(typ, expression.span, ())
        first = self.lower_expr(expression.items[0], element_expected)
        element_type = element_expected or first.typ
        items = [self.coerce(first, element_type, expression.items[0].span)]
        for item in expression.items[1:]:
            items.append(self.lower_expr(item, element_type))
        typ = NamedType("List", (element_type,))
        return self.coerce(ir.IRList(typ, expression.span, tuple(items)), expected, expression.span)

    def lower_struct_expr(self, expression: ast.StructExpr) -> ir.IRStruct:
        symbol = self.structs.get(expression.name)
        if symbol is None:
            raise self.error(f"unknown struct `{expression.name}`", expression.span)
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
