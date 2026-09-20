from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields, is_dataclass, replace
import os
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
    ReferenceType,
    TupleType,
    Type,
    TypeVar,
    assignable,
    common_numeric_type,
    integer_literal_fits,
    is_box,
    is_float,
    is_integer,
    is_list,
    is_numeric,
    is_reference,
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
class ClassSymbol:
    name: str
    visibility: str
    type_params: tuple[str, ...]
    node: ast.ClassDef
    fields: dict[str, FieldSymbol]


@dataclass
class ClassInstance:
    symbol: ClassSymbol
    typ: NamedType
    mapping: dict[str, Type]
    fields: dict[str, FieldSymbol]
    methods: dict[str, "FunctionSymbol"]
    static_methods: dict[str, "FunctionSymbol"]
    initializer: "FunctionSymbol | None"
    deinitializer: "FunctionSymbol | None"


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
    node: ast.FunctionDef | ast.NativeFunctionDef | ast.ClassMethodDef | ast.ClassInitDef | ast.ClassDeinitDef
    type_mapping: dict[str, Type]
    native_symbol: str | None = None
    owner_class: NamedType | None = None
    exported: bool = False


@dataclass
class ClosureContext:
    floor: int
    captures: dict[str, tuple[str, Type, ir.IRExpr]]


@dataclass(frozen=True)
class BorrowPlace:
    root: str
    label: str
    projections: tuple[str, ...] = ()

    def overlaps(self, other: "BorrowPlace") -> bool:
        if self.root != other.root:
            return False
        common = min(len(self.projections), len(other.projections))
        return self.projections[:common] == other.projections[:common]


@dataclass
class BorrowLoan:
    place: BorrowPlace
    mutable: bool
    aliases: set[str]


class Lowerer:
    def __init__(self, program: ast.Program, source_name: str = "<source>", *, require_main: bool = True) -> None:
        self.program = program
        self.source_name = source_name
        self.structs: dict[str, StructSymbol] = {}
        self.classes: dict[str, ClassSymbol] = {}
        self.class_instances: dict[NamedType, ClassInstance] = {}
        self.functions: dict[str, FunctionSymbol] = {}
        self.methods: dict[tuple[str, str], FunctionSymbol] = {}
        self.scopes: list[dict[str, Type]] = []
        self.local_names: list[dict[str, str]] = []
        self.local_name_counts: list[dict[str, int]] = []
        self.hoisted_scopes: list[list[ir.IRHoistedLocal]] = []
        self.narrowed_scopes: list[dict[str, Type]] = []
        self.task_scopes: list[dict[str, tuple[bool, SourceSpan]]] = []
        self.dropped_scopes: list[set[str]] = []
        self.borrow_scopes: list[list[BorrowLoan]] = []
        self.reference_loan_scopes: list[dict[str, tuple[str, list[BorrowLoan]]]] = []
        self.protected_borrow_names: set[str] = set()
        self.expression_borrow_loans: dict[int, list[BorrowLoan]] = {}
        self.current_function: FunctionSymbol | None = None
        self.current_receiver: str | None = None
        # None marks a catch expression whose result is discarded as a statement.
        self.catch_result_types: list[Type | None] = []
        self.require_main = require_main
        self.generic_instances: dict[tuple[str, tuple[Type, ...]], FunctionSymbol] = {}
        self.pending_functions: list[FunctionSymbol] = []
        self.spawn_counter = 0
        self.features: set[str] = set()
        self.loop_depth = 0
        self.closure_counter = 0
        self.closure_contexts: list[ClosureContext] = []
        self.lowered_closures: list[ir.IRClosureDef] = []

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
        self.closure_counter = 0
        self.closure_contexts = []
        self.lowered_closures = []
        self.pending_functions = [
            symbol
            for symbol in all_functions
            if symbol.native_symbol is None and (not symbol.type_params or not self.require_main)
        ]
        for symbol in self.classes.values():
            if not symbol.type_params:
                self.instantiate_class(NamedType(symbol.name), symbol.node.span)
        for instance in self.class_instances.values():
            members = [*instance.methods.values(), *instance.static_methods.values()]
            if instance.initializer is not None:
                members.append(instance.initializer)
            if instance.deinitializer is not None:
                members.append(instance.deinitializer)
            for member in members:
                if member not in self.pending_functions:
                    self.pending_functions.append(member)
        lowered_functions: list[ir.IRFunction] = []
        index = 0
        while index < len(self.pending_functions):
            lowered_functions.append(self.lower_function(self.pending_functions[index]))
            index += 1

        lowered_classes = tuple(self.lower_class(instance) for instance in self.class_instances.values())

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
        (
            native_sources,
            native_links,
            native_headers,
            native_include_dirs,
            native_lib_dirs,
            native_cflags,
            native_ldflags,
        ) = self.collect_native_metadata()
        if "threads" in self.features:
            native_links = native_links + (
                ir.IRNativeLink("linux", "pthread"),
                ir.IRNativeLink("macos", "pthread"),
            )
        return ir.IRProgram(
            structs=lowered_structs,
            classes=lowered_classes,
            closures=tuple(self.lowered_closures),
            functions=tuple(lowered_functions),
            extern_functions=extern_functions,
            native_sources=native_sources,
            native_links=native_links,
            native_headers=native_headers,
            native_include_dirs=native_include_dirs,
            native_lib_dirs=native_lib_dirs,
            native_cflags=native_cflags,
            native_ldflags=native_ldflags,
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
        native_struct_names: dict[str, str] = {}
        for definition in self.program.definitions:
            if isinstance(definition, ast.StructDef):
                if definition.name in self.structs or definition.name in self.classes:
                    raise self.error(f"duplicate type `{definition.name}`", definition.span)
                if definition.native_name is not None:
                    previous = native_struct_names.get(definition.native_name)
                    if previous is not None and previous != definition.name:
                        raise self.error(
                            f"native C struct `{definition.native_name}` is declared by more than one c_module template; use distinct C ABI struct names",
                            definition.span,
                        )
                    native_struct_names[definition.native_name] = definition.name
                self.structs[definition.name] = StructSymbol(
                    definition.name,
                    definition.visibility,
                    definition.type_params,
                    definition,
                    {},
                )
            elif isinstance(definition, ast.ClassDef):
                if definition.name in self.structs or definition.name in self.classes:
                    raise self.error(f"duplicate type `{definition.name}`", definition.span)
                self.classes[definition.name] = ClassSymbol(
                    definition.name,
                    definition.visibility,
                    definition.type_params,
                    definition,
                    {},
                )

    def collect_struct_fields(self) -> None:
        known = set(self.structs) | set(self.classes)
        for symbol in (*self.structs.values(), *self.classes.values()):
            type_vars = set(symbol.type_params)
            for field in symbol.node.fields:
                if field.name in STRUCT_METADATA_FIELDS:
                    raise self.error(f"`{field.name}` is reserved for struct metadata", field.span)
                if field.name in symbol.fields:
                    raise self.error(f"duplicate field `{field.name}` in `{symbol.name}`", field.span)
                typ = resolve_type_node(field.type_node, known, type_vars, self.source_name)
                self.validate_function_types(typ, field.span)
                if isinstance(typ, ReferenceType):
                    owner = "struct" if isinstance(symbol, StructSymbol) else "class"
                    raise self.error(f"references cannot be stored in {owner} fields", field.span)
                if is_task(typ):
                    raise self.error("Task<T> is linear and cannot be stored in a struct field", field.span)
                symbol.fields[field.name] = FieldSymbol(field.name, typ, field.visibility, field)
        for symbol in (*self.structs.values(), *self.classes.values()):
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
                        + "; use List<T>, Box<T>, or an ARC class for indirection",
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
            if typ.name in self.classes:
                return ()
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
            if typ.name in self.classes or is_list(typ) or typ.name in {"Box", "Ref", "Raw"} or typ.name.startswith("ptr."):
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
        known = set(self.structs) | set(self.classes)
        native_signatures: dict[str, tuple[tuple[Type, ...], Type]] = {}
        for definition in self.program.definitions:
            if not isinstance(definition, (ast.FunctionDef, ast.NativeFunctionDef)):
                continue
            type_params = definition.type_params if isinstance(definition, ast.FunctionDef) else ()
            exported = isinstance(definition, ast.FunctionDef) and definition.exported
            if exported and type_params:
                raise self.error("export functions cannot be generic", definition.span)
            type_vars = set(type_params)
            params: list[tuple[str, Type, bool]] = []
            seen: set[str] = set()
            for param in definition.params:
                if param.name in seen:
                    raise self.error(f"duplicate parameter `{param.name}`", param.span)
                seen.add(param.name)
                resolved_param = resolve_type_node(param.type_node, known, type_vars, self.source_name)
                params.append((param.name, self.ensure_class_type(resolved_param, param.span), param.mutable))
                self.validate_box_position(params[-1][1], param.span, "parameter")
                if is_task(params[-1][1]):
                    raise self.error("Task<T> cannot be passed as a function parameter", param.span)
            return_type = self.ensure_class_type(
                resolve_type_node(definition.return_type, known, type_vars, self.source_name),
                definition.return_type.span,
            )
            if isinstance(return_type, ReferenceType):
                raise self.error("references cannot be returned from functions", definition.return_type.span)
            self.validate_box_position(return_type, definition.return_type.span, "return type")
            if is_task(return_type):
                raise self.error("Task<T> cannot be returned; await it in the creating scope", definition.return_type.span)
            throws = None
            if definition.throws is not None:
                throws = resolve_type_node(definition.throws, known, type_vars, self.source_name)
                if throws != ERROR:
                    raise self.error("ZyenLang 0.3 error effects must be written `throws Error`", definition.throws.span)
            if exported and throws is not None:
                raise self.error("export functions cannot throw; expose a non-throwing C ABI facade", definition.span)

            export_types = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "f32", "f64", "bool", "str"}
            if exported:
                invalid_param = next(
                    (
                        typ
                        for _, typ, _ in params
                        if not isinstance(typ, PrimitiveType) or typ.name not in export_types
                    ),
                    None,
                )
                if invalid_param is not None:
                    raise self.error(
                        f"export parameter type `{invalid_param.display()}` is not supported by ABI v2",
                        definition.span,
                    )
                if not (
                    return_type == VOID
                    or (isinstance(return_type, PrimitiveType) and return_type.name in export_types)
                ):
                    raise self.error(
                        f"export return type `{return_type.display()}` is not supported by ABI v2",
                        definition.span,
                    )

            receiver = None
            receiver_name = None
            if isinstance(definition, ast.FunctionDef) and definition.receiver is not None:
                receiver_type = resolve_type_node(definition.receiver.type_node, known, type_vars, self.source_name)
                if not isinstance(receiver_type, NamedType) or receiver_type.name not in self.structs:
                    raise self.error("method receiver must be a struct type", definition.receiver.span)
                if receiver_type.args:
                    raise self.error("receiver functions were removed in ZyenLang 0.3; use a class method or module function", definition.receiver.span)
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
                if any(
                    not self.is_native_abi_type(typ, allow_void=False)
                    for typ in native_types
                ):
                    raise self.error(
                        "native parameters support scalar, str, fn callback, and c_module ZLC_STRUCT ABI types",
                        definition.span,
                    )
                if not self.is_native_abi_type(return_type, allow_void=True):
                    raise self.error(
                        "native return values support scalar, str, void, fn callback, and c_module ZLC_STRUCT ABI types",
                        definition.span,
                    )
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
                safe_name
                if exported and "::" not in definition.name
                else f"zy2_method_{safe_receiver}_{safe_name}"
                if receiver_name
                else f"zy2_fn_{safe_name}"
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
                None,
                exported,
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

    def is_native_abi_type(self, typ: Type, *, allow_void: bool, visiting: set[str] | None = None) -> bool:
        if isinstance(typ, PrimitiveType):
            return typ != ERROR and (allow_void or typ != VOID)
        if isinstance(typ, FunctionType):
            return all(self.is_native_abi_type(item, allow_void=False) for item in typ.params) and self.is_native_abi_type(
                typ.return_type,
                allow_void=True,
            )
        if isinstance(typ, NamedType) and typ.name in self.structs:
            symbol = self.structs[typ.name]
            if symbol.node.native_name is None or typ.args:
                return False
            visiting = set() if visiting is None else set(visiting)
            if typ.name in visiting:
                return False
            visiting.add(typ.name)
            return all(
                self.is_native_abi_type(field.typ, allow_void=False, visiting=visiting)
                for field in symbol.fields.values()
            )
        return False

    def collect_native_metadata(
        self,
    ) -> tuple[
        tuple[str, ...],
        tuple[ir.IRNativeLink, ...],
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        sources: list[str] = []
        links: list[ir.IRNativeLink] = []
        headers: list[str] = []
        include_dirs: list[str] = []
        lib_dirs: list[str] = []
        cflags: list[str] = []
        ldflags: list[str] = []
        seen_sources: set[str] = set()
        seen_links: set[tuple[str, str]] = set()
        seen_values: dict[str, set[str]] = {
            "headers": set(),
            "include_dirs": set(),
            "lib_dirs": set(),
            "cflags": set(),
            "ldflags": set(),
        }

        def append_unique(target: list[str], key: str, values: tuple[str, ...]) -> None:
            for value in values:
                identity = value if key in {"cflags", "ldflags"} else os.path.normcase(str(Path(value).resolve()))
                if identity not in seen_values[key]:
                    seen_values[key].add(identity)
                    target.append(value)

        for definition in self.program.definitions:
            if isinstance(definition, ast.NativeSourceDef):
                raw = Path(definition.path)
                if raw.is_absolute():
                    raise self.error("native source paths must be relative to their .zy module", definition.span)
                owner = Path(definition.span.source_name)
                if definition.span.source_name.startswith("<"):
                    raise self.error("native sources require file-based compilation", definition.span)
                resolved = (owner.parent / raw).resolve()
                allowed_root = owner.parent.resolve()
                std_root = (Path(__file__).resolve().parent / "std").resolve()
                if owner.resolve() == std_root or std_root in owner.resolve().parents:
                    allowed_root = std_root
                else:
                    for candidate in (owner.parent, *owner.parents):
                        if (candidate / "zyproject.toml").is_file():
                            allowed_root = candidate.resolve()
                            break
                if resolved != allowed_root and allowed_root not in resolved.parents:
                    raise self.error("native source path escapes its package root", definition.span)
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
            elif isinstance(definition, ast.NativeBuildDef):
                for source in definition.sources:
                    resolved = Path(source).resolve()
                    if resolved.suffix.lower() != ".c" or not resolved.is_file():
                        raise self.error(f"native source not found: {resolved}", definition.span)
                    value = str(resolved)
                    if value not in seen_sources:
                        seen_sources.add(value)
                        sources.append(value)
                for header in definition.headers:
                    resolved = Path(header).resolve()
                    if not resolved.is_file():
                        raise self.error(f"native header not found: {resolved}", definition.span)
                for directory in (*definition.include_dirs, *definition.lib_dirs):
                    if not Path(directory).resolve().is_dir():
                        raise self.error(f"native build directory not found: {directory}", definition.span)
                append_unique(headers, "headers", definition.headers)
                append_unique(include_dirs, "include_dirs", definition.include_dirs)
                append_unique(lib_dirs, "lib_dirs", definition.lib_dirs)
                append_unique(cflags, "cflags", definition.cflags)
                append_unique(ldflags, "ldflags", definition.ldflags)
                for library in definition.libraries:
                    if len(library) > 128 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.+-]*", library):
                        raise self.error(f"invalid native library name `{library}`", definition.span)
                    key = ("all", library)
                    if key not in seen_links:
                        seen_links.add(key)
                        links.append(ir.IRNativeLink(*key))
        return (
            tuple(sources),
            tuple(links),
            tuple(headers),
            tuple(include_dirs),
            tuple(lib_dirs),
            tuple(cflags),
            tuple(ldflags),
        )

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
        return ir.IRStructDef(symbol.name, tuple(fields), symbol.visibility, symbol.node.native_name)

    def ensure_class_type(self, typ: Type, span: SourceSpan) -> Type:
        if isinstance(typ, NamedType):
            args = tuple(self.ensure_class_type(arg, span) for arg in typ.args)
            typ = NamedType(typ.name, args)
            symbol = self.classes.get(typ.name)
            if symbol is not None:
                if len(args) != len(symbol.type_params):
                    raise self.error(
                        f"class `{symbol.name}` requires {len(symbol.type_params)} type argument(s), got {len(args)}",
                        span,
                    )
                if not any(self.contains_type_var(arg) for arg in args):
                    self.instantiate_class(typ, span)
            return typ
        if isinstance(typ, TupleType):
            return TupleType(tuple(self.ensure_class_type(item, span) for item in typ.items))
        if isinstance(typ, OptionalType):
            return OptionalType(self.ensure_class_type(typ.inner, span))
        if isinstance(typ, FunctionType):
            return FunctionType(
                tuple(self.ensure_class_type(item, span) for item in typ.params),
                self.ensure_class_type(typ.return_type, span),
            )
        if isinstance(typ, ReferenceType):
            return ReferenceType(self.ensure_class_type(typ.inner, span), typ.mutable)
        return typ

    def instantiate_class(self, typ: NamedType, span: SourceSpan) -> ClassInstance:
        existing = self.class_instances.get(typ)
        if existing is not None:
            return existing
        symbol = self.classes.get(typ.name)
        if symbol is None:
            raise self.error(f"unknown class `{typ.name}`", span)
        if len(typ.args) != len(symbol.type_params):
            raise self.error(
                f"class `{symbol.name}` requires {len(symbol.type_params)} type argument(s), got {len(typ.args)}",
                span,
            )
        if any(self.contains_type_var(arg) for arg in typ.args):
            raise self.error("generic class instances require concrete type arguments", span)
        mapping = dict(zip(symbol.type_params, typ.args))
        fields: dict[str, FieldSymbol] = {}
        for name, field in symbol.fields.items():
            concrete = self.ensure_class_type(substitute(field.typ, mapping), field.node.span)
            fields[name] = FieldSymbol(name, concrete, field.visibility, field.node)

        instance = ClassInstance(symbol, typ, mapping, fields, {}, {}, None, None)
        self.class_instances[typ] = instance
        suffix = self.mangle_type(typ)

        def resolve_member_type(node: ast.TypeNode) -> Type:
            raw = resolve_type_node(
                node,
                set(self.structs) | set(self.classes),
                set(symbol.type_params),
                self.source_name,
            )
            return self.ensure_class_type(substitute(raw, mapping), node.span)

        def make_params(nodes: tuple[ast.Param, ...]) -> tuple[tuple[str, Type, bool], ...]:
            params: list[tuple[str, Type, bool]] = []
            for node in nodes:
                value = resolve_member_type(node.type_node)
                if is_task(value):
                    raise self.error("Task<T> cannot be passed as a class method parameter", node.span)
                params.append((node.name, value, node.mutable))
            return tuple(params)

        for method in symbol.node.methods:
            if method.name in instance.methods or method.name in instance.static_methods:
                raise self.error(f"duplicate class method `{symbol.name}.{method.name}`", method.span)
            params = make_params(method.params)
            return_type = resolve_member_type(method.return_type)
            if isinstance(return_type, ReferenceType):
                raise self.error("class methods cannot return references", method.return_type.span)
            throws = resolve_member_type(method.throws) if method.throws else None
            if throws is not None and throws != ERROR:
                raise self.error("class methods may only declare `throws Error`", method.span)
            receiver = None if method.static else ("this", typ, method.mutable)
            c_name = f"zy3_class_{suffix}_{self.mangle_type(PrimitiveType(method.name))}"
            member = FunctionSymbol(
                f"{symbol.name}::{method.name}",
                c_name,
                method.visibility,
                params,
                return_type,
                throws,
                receiver,
                (),
                method,
                mapping,
                None,
                typ,
            )
            (instance.static_methods if method.static else instance.methods)[method.name] = member
            self.pending_functions.append(member)

        if symbol.node.initializer is not None:
            node = symbol.node.initializer
            if node.throws is not None:
                raise self.error("throwing class initializers are not enabled in v0.3.0", node.span)
            member = FunctionSymbol(
                f"{symbol.name}::init",
                f"zy3_class_{suffix}_init",
                node.visibility,
                make_params(node.params),
                VOID,
                None,
                ("this", typ, True),
                (),
                node,
                mapping,
                None,
                typ,
            )
            instance.initializer = member
            self.pending_functions.append(member)

        if symbol.node.deinitializer is not None:
            node = symbol.node.deinitializer
            member = FunctionSymbol(
                f"{symbol.name}::deinit",
                f"zy3_class_{suffix}_deinit",
                "private",
                (),
                VOID,
                None,
                ("this", typ, True),
                (),
                node,
                mapping,
                None,
                typ,
            )
            instance.deinitializer = member
            self.pending_functions.append(member)
        return instance

    def lower_class(self, instance: ClassInstance) -> ir.IRClassDef:
        fields: list[ir.IRFieldDef] = []
        for field in instance.fields.values():
            default = None
            if field.node.default is not None:
                self.push_scope()
                try:
                    default = self.lower_expr(field.node.default, field.typ)
                finally:
                    self.pop_scope()
            fields.append(ir.IRFieldDef(field.name, field.typ, field.visibility, default))
        return ir.IRClassDef(
            instance.typ,
            tuple(fields),
            instance.symbol.visibility,
            instance.deinitializer.c_name if instance.deinitializer else None,
        )

    def lower_function(self, symbol: FunctionSymbol) -> ir.IRFunction:
        self.current_function = symbol
        self.current_receiver = symbol.owner_class.name if symbol.owner_class is not None else None
        self.push_scope()
        params: list[ir.IRParam] = []
        try:
            for name, typ, _ in symbol.params:
                param_node = next(item for item in symbol.node.params if item.name == name)
                if param_node.default is not None:
                    self.lower_expr(param_node.default, typ)
            if symbol.receiver is not None:
                name, typ, mutable = symbol.receiver
                local_name = self.define_local(
                    name,
                    typ,
                    symbol.node.span,
                )
                params.append(ir.IRParam(local_name, typ, mutable))
            for name, typ, mutable in symbol.params:
                param_node = next(item for item in symbol.node.params if item.name == name)
                local_name = self.define_local(name, typ, param_node.span)
                params.append(ir.IRParam(local_name, typ, mutable))
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
            symbol.receiver[2] if symbol.receiver else False,
            symbol.throws,
            symbol.exported,
        )

    def push_scope(self) -> None:
        self.scopes.append({})
        self.local_names.append({})
        self.local_name_counts.append({})
        self.hoisted_scopes.append([])
        self.narrowed_scopes.append({})
        self.task_scopes.append({})
        self.dropped_scopes.append(set())
        self.borrow_scopes.append([])
        self.reference_loan_scopes.append({})

    def pop_scope(self) -> None:
        self.scopes.pop()
        self.local_names.pop()
        self.local_name_counts.pop()
        self.hoisted_scopes.pop()
        self.narrowed_scopes.pop()
        self.task_scopes.pop()
        self.dropped_scopes.pop()
        loans = self.borrow_scopes.pop()
        aliases = self.reference_loan_scopes.pop()
        for _, (local_name, referenced_loans) in aliases.items():
            for loan in referenced_loans:
                loan.aliases.discard(local_name)
        for loan in loans:
            loan.aliases.clear()

    def active_borrows(self, place: BorrowPlace | str) -> tuple[int, bool]:
        if isinstance(place, str):
            scope_index = self.find_local_scope_index(place)
            if scope_index is None:
                return 0, False
            local_name = self.local_names[scope_index][place]
            place = BorrowPlace(local_name, place)
        readonly = 0
        mutable = False
        for scope in self.borrow_scopes:
            for loan in scope:
                if not loan.place.overlaps(place):
                    continue
                if loan.mutable:
                    mutable = True
                else:
                    readonly += 1
        return readonly, mutable

    def register_borrow(self, place: BorrowPlace, mutable: bool, span: SourceSpan) -> BorrowLoan:
        readonly, active_mutable = self.active_borrows(place)
        if mutable:
            if readonly or active_mutable:
                raise self.error(
                    f"cannot mutably borrow `{place.label}` while another reference exists",
                    span,
                )
        elif active_mutable:
            raise self.error(
                f"cannot borrow `{place.label}` while a mutable reference exists",
                span,
            )
        loan = BorrowLoan(place, mutable, set())
        self.borrow_scopes[-1].append(loan)
        return loan

    def bind_reference_loans(self, name: str, local_name: str, loans: list[BorrowLoan]) -> None:
        self.reference_loan_scopes[-1][name] = (local_name, loans)
        for loan in loans:
            loan.aliases.add(local_name)

    def lookup_reference_loans(self, name: str) -> list[BorrowLoan]:
        for scope in reversed(self.reference_loan_scopes):
            if name in scope:
                return scope[name][1]
        return []

    def expire_borrows(self, live_names: set[str]) -> None:
        source_names = live_names | self.protected_borrow_names
        keep_names: set[str] = set()
        for scope in self.reference_loan_scopes:
            for source_name, (local_name, _) in scope.items():
                if source_name in source_names:
                    keep_names.add(local_name)
        for scope in self.borrow_scopes:
            scope[:] = [loan for loan in scope if loan.aliases & keep_names]

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

    def allocate_local_name(self, scope_index: int, name: str) -> str:
        counts = self.local_name_counts[scope_index]
        generation = counts.get(name, 0) + 1
        counts[name] = generation
        return name if generation == 1 else f"__zy_{name}_{generation}"

    def define_local(self, name: str, typ: Type, span: SourceSpan) -> str:
        if name in PROCESS_SPECIAL_VALUES:
            raise self.error(f"`{name}` is a reserved process value and cannot be shadowed", span)
        if name in COMPILER_SPECIAL_VALUES:
            raise self.error(f"`{name}` is a reserved compiler value and cannot be shadowed", span)
        scope = self.scopes[-1]
        if name in scope:
            raise self.error(f"duplicate local `{name}`", span)
        scope[name] = typ
        local_name = self.allocate_local_name(len(self.scopes) - 1, name)
        self.local_names[-1][name] = local_name
        return local_name

    def lookup_local_name(self, name: str, span: SourceSpan) -> str:
        for index in range(len(self.local_names) - 1, -1, -1):
            names = self.local_names[index]
            if name in names:
                if name in self.dropped_scopes[index]:
                    raise self.error(f"local `{name}` was dropped and has not been reinitialized", span)
                return names[name]
        raise self.error(f"unknown name `{name}`", span)

    def lower_local_access(self, name: str, span: SourceSpan) -> ir.IRExpr:
        scope_index = self.find_local_scope_index(name)
        if scope_index is None:
            raise self.error(f"unknown name `{name}`", span)
        if name in self.dropped_scopes[scope_index]:
            raise self.error(f"local `{name}` was dropped and has not been reinitialized", span)
        typ = self.scopes[scope_index][name]
        return self.lower_local_access_in_context(
            name,
            scope_index,
            typ,
            span,
            len(self.closure_contexts) - 1,
        )

    def lower_local_access_in_context(
        self,
        name: str,
        scope_index: int,
        typ: Type,
        span: SourceSpan,
        context_index: int,
    ) -> ir.IRExpr:
        if context_index < 0 or scope_index >= self.closure_contexts[context_index].floor:
            return ir.IRName(typ, span, self.local_names[scope_index][name])
        if isinstance(typ, ReferenceType):
            raise self.error("references cannot be captured by closures", span)
        if is_task(typ):
            raise self.error("Task<T> cannot be captured by closures", span)
        context = self.closure_contexts[context_index]
        captured = context.captures.get(name)
        if captured is None:
            source = self.lower_local_access_in_context(
                name,
                scope_index,
                typ,
                span,
                context_index - 1,
            )
            field_name = f"capture_{len(context.captures)}_{name}"
            captured = (field_name, typ, source)
            context.captures[name] = captured
        return ir.IRCapture(typ, span, captured[0])

    def is_captured_local(self, name: str) -> bool:
        if not self.closure_contexts:
            return False
        scope_index = self.find_local_scope_index(name)
        return scope_index is not None and scope_index < self.closure_contexts[-1].floor

    def drop_local(self, name: str, span: SourceSpan) -> ir.IRDrop:
        if name not in self.scopes[-1]:
            if self.find_local(name) is not None:
                raise self.error(f"DROP__ can only consume `{name}` in its declaring block", span)
            raise self.error(f"unknown name `{name}`", span)
        typ = self.scopes[-1][name]
        if is_task(typ):
            raise self.error("DROP__ cannot discard Task<T>; await it exactly once", span)
        if is_reference(typ):
            raise self.error("DROP__ cannot consume a borrowed reference", span)
        if not self.type_requires_management(typ):
            raise self.error(f"`{name}` has no managed resource to drop", span)
        if name in self.dropped_scopes[-1]:
            raise self.error(f"local `{name}` was already dropped", span)
        readonly, mutable = self.active_borrows(name)
        if readonly or mutable:
            raise self.error(f"cannot drop `{name}` while it is borrowed", span)
        local_name = self.local_names[-1][name]
        self.dropped_scopes[-1].add(name)
        self.narrowed_scopes[-1].pop(name, None)
        self.task_scopes[-1].pop(name, None)
        return ir.IRDrop(span, local_name, typ)

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

    def find_local_scope_index(self, name: str) -> int | None:
        for index in range(len(self.scopes) - 1, -1, -1):
            if name in self.scopes[index]:
                return index
        return None

    def find_narrowed_local(self, name: str) -> Type | None:
        declaration = self.find_local_scope_index(name)
        if declaration is None:
            return None
        for index in range(len(self.narrowed_scopes) - 1, declaration - 1, -1):
            narrowed = self.narrowed_scopes[index].get(name)
            if narrowed is not None:
                return narrowed
        return None

    def invalidate_narrowing(self, name: str) -> None:
        declaration = self.find_local_scope_index(name)
        if declaration is None:
            return
        for index in range(declaration, len(self.narrowed_scopes)):
            self.narrowed_scopes[index].pop(name, None)

    def lower_narrowed_block(self, block: ast.Block, name: str, inner: Type) -> ir.IRBlock:
        self.push_scope()
        try:
            self.narrowed_scopes[-1][name] = inner
            lowered = self.lower_block(block, push_scope=False)
            self.validate_current_task_scope()
            return lowered
        finally:
            self.pop_scope()

    @classmethod
    def types_may_match(cls, left: Type, right: Type) -> bool:
        if isinstance(left, TypeVar) or isinstance(right, TypeVar):
            return True
        if isinstance(left, NamedType) and isinstance(right, NamedType):
            return (
                left.name == right.name
                and len(left.args) == len(right.args)
                and all(cls.types_may_match(a, b) for a, b in zip(left.args, right.args))
            )
        if isinstance(left, TupleType) and isinstance(right, TupleType):
            return len(left.items) == len(right.items) and all(
                cls.types_may_match(a, b) for a, b in zip(left.items, right.items)
            )
        if isinstance(left, OptionalType) and isinstance(right, OptionalType):
            return cls.types_may_match(left.inner, right.inner)
        if isinstance(left, FunctionType) and isinstance(right, FunctionType):
            return (
                len(left.params) == len(right.params)
                and all(cls.types_may_match(a, b) for a, b in zip(left.params, right.params))
                and cls.types_may_match(left.return_type, right.return_type)
            )
        return left == right

    def lower_typeof(
        self,
        expression: ast.TypeOfExpr,
        expected: Type | None = None,
    ) -> tuple[ir.IRExpr, bool | None, Type]:
        value = self.lower_expr(expression.value)
        target_type = self.resolve_optional_annotation(expression.target_type)
        assert target_type is not None
        unknown = self.contains_type_var(value.typ) or self.contains_type_var(target_type)
        result: bool | None
        if unknown and self.types_may_match(value.typ, target_type):
            result = None
        else:
            result = value.typ == target_type
        test: ir.IRExpr
        if result is None:
            test = ir.IRStaticTypeTest(BOOL, expression.span)
        else:
            test = ir.IRBool(BOOL, expression.span, result)
        lowered = self.coerce(test, expected, expression.span)
        return lowered, result, target_type

    def optional_null_narrowing(self, expression: ast.Expr) -> tuple[str, Type, bool] | None:
        if not isinstance(expression, ast.BinaryExpr) or expression.operator not in {"==", "!="}:
            return None
        candidate: ast.Expr | None = None
        if isinstance(expression.left, ast.NullExpr):
            candidate = expression.right
        elif isinstance(expression.right, ast.NullExpr):
            candidate = expression.left
        if not isinstance(candidate, ast.NameExpr):
            return None
        typ = self.find_local(candidate.name)
        if not isinstance(typ, OptionalType):
            return None
        return candidate.name, typ.inner, expression.operator == "!="

    def lower_block(self, block: ast.Block, *, push_scope: bool = True) -> ir.IRBlock:
        if push_scope:
            self.push_scope()
        previous_protected = self.protected_borrow_names
        try:
            suffix_uses: list[set[str]] = [set() for _ in range(len(block.statements) + 1)]
            for index in range(len(block.statements) - 1, -1, -1):
                suffix_uses[index] = suffix_uses[index + 1] | self.name_uses(block.statements[index])
            lowered: list[ir.IRStmt] = []
            outer_live = set(previous_protected)
            for index, statement in enumerate(block.statements):
                # A control-flow statement is one conservative borrow region. This
                # keeps branch/loop lowering sound while still ending ordinary
                # loans immediately after their final statement-level use.
                self.protected_borrow_names = outer_live | suffix_uses[index]
                lowered.append(self.lower_stmt(statement))
                self.protected_borrow_names = outer_live
                self.expire_borrows(outer_live | suffix_uses[index + 1])
            statements = tuple(lowered)
            if push_scope:
                self.validate_current_task_scope()
            return ir.IRBlock(statements, block.span, tuple(self.hoisted_scopes[-1]))
        finally:
            self.protected_borrow_names = previous_protected
            if push_scope:
                self.pop_scope()

    @classmethod
    def name_uses(cls, value: object) -> set[str]:
        if isinstance(value, ast.NameExpr):
            return {value.name}
        if isinstance(value, tuple):
            result: set[str] = set()
            for item in value:
                result.update(cls.name_uses(item))
            return result
        if is_dataclass(value) and value.__class__.__module__ == ast.__name__:
            result: set[str] = set()
            for item in dataclass_fields(value):
                if item.name != "span":
                    result.update(cls.name_uses(getattr(value, item.name)))
            return result
        return set()

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
            if expected is None:
                value = self.lower_discarded_value_sequence(statement.values, statement.span)
            else:
                value = self.lower_value_sequence(statement.values, expected, statement.span)
            return ir.IRRecover(statement.span, value)
        if isinstance(statement, ast.IfStmt):
            typeof_result: bool | None = None
            typeof_target: Type | None = None
            if isinstance(statement.condition, ast.TypeOfExpr):
                condition, typeof_result, typeof_target = self.lower_typeof(statement.condition, BOOL)
            else:
                condition = self.lower_expr(statement.condition, BOOL)
            if isinstance(statement.condition, ast.TypeOfExpr) and typeof_result is True:
                then_block = self.lower_block(statement.then_block)
                return ir.IRIf(statement.span, condition, then_block, None)
            if isinstance(statement.condition, ast.TypeOfExpr) and typeof_result is False:
                then_block = ir.IRBlock((), statement.then_block.span)
                else_block = self.lower_block(statement.else_block) if statement.else_block else None
                return ir.IRIf(statement.span, condition, then_block, else_block)
            if (
                isinstance(statement.condition, ast.TypeOfExpr)
                and isinstance(statement.condition.value, ast.NameExpr)
                and typeof_target is not None
                and not self.contains_type_var(typeof_target)
            ):
                then_block = self.lower_narrowed_block(
                    statement.then_block,
                    statement.condition.value.name,
                    typeof_target,
                )
                else_block = self.lower_block(statement.else_block) if statement.else_block else None
                return ir.IRIf(statement.span, condition, then_block, else_block)
            narrowing = self.optional_null_narrowing(statement.condition)
            if narrowing is None:
                then_block = self.lower_block(statement.then_block)
                else_block = self.lower_block(statement.else_block) if statement.else_block else None
            else:
                name, inner, non_null_when_true = narrowing
                then_block = (
                    self.lower_narrowed_block(statement.then_block, name, inner)
                    if non_null_when_true
                    else self.lower_block(statement.then_block)
                )
                else_block = None
                if statement.else_block is not None:
                    else_block = (
                        self.lower_block(statement.else_block)
                        if non_null_when_true
                        else self.lower_narrowed_block(statement.else_block, name, inner)
                    )
            return ir.IRIf(statement.span, condition, then_block, else_block)
        if isinstance(statement, ast.IfLetStmt):
            value = self.lower_expr(statement.value)
            if not isinstance(value.typ, OptionalType):
                raise self.error("if let requires a `T | null` value", statement.value.span)
            self.push_scope()
            try:
                local_name = self.define_local(statement.binding, value.typ.inner, statement.span)
                then_block = self.lower_block(statement.then_block, push_scope=False)
            finally:
                self.pop_scope()
            else_block = self.lower_block(statement.else_block) if statement.else_block else None
            return ir.IRIfLet(statement.span, local_name, value.typ.inner, value, then_block, else_block)
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
        if isinstance(statement, ast.DropStmt):
            return self.drop_local(statement.name, statement.span)
        if isinstance(statement, ast.DeferStmt):
            call = self.lower_call(statement.call)
            if isinstance(call, ir.IRCall) and call.throws is not None:
                raise self.error("defer calls cannot throw", statement.call.span)
            if not isinstance(call, (ir.IRCall, ir.IRIndirectCall)):
                raise self.error("defer requires a function or class method call", statement.call.span)
            if call.typ != VOID:
                raise self.error("defer call must return void", statement.call.span)
            if isinstance(call, ir.IRCall) and call.target.startswith("__zy2_"):
                raise self.error("compiler intrinsics cannot be deferred", statement.call.span)
            return ir.IRDefer(statement.span, call)
        if isinstance(statement, ast.ExprStmt):
            return ir.IRExprStmt(
                statement.span,
                self.lower_expr(statement.value, discard_result=True),
            )
        raise self.error("unsupported statement", statement.span)

    def lower_assignment(self, statement: ast.AssignStmt) -> ir.IRAssign:
        assigned_name: str | None = None
        if isinstance(statement.target, ast.NameExpr):
            if statement.target.name in PROCESS_SPECIAL_VALUES:
                raise self.error("process special values cannot be assigned", statement.target.span)
            target_type = self.lookup_local(statement.target.name, statement.target.span)
            if self.is_captured_local(statement.target.name):
                raise self.error("closure captures are readonly snapshots", statement.target.span)
            assigned_name = statement.target.name
            if isinstance(target_type, ReferenceType):
                raise self.error("reference bindings cannot be reassigned; borrow again after their last use", statement.target.span)
            readonly, mutable = self.active_borrows(statement.target.name)
            if readonly or mutable:
                raise self.error(f"cannot assign `{statement.target.name}` while it is borrowed", statement.target.span)
            if is_task(target_type):
                raise self.error("Task<T> variables cannot be reassigned", statement.target.span)
            scope_index = self.find_local_scope_index(statement.target.name)
            assert scope_index is not None
            target = ir.IRName(target_type, statement.target.span, self.local_names[scope_index][statement.target.name])
        else:
            root = statement.target
            while isinstance(root, ast.FieldExpr):
                root = root.receiver
            if not isinstance(root, ast.NameExpr):
                raise self.error("field assignment must be rooted in a local variable", statement.target.span)
            if self.is_captured_local(root.name):
                raise self.error("closure captures are readonly snapshots", statement.target.span)
            if root.name == "this" and self.current_function and self.current_function.owner_class is not None:
                if self.current_function.receiver is not None and not self.current_function.receiver[2]:
                    raise self.error("readonly class method cannot modify `this`", statement.target.span)
            readonly, mutable = self.active_borrows(self.borrow_place(statement.target))
            if readonly or mutable:
                raise self.error(
                    f"cannot assign `{self.borrow_place(statement.target).label}` while it is borrowed",
                    statement.target.span,
                )
            target = self.lower_field(statement.target)
            if is_task(target.typ):
                raise self.error("Task<T> fields are not assignable", statement.target.span)
        value = self.lower_expr(statement.value, target.typ)
        if assigned_name is not None:
            scope_index = self.find_local_scope_index(assigned_name)
            assert scope_index is not None
            self.dropped_scopes[scope_index].discard(assigned_name)
        if assigned_name is not None and not isinstance(value, ir.IROptionalSome):
            self.invalidate_narrowing(assigned_name)
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
            reference_loans = list(self.expression_borrow_loans.get(id(value), ()))
            if isinstance(typ, ReferenceType) and isinstance(statement.value, ast.NameExpr):
                source_type = self.lookup_local(statement.value.name, statement.value.span)
                if isinstance(source_type, ReferenceType) and source_type.mutable:
                    raise self.error(
                        "mutable references cannot be copied; pass them directly or create a new borrow after the last use",
                        statement.value.span,
                    )
                reference_loans = list(self.lookup_reference_loans(statement.value.name))
            local_name = self.define_local(binding.name, typ, binding.span)
            if isinstance(typ, ReferenceType):
                self.bind_reference_loans(binding.name, local_name, reference_loans)
            if is_task(typ):
                self.task_scopes[-1][binding.name] = (False, binding.span)
            return ir.IRLet(statement.span, (local_name,), (typ,), value)

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
        local_names: list[str] = []
        for binding, annotation, actual in zip(statement.bindings, annotations, value.typ.items):
            typ = annotation or actual
            self.validate_box_position(typ, binding.span, "destructured local type")
            if not assignable(typ, actual):
                raise self.error(
                    f"cannot bind `{binding.name}: {typ.display()}` from `{actual.display()}`",
                    binding.span,
                )
            local_names.append(self.define_local(binding.name, typ, binding.span))
            final_types.append(typ)
        return ir.IRLet(statement.span, tuple(local_names), tuple(final_types), value)

    def resolve_optional_annotation(self, node: ast.TypeNode | None) -> Type | None:
        if node is None:
            return None
        if self.current_function is None:
            resolved = resolve_type_node(node, set(self.structs) | set(self.classes), set(), self.source_name)
            return self.ensure_class_type(resolved, node.span)
        type_vars = set(self.current_function.type_params) | set(self.current_function.type_mapping)
        resolved = resolve_type_node(node, set(self.structs) | set(self.classes), type_vars, self.source_name)
        return self.ensure_class_type(substitute(resolved, self.current_function.type_mapping), node.span)

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

    def lower_discarded_value_sequence(
        self,
        values: tuple[ast.Expr, ...],
        span: SourceSpan,
    ) -> ir.IRExpr | None:
        if not values:
            return None
        if len(values) == 1:
            return self.lower_expr(values[0])
        raise self.error("a discarded catch accepts at most one recover value", span)

    def lower_expr(
        self,
        expression: ast.Expr,
        expected: Type | None = None,
        *,
        discard_result: bool = False,
    ) -> ir.IRExpr:
        if isinstance(expression, ast.IntExpr):
            literal_expected = expected.inner if isinstance(expected, OptionalType) else expected
            typ = literal_expected if literal_expected is not None and is_integer(literal_expected) else PrimitiveType("i32")
            if not integer_literal_fits(expression.value, typ):
                raise self.error(
                    f"integer literal {expression.value} does not fit `{typ.display()}`",
                    expression.span,
                )
            return self.coerce(ir.IRInt(typ, expression.span, expression.value), expected, expression.span)
        if isinstance(expression, ast.FloatExpr):
            literal_expected = expected.inner if isinstance(expected, OptionalType) else expected
            typ = (
                literal_expected
                if literal_expected is not None and is_float(literal_expected)
                else PrimitiveType("f64")
            )
            return self.coerce(ir.IRFloat(typ, expression.span, expression.value), expected, expression.span)
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
        if isinstance(expression, ast.ClosureExpr):
            return self.lower_closure(expression, expected)
        if isinstance(expression, ast.NameExpr):
            if expression.name in COMPILER_SPECIAL_VALUES:
                raise self.error(f"`{expression.name}` must be called with parentheses", expression.span)
            local_type = self.find_local(expression.name)
            if local_type is not None:
                if is_task(local_type):
                    raise self.error("Task<T> can only be consumed with `await task`", expression.span)
                local = self.lower_local_access(expression.name, expression.span)
                narrowed_type = self.find_narrowed_local(expression.name)
                if narrowed_type is not None:
                    if isinstance(local_type, OptionalType):
                        local = ir.IROptionalValue(narrowed_type, expression.span, local)
                    else:
                        local = replace(local, typ=narrowed_type)
                return self.coerce(local, expected, expression.span)
            symbol = self.functions.get(expression.name)
            if symbol is not None:
                return self.coerce(self.lower_function_ref(symbol, expression.span), expected, expression.span)
            raise self.error(f"unknown name `{expression.name}`", expression.span)
        if isinstance(expression, ast.PathExpr):
            name = "::".join(expression.parts)
            symbol = self.functions.get(name)
            if symbol is not None:
                return self.coerce(self.lower_function_ref(symbol, expression.span), expected, expression.span)
            raise self.error(f"unknown path `{name}`", expression.span)
        if isinstance(expression, ast.TypeApplyExpr):
            path = self.qualified_name(expression.callee)
            raise self.error(f"unknown generic class constructor `{path or '<expression>'}`", expression.span)
        if isinstance(expression, ast.AssociatedExpr):
            raise self.error("a class static function path must be called", expression.span)
        if isinstance(expression, ast.BorrowExpr):
            if isinstance(expression.value, ast.IndexExpr):
                return self.lower_list_element_borrow(expression, expected)
            place = self.borrow_place(expression.value)
            root_name = self.borrow_root_name(expression.value)
            if root_name is None:
                raise self.error("references require a stable local or local-rooted field", expression.value.span)
            if self.is_captured_local(root_name):
                raise self.error("references cannot borrow a value from an outer closure scope", expression.span)
            value = self.lower_borrow_place(expression.value)
            typ = value.typ
            if isinstance(typ, ReferenceType):
                raise self.error("references cannot point to references", expression.span)
            loan = self.register_borrow(place, expression.mutable, expression.span)
            reference_type = ReferenceType(typ, expression.mutable)
            result = self.coerce(
                ir.IRBorrow(reference_type, expression.span, value, expression.mutable),
                expected,
                expression.span,
            )
            self.expression_borrow_loans[id(result)] = [loan]
            return result
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
            if (
                isinstance(expression.receiver, ast.CallExpr)
                and isinstance(expression.receiver.callee, ast.NameExpr)
                and expression.receiver.callee.name == "STR_TO_LIST__"
            ):
                raise self.error(
                    "store `STR_TO_LIST__` in a local before indexing so the borrowed str cannot outlive its List",
                    expression.receiver.span,
                )
            receiver = self.lower_expr(expression.receiver)
            receiver = self.autoderef_receiver(receiver, expression.receiver, "index")
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
            self.catch_result_types.append(None if discard_result else value.typ)
            try:
                error_name = self.define_local(expression.error_name, ERROR, expression.span)
                handler = self.lower_block(expression.handler, push_scope=False)
            finally:
                self.catch_result_types.pop()
                self.pop_scope()
            if not self.block_catch_completes(handler):
                raise self.error("catch must end with recover, return, or stop on every path", expression.handler.span)
            result_type = VOID if discard_result else value.typ
            return self.coerce(
                ir.IRCatch(result_type, expression.span, value, error_name, handler),
                expected,
                expression.span,
            )
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
            task = ir.IRName(
                task_type,
                expression.task.span,
                self.lookup_local_name(expression.task.name, expression.task.span),
            )
            return self.coerce(ir.IRAwait(task_type.args[0], expression.span, task), expected, expression.span)
        if isinstance(expression, ast.TypeOfExpr):
            lowered, _, _ = self.lower_typeof(expression, expected)
            return lowered
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
                "stack": NamedType("List", (STR,)),
            }
            field_type = error_fields.get(expression.name)
            if field_type is not None:
                if expression.name == "stack":
                    return ir.IRErrorStack(field_type, expression.span, receiver)
                runtime_name = "source_file" if expression.name == "file" else expression.name
                return ir.IRField(field_type, expression.span, receiver, runtime_name)
        if not isinstance(receiver.typ, NamedType) or receiver.typ.name not in self.structs:
            if isinstance(receiver.typ, NamedType) and receiver.typ.name in self.classes:
                instance = self.instantiate_class(receiver.typ, expression.span)
                field = instance.fields.get(expression.name)
                if field is None:
                    raise self.error(f"class `{receiver.typ.display()}` has no field `{expression.name}`", expression.span)
                if field.visibility == "private" and self.current_receiver != receiver.typ.name:
                    raise self.error(f"field `{receiver.typ.display()}.{field.name}` is private", expression.span)
                return ir.IRField(field.typ, expression.span, receiver, field.name)
            raise self.error(f"`{receiver.typ.display()}` has no field `{expression.name}`", expression.span)
        if category := STRUCT_METADATA_FIELDS.get(expression.name):
            metadata_type = NamedType("List", (STR,))
            return ir.IRStructMetadata(metadata_type, expression.span, receiver, receiver.typ.name, category)
        symbol = self.structs[receiver.typ.name]
        field = symbol.fields.get(expression.name)
        if field is None:
            raise self.error(f"struct `{symbol.name}` has no field `{expression.name}`", expression.span)
        current_module = self.module_name(self.current_function.name if self.current_function else "")
        if field.visibility == "private" and current_module != self.module_name(symbol.name):
            raise self.error(f"field `{symbol.name}.{field.name}` is private", expression.span)
        return ir.IRField(field.typ, expression.span, receiver, field.name)

    def lower_call(self, expression: ast.CallExpr, expected: Type | None = None) -> ir.IRExpr:
        if isinstance(expression.callee, ast.AssociatedExpr):
            receiver = expression.callee.receiver
            if not isinstance(receiver, ast.TypeApplyExpr):
                raise self.error("static function receiver must be a class type", expression.callee.span)
            class_name = self.qualified_name(receiver.callee)
            if class_name is None or class_name not in self.classes:
                raise self.error(f"unknown generic class `{class_name or '<expression>'}`", expression.span)
            type_args = tuple(self.resolve_optional_annotation(node) for node in receiver.type_args)
            if any(item is None for item in type_args):
                raise self.error("class type arguments cannot be omitted", receiver.span)
            typ = NamedType(class_name, tuple(item for item in type_args if item is not None))
            instance = self.instantiate_class(typ, expression.span)
            method_name = expression.callee.name
            static = instance.static_methods.get(method_name)
            if static is None:
                raise self.error(
                    f"class `{typ.display()}` has no static function `{method_name}`",
                    expression.span,
                )
            if static.visibility == "private" and self.current_receiver != class_name:
                raise self.error(f"static function `{typ.display()}::{method_name}` is private", expression.span)
            return self.lower_symbol_call(static, expression.args, (), expression.span)
        if isinstance(expression.callee, ast.TypeApplyExpr):
            class_name = self.qualified_name(expression.callee.callee)
            if class_name is None or class_name not in self.classes:
                raise self.error(f"unknown generic class `{class_name or '<expression>'}`", expression.span)
            type_args = tuple(
                self.resolve_optional_annotation(node) for node in expression.callee.type_args
            )
            if any(item is None for item in type_args):
                raise self.error("class type arguments cannot be omitted", expression.callee.span)
            typ = NamedType(class_name, tuple(item for item in type_args if item is not None))
            return self.lower_class_constructor(typ, expression.args, expression.span, expected)
        if isinstance(expression.callee, ast.PathExpr):
            name = "::".join(expression.callee.parts)
            if name in self.classes:
                class_symbol = self.classes[name]
                if class_symbol.type_params:
                    raise self.error(
                        f"generic class `{name}` requires explicit type arguments",
                        expression.span,
                    )
                return self.lower_class_constructor(NamedType(name), expression.args, expression.span, expected)
            symbol = self.functions.get(name)
            if symbol is None:
                class_name, separator, method_name = name.rpartition("::")
                class_symbol = self.classes.get(class_name) if separator else None
                if class_symbol is not None:
                    if class_symbol.type_params:
                        raise self.error(
                            f"generic class `{class_name}` requires explicit type arguments before static method `{method_name}`",
                            expression.span,
                        )
                    instance = self.instantiate_class(NamedType(class_name), expression.span)
                    static = instance.static_methods.get(method_name)
                    if static is None:
                        raise self.error(f"class `{class_name}` has no static function `{method_name}`", expression.span)
                    if static.visibility == "private" and self.current_receiver != class_name:
                        raise self.error(f"static function `{name}` is private", expression.span)
                    return self.lower_symbol_call(static, expression.args, (), expression.span)
                raise self.error(f"unknown function `{name}`", expression.span)
            if symbol.visibility == "private" and self.module_name(self.current_function.name if self.current_function else "") != self.module_name(name):
                raise self.error(f"function `{name}` is private", expression.span)
            if symbol.type_params:
                return self.lower_generic_call(symbol, expression.args, (), expression.span)
            return self.lower_symbol_call(symbol, expression.args, (), expression.span)
        if isinstance(expression.callee, ast.NameExpr):
            name = expression.callee.name
            if name == "FILE__":
                if expression.args:
                    raise self.error("FILE__ expects no arguments", expression.span)
                source_name = expression.span.source_name
                if not source_name.startswith("<"):
                    source_name = str(Path(source_name).resolve())
                return self.coerce(ir.IRString(STR, expression.span, source_name), expected, expression.span)
            if name == "GET_ARGS__":
                if expression.args:
                    raise self.error("GET_ARGS__ expects no arguments", expression.span)
                self.require_main_process_value(name, expression.span)
                return self.coerce(
                    ir.IRCall(NamedType("List", (STR,)), expression.span, "__zy2_get_args", ()),
                    expected,
                    expression.span,
                )
            if name == "GET_EXE__":
                if expression.args:
                    raise self.error("GET_EXE__ expects no arguments", expression.span)
                self.require_main_process_value(name, expression.span)
                return self.coerce(
                    ir.IRCall(STR, expression.span, "__zy2_get_exe", ()),
                    expected,
                    expression.span,
                )
            if name == "CLONE__":
                if len(expression.args) != 1:
                    raise self.error("CLONE__ expects exactly one value", expression.span)
                return self.coerce(self.lower_expr(expression.args[0]), expected, expression.span)
            if name == "CLONE_REF__":
                if len(expression.args) != 1:
                    raise self.error("CLONE_REF__ expects exactly one reference", expression.span)
                reference = self.lower_expr(expression.args[0])
                if not isinstance(reference.typ, ReferenceType):
                    raise self.error(
                        f"CLONE_REF__ expects `&T` or `&mut T`, got `{reference.typ.display()}`",
                        expression.args[0].span,
                    )
                return self.coerce(
                    ir.IRReferenceValue(reference.typ.inner, expression.span, reference),
                    expected,
                    expression.span,
                )
            if name == "REF_SET__":
                if len(expression.args) != 2:
                    raise self.error("REF_SET__ expects a mutable reference and one value", expression.span)
                reference = self.lower_expr(expression.args[0])
                if not isinstance(reference.typ, ReferenceType) or not reference.typ.mutable:
                    raise self.error("REF_SET__ requires `&mut T`", expression.args[0].span)
                value = self.lower_expr(expression.args[1], reference.typ.inner)
                return ir.IRCall(VOID, expression.span, "__zy2_ref_set", (reference, value))
            if name == "LIST_LEN__":
                if len(expression.args) != 1:
                    raise self.error("LIST_LEN__ expects exactly one List<T> value", expression.span)
                value = self.lower_list_receiver(expression.args[0], "LIST_LEN__")
                return ir.IRCall(PrimitiveType("usize"), expression.span, "zy2_list_len", (value,))
            if name == "LIST_GET__":
                if len(expression.args) != 2:
                    raise self.error("LIST_GET__ expects a List<T> and i32 index", expression.span)
                receiver = self.lower_list_receiver(expression.args[0], "LIST_GET__")
                index = self.lower_expr(expression.args[1], PrimitiveType("i32"))
                assert isinstance(receiver.typ, NamedType)
                return ir.IRCall(receiver.typ.args[0], expression.span, "__zy2_list_get", (receiver, index), ERROR)
            if name == "LIST_PUSH__":
                if len(expression.args) != 2:
                    raise self.error("LIST_PUSH__ expects a List<T> variable and one value", expression.span)
                receiver = self.lower_list_receiver(expression.args[0], "LIST_PUSH__", mutable=True)
                assert isinstance(receiver.typ, NamedType)
                value = self.lower_expr(expression.args[1], receiver.typ.args[0])
                return ir.IRCall(VOID, expression.span, "__zy2_list_push", (receiver, value))
            if name == "LIST_SET__":
                if len(expression.args) != 3:
                    raise self.error("LIST_SET__ expects a List<T> variable, i32 index, and one value", expression.span)
                receiver = self.lower_list_receiver(expression.args[0], "LIST_SET__", mutable=True)
                assert isinstance(receiver.typ, NamedType)
                index = self.lower_expr(expression.args[1], PrimitiveType("i32"))
                value = self.lower_expr(expression.args[2], receiver.typ.args[0])
                return ir.IRCall(VOID, expression.span, "__zy2_list_set", (receiver, index, value), ERROR)
            if name == "LIST_POP__":
                if len(expression.args) != 1:
                    raise self.error("LIST_POP__ expects exactly one List<T> variable", expression.span)
                receiver = self.lower_list_receiver(expression.args[0], "LIST_POP__", mutable=True)
                assert isinstance(receiver.typ, NamedType)
                return ir.IRCall(receiver.typ.args[0], expression.span, "__zy2_list_pop", (receiver,), ERROR)
            if name == "LIST_CLEAR__":
                if len(expression.args) != 1:
                    raise self.error("LIST_CLEAR__ expects exactly one List<T> variable", expression.span)
                receiver = self.lower_list_receiver(expression.args[0], "LIST_CLEAR__", mutable=True)
                return ir.IRCall(VOID, expression.span, "__zy2_list_clear", (receiver,))
            if name == "PRINT_CMD__":
                if len(expression.args) != 2:
                    raise self.error("PRINT_CMD__ expects text and a #RRGGBB color", expression.span)
                if isinstance(expression.args[1], ast.StringExpr) and not re.fullmatch(
                    r"#[0-9A-Fa-f]{6}", expression.args[1].value
                ):
                    raise self.error("PRINT_CMD__ color literal must use #RRGGBB", expression.args[1].span)
                text = self.lower_expr(expression.args[0], STR)
                color = self.lower_expr(expression.args[1], STR)
                return ir.IRCall(VOID, expression.span, "zy2_print_cmd", (text, color))
            if name == "STR_TO_LIST__":
                if len(expression.args) != 1:
                    raise self.error("STR_TO_LIST__ expects exactly one str", expression.span)
                text = self.lower_expr(expression.args[0], STR)
                return ir.IRCall(NamedType("List", (STR,)), expression.span, "__zy2_str_to_list", (text,))
            if name == "STR_LEN__":
                if len(expression.args) != 1:
                    raise self.error("STR_LEN__ expects exactly one str", expression.span)
                text = self.lower_expr(expression.args[0], STR)
                return ir.IRCall(PrimitiveType("usize"), expression.span, "__zy3_str_len", (text,))
            if name == "STR_BYTE_LEN__":
                if len(expression.args) != 1:
                    raise self.error("STR_BYTE_LEN__ expects exactly one str", expression.span)
                text = self.lower_expr(expression.args[0], STR)
                return ir.IRCall(PrimitiveType("usize"), expression.span, "__zy3_str_byte_len", (text,))
            if name == "STR_GET__":
                if len(expression.args) != 2:
                    raise self.error("STR_GET__ expects a str and usize scalar index", expression.span)
                text = self.lower_expr(expression.args[0], STR)
                index = self.lower_expr(expression.args[1], PrimitiveType("usize"))
                return ir.IRCall(STR, expression.span, "__zy3_str_get", (text, index), ERROR)
            if name == "STR_SLICE__":
                if len(expression.args) != 3:
                    raise self.error("STR_SLICE__ expects a str, start, and end scalar index", expression.span)
                text = self.lower_expr(expression.args[0], STR)
                start = self.lower_expr(expression.args[1], PrimitiveType("usize"))
                end = self.lower_expr(expression.args[2], PrimitiveType("usize"))
                return ir.IRCall(STR, expression.span, "__zy3_str_slice", (text, start, end), ERROR)
            if name == "Box":
                if len(expression.args) != 1:
                    raise self.error("Box expects exactly one value", expression.span)
                expected_inner = expected.args[0] if is_box(expected) else None
                value = self.lower_expr(expression.args[0], expected_inner)
                box_type = NamedType("Box", (value.typ,))
                self.validate_box_payload(box_type, expression.span)
                return ir.IRBox(box_type, expression.span, value)
            if name in self.classes:
                symbol = self.classes[name]
                if symbol.type_params:
                    raise self.error(
                        f"generic class `{name}` requires explicit type arguments such as `{name}<i32>(...)`",
                        expression.span,
                    )
                return self.lower_class_constructor(NamedType(name), expression.args, expression.span, expected)
            local_type = self.find_local(name)
            if local_type is not None:
                callee = self.lower_expr(expression.callee)
                return self.lower_indirect_call(callee, expression.args, expression.span)
            symbol = self.functions.get(name)
            if symbol is None:
                raise self.error(f"unknown function `{name}`", expression.span)
            if symbol.type_params:
                return self.lower_generic_call(symbol, expression.args, (), expression.span)
            return self.lower_symbol_call(symbol, expression.args, (), expression.span)

        if isinstance(expression.callee, ast.FieldExpr):
            qualified = self.qualified_name(expression.callee)
            imported_name = qualified.replace(".", "::") if qualified is not None else None
            imported = self.functions.get(imported_name) if imported_name is not None else None
            if imported is not None:
                raise self.error(
                    f"module functions use `::` in ZyenLang 0.3; write `{imported_name}(...)`",
                    expression.callee.span,
                )
            receiver = self.lower_expr(expression.callee.receiver)
            method_name = expression.callee.name
            receiver_reference = receiver.typ if isinstance(receiver.typ, ReferenceType) else None
            receiver_value_type = receiver_reference.inner if receiver_reference is not None else receiver.typ
            if is_list(receiver_value_type):
                mutating_list_method = method_name in {"push", "add", "set", "pop", "remove", "clear"}
                if receiver_reference is not None:
                    receiver = self.autoderef_receiver(
                        receiver,
                        expression.callee.receiver,
                        f"List.{method_name}",
                        mutable_place=mutating_list_method,
                    )
                elif mutating_list_method:
                    self.require_mutable_list_receiver(expression.callee.receiver, method_name)
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
                if len(expression.args) != 1:
                    raise self.error(f"List.{method_name} expects exactly one value", expression.span)
                assert isinstance(receiver.typ, NamedType)
                value = self.lower_expr(expression.args[0], receiver.typ.args[0])
                return ir.IRCall(VOID, expression.span, "__zy2_list_push", (receiver, value))
            if is_list(receiver.typ) and method_name == "set":
                if len(expression.args) != 2:
                    raise self.error("List.set expects an i32 index and one value", expression.span)
                assert isinstance(receiver.typ, NamedType)
                index = self.lower_expr(expression.args[0], PrimitiveType("i32"))
                value = self.lower_expr(expression.args[1], receiver.typ.args[0])
                return ir.IRCall(VOID, expression.span, "__zy2_list_set", (receiver, index, value), ERROR)
            if is_list(receiver.typ) and method_name == "pop":
                if expression.args:
                    raise self.error("List.pop takes no arguments", expression.span)
                assert isinstance(receiver.typ, NamedType)
                return ir.IRCall(receiver.typ.args[0], expression.span, "__zy2_list_pop", (receiver,), ERROR)
            if is_list(receiver.typ) and method_name == "remove":
                if len(expression.args) != 1:
                    raise self.error("List.remove expects exactly one i32 index", expression.span)
                assert isinstance(receiver.typ, NamedType)
                index = self.lower_expr(expression.args[0], PrimitiveType("i32"))
                return ir.IRCall(receiver.typ.args[0], expression.span, "__zy2_list_remove", (receiver, index), ERROR)
            if is_list(receiver.typ) and method_name == "clear":
                if expression.args:
                    raise self.error("List.clear takes no arguments", expression.span)
                return ir.IRCall(VOID, expression.span, "__zy2_list_clear", (receiver,))
            class_reference = receiver.typ if isinstance(receiver.typ, ReferenceType) else None
            class_type = class_reference.inner if class_reference is not None else receiver.typ
            if isinstance(class_type, NamedType) and class_type.name in self.classes:
                instance = self.instantiate_class(class_type, expression.span)
                symbol = instance.methods.get(method_name)
                if symbol is not None and symbol.receiver is not None and symbol.receiver[2]:
                    if class_reference is not None and not class_reference.mutable:
                        raise self.error(
                            f"mutating method `{class_type.display()}.{method_name}` requires `&mut {class_type.display()}`",
                            expression.callee.receiver.span,
                        )
                    if class_reference is None:
                        self.require_mutable_receiver(expression.callee.receiver)
                if class_reference is not None:
                    receiver = self.autoderef_receiver(receiver, expression.callee.receiver, method_name)
                assert isinstance(receiver.typ, NamedType)
                instance = self.instantiate_class(receiver.typ, expression.span)
                symbol = instance.methods.get(method_name)
                if symbol is None:
                    field = instance.fields.get(method_name)
                    if field is not None and isinstance(field.typ, FunctionType):
                        if field.visibility == "private" and self.current_receiver != receiver.typ.name:
                            raise self.error(f"field `{receiver.typ.display()}.{field.name}` is private", expression.span)
                        callee = ir.IRField(field.typ, expression.callee.span, receiver, field.name)
                        return self.lower_indirect_call(callee, expression.args, expression.span)
                    raise self.error(f"class `{receiver.typ.display()}` has no method `{method_name}`", expression.span)
                if symbol.visibility == "private" and self.current_receiver != receiver.typ.name:
                    raise self.error(f"method `{receiver.typ.display()}.{method_name}` is private", expression.span)
                return self.lower_symbol_call(symbol, expression.args, (receiver,), expression.span)
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

    def lower_class_constructor(
        self,
        typ: NamedType,
        args: tuple[ast.Expr, ...],
        span: SourceSpan,
        expected: Type | None,
    ) -> ir.IRExpr:
        instance = self.instantiate_class(typ, span)
        current_module = self.module_name(self.current_function.name if self.current_function else "")
        if instance.symbol.visibility == "private" and current_module != self.module_name(instance.symbol.name):
            raise self.error(f"class `{typ.display()}` is private", span)
        lowered_args: tuple[ir.IRExpr, ...]
        initializer_name = None
        if instance.initializer is None:
            if args:
                raise self.error(f"class `{typ.display()}` has no init parameters", span)
            lowered_args = ()
        else:
            initializer = instance.initializer
            if initializer.visibility == "private" and self.current_receiver != typ.name:
                raise self.error(f"initializer for `{typ.display()}` is private", span)
            bound = self.bind_call_arguments(initializer, args, span)
            lowered_args = tuple(
                self.lower_expr(value, param_type)
                for value, (_, param_type, _) in zip(bound, initializer.params)
            )
            initializer_name = initializer.c_name
        defaults: list[tuple[str, ir.IRExpr]] = []
        for field in instance.fields.values():
            if field.node.default is not None:
                defaults.append((field.name, self.lower_expr(field.node.default, field.typ)))
        value = ir.IRClassNew(typ, span, initializer_name, lowered_args, tuple(defaults))
        return self.coerce(value, expected, span)

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

    def lower_closure(self, expression: ast.ClosureExpr, expected: Type | None) -> ir.IRExpr:
        seen: set[str] = set()
        params: list[tuple[str, Type, bool]] = []
        for param in expression.params:
            if param.name in seen:
                raise self.error(f"duplicate closure parameter `{param.name}`", param.span)
            seen.add(param.name)
            typ = self.resolve_optional_annotation(param.type_node)
            assert typ is not None
            self.validate_box_position(typ, param.span, "closure parameter")
            params.append((param.name, typ, param.mutable))
        return_type = self.resolve_optional_annotation(expression.return_type)
        assert return_type is not None
        if isinstance(return_type, ReferenceType):
            raise self.error("closures cannot return references", expression.return_type.span)
        self.validate_box_position(return_type, expression.return_type.span, "closure return type")
        function_type = FunctionType(tuple(item for _, item, _ in params), return_type)
        self.validate_function_types(function_type, expression.span)

        self.closure_counter += 1
        closure_name = f"__zy3_closure_{self.closure_counter}"
        synthetic = ast.FunctionDef(
            closure_name,
            expression.params,
            expression.return_type,
            expression.body,
            "private",
            expression.span,
        )
        symbol = FunctionSymbol(
            closure_name,
            closure_name,
            "private",
            tuple(params),
            return_type,
            None,
            None,
            (),
            synthetic,
            {},
        )

        previous_function = self.current_function
        previous_receiver = self.current_receiver
        previous_loop_depth = self.loop_depth
        previous_catch_types = self.catch_result_types
        outer_scope_count = len(self.scopes)
        context = ClosureContext(outer_scope_count, {})
        self.current_function = symbol
        self.current_receiver = None
        self.loop_depth = 0
        self.catch_result_types = []
        self.push_scope()
        self.closure_contexts.append(context)
        ir_params: list[ir.IRParam] = []
        try:
            for param_node, (name, typ, mutable) in zip(expression.params, params):
                local_name = self.define_local(name, typ, param_node.span)
                ir_params.append(ir.IRParam(local_name, typ, mutable))
            body = self.lower_block(expression.body, push_scope=False)
            self.validate_current_task_scope()
            if return_type != VOID and not self.block_terminates(body):
                raise self.error(
                    f"closure must return `{return_type.display()}` on every path",
                    expression.body.span,
                )
        finally:
            self.closure_contexts.pop()
            self.pop_scope()
            self.current_function = previous_function
            self.current_receiver = previous_receiver
            self.loop_depth = previous_loop_depth
            self.catch_result_types = previous_catch_types

        captures = tuple(
            (field_name, capture_type, source)
            for field_name, capture_type, source in context.captures.values()
        )
        self.lowered_closures.append(
            ir.IRClosureDef(
                closure_name,
                function_type,
                tuple(ir_params),
                body,
                tuple((field_name, capture_type) for field_name, capture_type, _ in captures),
            )
        )
        value = ir.IRClosure(
            function_type,
            expression.span,
            closure_name,
            tuple((field_name, source) for field_name, _, source in captures),
        )
        return self.coerce(value, expected, expression.span)

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
        if self.contains_reference(typ) and not isinstance(typ, ReferenceType):
            raise self.error(f"references cannot be nested in a {context}", span)
        if is_list(typ):
            self.validate_list_element(typ.args[0], span)
            return
        if is_box(typ):
            self.validate_box_payload(typ, span)
            return
        if isinstance(typ, NamedType):
            for item in typ.args:
                self.validate_box_position(item, span, context)
        elif isinstance(typ, TupleType):
            for item in typ.items:
                self.validate_box_position(item, span, context)
        elif isinstance(typ, OptionalType):
            self.validate_box_position(typ.inner, span, context)

    def validate_function_types(self, typ: Type, span: SourceSpan) -> None:
        if isinstance(typ, ReferenceType):
            self.validate_function_types(typ.inner, span)
            return
        if isinstance(typ, FunctionType):
            for item in (*typ.params, typ.return_type):
                if isinstance(item, FunctionType):
                    self.validate_function_types(item, span)
                    continue
                if isinstance(item, TypeVar):
                    continue
                if isinstance(item, PrimitiveType) and item not in {ERROR, NULL}:
                    continue
                if isinstance(item, NamedType) and (item.name in self.structs or item.name in self.classes):
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
        if inner == VOID or is_task(inner) or self.contains_reference(inner):
            raise self.error(f"Box payload `{inner.display()}` is not an owning value type", span)
        self.validate_box_position(inner, span, "Box payload")

    def validate_list_element(self, element: Type, span: SourceSpan) -> None:
        if is_task(element):
            raise self.error("List<Task<T>> is not allowed because Task is linear", span)
        if self.contains_reference(element):
            raise self.error("references cannot be stored in List<T>", span)
        if is_box(element):
            self.validate_box_payload(element, span)
            return
        if is_list(element):
            self.validate_list_element(element.args[0], span)
            return
        if isinstance(element, NamedType) and element.name in self.classes:
            return
        self.validate_box_position(element, span, "List element")

    @classmethod
    def contains_reference(cls, typ: Type) -> bool:
        if isinstance(typ, ReferenceType):
            return True
        if isinstance(typ, NamedType):
            return any(cls.contains_reference(arg) for arg in typ.args)
        if isinstance(typ, TupleType):
            return any(cls.contains_reference(item) for item in typ.items)
        if isinstance(typ, OptionalType):
            return cls.contains_reference(typ.inner)
        if isinstance(typ, FunctionType):
            return any(cls.contains_reference(item) for item in (*typ.params, typ.return_type))
        return False

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
        if isinstance(typ, ReferenceType):
            return cls.contains_type_var(typ.inner)
        return False

    def supports_equality(self, typ: Type, visiting: set[str] | None = None) -> bool:
        if isinstance(typ, TypeVar):
            return True
        if typ in {BOOL, STR} or is_numeric(typ) or is_box(typ):
            return True
        if isinstance(typ, NamedType) and typ.name in self.classes:
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
        if typ in {STR, ERROR} or is_box(typ) or is_list(typ) or isinstance(typ, FunctionType):
            return True
        if isinstance(typ, NamedType) and typ.name in self.classes:
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

    def borrow_root_name(self, expression: ast.Expr) -> str | None:
        current = expression
        while isinstance(current, (ast.FieldExpr, ast.IndexExpr)):
            current = current.receiver
        return current.name if isinstance(current, ast.NameExpr) else None

    def borrow_place(self, expression: ast.Expr) -> BorrowPlace:
        projections: list[str] = []
        current = expression
        while isinstance(current, (ast.FieldExpr, ast.IndexExpr)):
            if isinstance(current, ast.FieldExpr):
                projections.append(current.name)
            else:
                projections.append("[*]")
            current = current.receiver
        if not isinstance(current, ast.NameExpr):
            raise self.error("references require a stable local or local-rooted field", expression.span)
        if self.is_captured_local(current.name):
            raise self.error("references cannot borrow a value from an outer closure scope", expression.span)
        root = self.lookup_local_name(current.name, current.span)
        ordered = tuple(reversed(projections))
        label = current.name + "".join(
            projection if projection == "[*]" else f".{projection}" for projection in ordered
        )
        return BorrowPlace(root, label, ordered)

    def lower_borrow_place(self, expression: ast.Expr) -> ir.IRExpr:
        if isinstance(expression, ast.NameExpr):
            return self.lower_local_access(expression.name, expression.span)
        if isinstance(expression, ast.FieldExpr):
            value = self.lower_field(expression)
            if not isinstance(value, ir.IRField):
                raise self.error("only concrete struct or class fields can be borrowed", expression.span)
            if isinstance(value.receiver, ir.IRField) and (
                isinstance(value.receiver.typ, NamedType) and value.receiver.typ.name in self.classes
            ):
                raise self.error(
                    "borrowing through a nested class handle is not allowed; bind that class handle to a local first",
                    expression.span,
                )
            return value
        raise self.error("references require a stable local or local-rooted field", expression.span)

    def autoderef_receiver(
        self,
        receiver: ir.IRExpr,
        source: ast.Expr,
        operation: str,
        *,
        mutable_place: bool = False,
    ) -> ir.IRExpr:
        if not isinstance(receiver.typ, ReferenceType):
            return receiver
        if mutable_place and not receiver.typ.mutable:
            raise self.error(f"{operation} requires `&mut {receiver.typ.inner.display()}`", source.span)
        if mutable_place:
            if isinstance(source, ast.BorrowExpr):
                if not source.mutable:
                    raise self.error(f"{operation} requires a mutable reference", source.span)
                return self.lower_borrow_place(source.value)
            if not isinstance(source, ast.NameExpr):
                raise self.error(
                    f"{operation} through a reference requires a stable reference local or parameter",
                    source.span,
                )
            return ir.IRReferencePlace(receiver.typ.inner, source.span, receiver)
        return ir.IRReferenceValue(receiver.typ.inner, source.span, receiver)

    def lower_list_receiver(self, source: ast.Expr, operation: str, *, mutable: bool = False) -> ir.IRExpr:
        receiver = self.lower_expr(source)
        if isinstance(receiver.typ, ReferenceType):
            receiver = self.autoderef_receiver(
                receiver,
                source,
                operation,
                mutable_place=mutable,
            )
        elif mutable:
            self.require_mutable_list_receiver(source, operation)
        if not is_list(receiver.typ):
            raise self.error(
                f"{operation} expects `List<T>`, got `{receiver.typ.display()}`",
                source.span,
            )
        return receiver

    def lower_list_element_borrow(self, expression: ast.BorrowExpr, expected: Type | None) -> ir.IRExpr:
        assert isinstance(expression.value, ast.IndexExpr)
        indexed = expression.value
        place = self.borrow_place(indexed)
        root_name = self.borrow_root_name(indexed)
        assert root_name is not None
        root_type = self.lookup_local(root_name, indexed.span)
        receiver = self.lower_expr(indexed.receiver)
        if expression.mutable:
            if isinstance(receiver.typ, ReferenceType):
                raise self.error(
                    "mutable List element borrows require an owned local List or value-struct field; use LIST_SET__ through &mut List<T>",
                    indexed.receiver.span,
                )
            if isinstance(root_type, ReferenceType) or self.place_crosses_class(receiver) or (
                isinstance(root_type, NamedType) and root_type.name in self.classes
            ):
                raise self.error(
                    "mutable List element borrows cannot cross a class or reference alias",
                    indexed.receiver.span,
                )
            self.require_mutable_list_receiver(indexed.receiver, "mutable List element borrow")
        else:
            receiver = self.autoderef_receiver(receiver, indexed.receiver, "List element borrow")
        if not is_list(receiver.typ):
            raise self.error(
                f"List element borrow requires `List<T>`, got `{receiver.typ.display()}`",
                indexed.receiver.span,
            )
        assert isinstance(receiver.typ, NamedType)
        index = self.lower_expr(indexed.index, PrimitiveType("i32"))
        reference_type = ReferenceType(receiver.typ.args[0], expression.mutable)
        loan = self.register_borrow(place, expression.mutable, expression.span)
        target = "__zy2_list_borrow_mut" if expression.mutable else "__zy2_list_borrow"
        result = self.coerce(
            ir.IRCall(reference_type, expression.span, target, (receiver, index), ERROR),
            expected,
            expression.span,
        )
        self.expression_borrow_loans[id(result)] = [loan]
        return result

    def place_crosses_class(self, value: ir.IRExpr) -> bool:
        current = value
        while isinstance(current, ir.IRField):
            receiver_type = current.receiver.typ
            if isinstance(receiver_type, NamedType) and receiver_type.name in self.classes:
                return True
            current = current.receiver
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
        self.require_mutable_receiver(receiver)

    def require_mutable_receiver(self, receiver: ast.Expr) -> None:
        root = receiver
        while isinstance(root, ast.FieldExpr):
            root = root.receiver
        if not isinstance(root, ast.NameExpr):
            return
        if self.is_captured_local(root.name):
            raise self.error("closure captures are readonly snapshots", receiver.span)
        if root.name == "this" and self.current_function and self.current_function.owner_class is not None:
            if self.current_function.receiver is not None and not self.current_function.receiver[2]:
                raise self.error("readonly class method cannot modify `this`", receiver.span)
        place = self.borrow_place(receiver)
        readonly, mutable = self.active_borrows(place)
        if readonly or mutable:
            raise self.error(f"cannot modify `{place.label}` while it is borrowed", receiver.span)

    @staticmethod
    def qualified_name(expression: ast.Expr) -> str | None:
        if isinstance(expression, ast.PathExpr):
            return "::".join(expression.parts)
        if isinstance(expression, ast.TypeApplyExpr):
            return Lowerer.qualified_name(expression.callee)
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
            raise self.error("receiver functions were removed in ZyenLang 0.3; use a class method or module function", span)
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
        if isinstance(template, ReferenceType) and isinstance(actual, ReferenceType):
            if template.mutable != actual.mutable:
                raise self.error(
                    f"generic argument expected `{template.display()}`, got `{actual.display()}`",
                    span,
                )
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
        return name.rsplit("::", 1)[0] if "::" in name else ""

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
            raise self.error("generic structs are not supported in ZyenLang 0.3; use a generic class or a concrete struct", expression.span)
        seen: set[str] = set()
        fields: list[tuple[str, ir.IRExpr]] = []
        for item in expression.fields:
            if item.name in seen:
                raise self.error(f"duplicate initializer for `{expression.name}.{item.name}`", item.span)
            seen.add(item.name)
            field = symbol.fields.get(item.name)
            if field is None:
                raise self.error(f"struct `{expression.name}` has no field `{item.name}`", item.span)
            if field.visibility == "private" and current_module != self.module_name(expression.name):
                raise self.error(f"field `{expression.name}.{item.name}` is private", item.span)
            fields.append((item.name, self.lower_expr(item.value, field.typ)))
        for field in symbol.fields.values():
            if field.name not in seen and field.node.default is not None:
                fields.append((field.name, self.lower_expr(field.node.default, field.typ)))
        return ir.IRStruct(NamedType(expression.name), expression.span, expression.name, tuple(fields))

    def coerce(self, value: ir.IRExpr, expected: Type | None, span: SourceSpan) -> ir.IRExpr:
        if expected is None or value.typ == expected:
            return value
        if (
            isinstance(expected, ReferenceType)
            and not expected.mutable
            and isinstance(value.typ, ReferenceType)
            and value.typ.mutable
            and value.typ.inner == expected.inner
        ):
            coerced = ir.IRReferenceCoerce(expected, span, value)
            loans = self.expression_borrow_loans.get(id(value))
            if loans is not None:
                self.expression_borrow_loans[id(coerced)] = loans
            return coerced
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
            if isinstance(final.condition, ir.IRBool):
                selected = final.then_block if final.condition.value else final.else_block
                return selected is not None and Lowerer.block_terminates(selected)
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
            if isinstance(final.condition, ir.IRBool):
                selected = final.then_block if final.condition.value else final.else_block
                return selected is not None and Lowerer.block_catch_completes(selected)
            return final.else_block is not None and Lowerer.block_catch_completes(final.then_block) and Lowerer.block_catch_completes(final.else_block)
        if isinstance(final, ir.IRIfLet):
            return final.else_block is not None and Lowerer.block_catch_completes(final.then_block) and Lowerer.block_catch_completes(final.else_block)
        return False


def lower(program: ast.Program, source_name: str = "<source>", *, require_main: bool = True) -> ir.IRProgram:
    return Lowerer(program, source_name, require_main=require_main).lower()
