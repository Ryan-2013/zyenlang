from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from . import ast
from .diagnostics import CompileError
from .package_manager import (
    LockFile,
    MANIFEST_NAME,
    PackageError,
    find_project_root,
    load_manifest,
    locked_packages_for_project,
)
from .parser import parse


MAX_MODULE_DEPTH = 64
MAX_UNIQUE_MODULES = 256
MAX_MODULE_EXPANSIONS = 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024


class ModuleLoader:
    def __init__(self) -> None:
        self.std_root = Path(__file__).resolve().parent / "std"
        self._unique_modules: set[Path] = set()
        self._module_expansions = 0
        self.project_root: Path | None = None
        self.package_lock: LockFile | None = None
        self.package_roots: dict[str, Path] = {}

    def load(self, root: Path, source_override: str | None = None) -> ast.Program:
        root = root.resolve()
        self.project_root = find_project_root(root, required=False)
        self._account_module(root, 0)
        if source_override is not None and len(source_override.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise CompileError(f"source exceeds the {MAX_SOURCE_BYTES}-byte safety limit", source_name=str(root))
        program = parse(source_override, str(root)) if source_override is not None else self.read(root)
        aliases: set[str] = set()
        imported: list[ast.Definition] = []
        for item in program.imports:
            if item.alias in aliases:
                raise CompileError(f"duplicate import alias `{item.alias}`", item.span)
            aliases.add(item.alias)
            child = self.resolve(root, item)
            if child == root:
                raise CompileError(f"circular import: {root} -> {root}", item.span)
            imported.extend(self.load_namespaced(child, item.alias, [root]))
        return ast.Program((), tuple(imported) + program.definitions)

    def read(self, path: Path) -> ast.Program:
        if not path.is_file():
            raise CompileError(f"module not found: {path}", source_name=str(path))
        try:
            size = path.stat().st_size
            if size > MAX_SOURCE_BYTES:
                raise CompileError(f"source exceeds the {MAX_SOURCE_BYTES}-byte safety limit", source_name=str(path))
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CompileError("source must be valid UTF-8", source_name=str(path)) from exc
        return parse(source, str(path))

    def _account_module(self, path: Path, depth: int) -> None:
        if depth > MAX_MODULE_DEPTH:
            raise CompileError(f"import depth exceeds the safety limit of {MAX_MODULE_DEPTH}", source_name=str(path))
        self._module_expansions += 1
        if self._module_expansions > MAX_MODULE_EXPANSIONS:
            raise CompileError(
                f"module graph exceeds the expansion safety limit of {MAX_MODULE_EXPANSIONS}",
                source_name=str(path),
            )
        self._unique_modules.add(path)
        if len(self._unique_modules) > MAX_UNIQUE_MODULES:
            raise CompileError(
                f"module graph exceeds the safety limit of {MAX_UNIQUE_MODULES} unique modules",
                source_name=str(path),
            )

    def resolve(self, parent: Path, item: ast.ImportDef) -> Path:
        if item.is_angle:
            parts = item.path.split("/")
            if parts and parts[0] == "std":
                if len(parts) < 2:
                    raise CompileError("standard imports must use `<std/module>`", item.span)
                relative = Path(*parts[1:]).with_suffix(".zy")
                resolved = (self.std_root / relative).resolve()
                if self.std_root.resolve() not in resolved.parents:
                    raise CompileError("standard import escapes the v2 std root", item.span)
                return resolved
            return self.resolve_package(parent, parts, item)
        resolved = (parent.parent / item.path).resolve()
        if resolved.suffix == "":
            resolved = resolved.with_suffix(".zy")
        package_root = self.containing_package(parent)
        if package_root is not None and package_root != resolved and package_root not in resolved.parents:
            raise CompileError("relative import escapes the package root", item.span)
        return resolved

    def load_package_state(self, span) -> None:
        if self.package_lock is not None:
            return
        if self.project_root is None:
            raise CompileError(
                f"package imports require a {MANIFEST_NAME} project and zy.lock",
                span,
            )
        try:
            self.package_lock, self.package_roots = locked_packages_for_project(self.project_root)
        except PackageError as exc:
            raise CompileError(str(exc), span) from exc

    def containing_package(self, path: Path) -> Path | None:
        resolved = path.resolve()
        for root in self.package_roots.values():
            if resolved == root or root in resolved.parents:
                return root
        return None

    def resolve_package(self, parent: Path, parts: list[str], item: ast.ImportDef) -> Path:
        if not parts or not parts[0]:
            raise CompileError("package imports require a package name", item.span)
        self.load_package_state(item.span)
        assert self.package_lock is not None
        package_name = parts[0]
        records = self.package_lock.by_name()
        owner_name = next(
            (
                name
                for name, root in self.package_roots.items()
                if parent.resolve() == root or root in parent.resolve().parents
            ),
            None,
        )
        allowed = (
            set(self.package_lock.root_dependencies)
            if owner_name is None
            else set(records[owner_name].dependencies)
        )
        if package_name not in allowed:
            owner = "root project" if owner_name is None else f"package `{owner_name}`"
            raise CompileError(f"package `{package_name}` is not a declared dependency of the {owner}", item.span)
        package_root = self.package_roots.get(package_name)
        if package_root is None:
            raise CompileError(f"package `{package_name}` is missing from zy.lock", item.span)
        try:
            if len(parts) == 1:
                relative = Path(*load_manifest(package_root).entry.split("/"))
            else:
                relative = Path("src", *parts[1:]).with_suffix(".zy")
        except PackageError as exc:
            raise CompileError(str(exc), item.span) from exc
        resolved = (package_root / relative).resolve()
        if resolved != package_root and package_root not in resolved.parents:
            raise CompileError("package import escapes the package root", item.span)
        return resolved

    def load_namespaced(self, path: Path, namespace: str, stack: list[Path]) -> list[ast.Definition]:
        path = path.resolve()
        self._account_module(path, len(stack))
        if path in stack:
            chain = " -> ".join(str(item) for item in stack + [path])
            raise CompileError(f"circular import: {chain}", source_name=str(path))
        program = self.read(path)
        definitions: list[ast.Definition] = []
        aliases: set[str] = set()
        for item in program.imports:
            if item.alias in aliases:
                raise CompileError(f"duplicate import alias `{item.alias}`", item.span)
            aliases.add(item.alias)
            child = self.resolve(path, item)
            if child in stack or child == path:
                cycle_paths = stack + [path, child]
                start = cycle_paths.index(child)
                chain = " -> ".join(str(value) for value in cycle_paths[start:])
                raise CompileError(f"circular import: {chain}", item.span)
            definitions.extend(self.load_namespaced(child, f"{namespace}.{item.alias}", stack + [path]))
        definitions.extend(namespace_program(program, namespace, aliases).definitions)
        return definitions


def namespace_program(program: ast.Program, namespace: str, import_aliases: set[str] | None = None) -> ast.Program:
    import_aliases = import_aliases or set()
    local_structs = {item.name for item in program.definitions if isinstance(item, ast.StructDef)}
    local_functions = {
        item.name
        for item in program.definitions
        if (
            isinstance(item, ast.NativeFunctionDef)
            or (isinstance(item, ast.FunctionDef) and item.receiver is None)
        )
    }

    def type_node(node: ast.TypeNode) -> ast.TypeNode:
        if isinstance(node, ast.NamedTypeNode):
            first = node.name.split(".", 1)[0]
            if node.name in local_structs or first in import_aliases:
                name = f"{namespace}.{node.name}"
            else:
                name = node.name
            return ast.NamedTypeNode(node.span, name, tuple(type_node(arg) for arg in node.args))
        if isinstance(node, ast.TupleTypeNode):
            return ast.TupleTypeNode(node.span, tuple(type_node(item) for item in node.items))
        if isinstance(node, ast.OptionalTypeNode):
            return ast.OptionalTypeNode(node.span, type_node(node.inner) if node.inner else None)
        return node

    def expression(node: ast.Expr) -> ast.Expr:
        if isinstance(node, ast.NameExpr):
            if node.name in import_aliases:
                name = f"{namespace}.{node.name}"
            else:
                name = node.name
            return ast.NameExpr(node.span, name)
        if isinstance(node, ast.UnaryExpr):
            return ast.UnaryExpr(node.span, node.operator, expression(node.operand))
        if isinstance(node, ast.BinaryExpr):
            return ast.BinaryExpr(node.span, expression(node.left), node.operator, expression(node.right))
        if isinstance(node, ast.FieldExpr):
            return ast.FieldExpr(node.span, expression(node.receiver), node.name)
        if isinstance(node, ast.CallExpr):
            if isinstance(node.callee, ast.NameExpr) and node.callee.name in local_functions:
                callee: ast.Expr = ast.NameExpr(node.callee.span, f"{namespace}.{node.callee.name}")
            else:
                callee = expression(node.callee)
            return ast.CallExpr(node.span, callee, tuple(expression(arg) for arg in node.args))
        if isinstance(node, ast.TupleExpr):
            return ast.TupleExpr(node.span, tuple(expression(item) for item in node.items))
        if isinstance(node, ast.ListExpr):
            return ast.ListExpr(node.span, tuple(expression(item) for item in node.items))
        if isinstance(node, ast.StructExpr):
            name = f"{namespace}.{node.name}" if node.name in local_structs else node.name
            fields = tuple(ast.StructFieldValue(item.name, expression(item.value), item.span) for item in node.fields)
            return ast.StructExpr(node.span, name, fields)
        if isinstance(node, ast.CatchExpr):
            return ast.CatchExpr(node.span, expression(node.value), node.error_name, block(node.handler))
        if isinstance(node, ast.SpawnExpr):
            return ast.SpawnExpr(node.span, expression(node.call))
        if isinstance(node, ast.AwaitExpr):
            return ast.AwaitExpr(node.span, expression(node.task))
        if isinstance(node, ast.TypeOfExpr):
            return ast.TypeOfExpr(node.span, expression(node.value), type_node(node.target_type))
        return node

    def statement(node: ast.Stmt) -> ast.Stmt:
        if isinstance(node, ast.LetStmt):
            bindings = tuple(
                ast.Binding(item.name, type_node(item.type_node) if item.type_node else None, item.span)
                for item in node.bindings
            )
            return ast.LetStmt(node.span, bindings, expression(node.value))
        if isinstance(node, ast.ReturnStmt):
            return ast.ReturnStmt(node.span, tuple(expression(item) for item in node.values))
        if isinstance(node, ast.StopStmt):
            return ast.StopStmt(node.span, expression(node.value))
        if isinstance(node, ast.RecoverStmt):
            return ast.RecoverStmt(node.span, tuple(expression(item) for item in node.values))
        if isinstance(node, ast.IfStmt):
            return ast.IfStmt(
                node.span,
                expression(node.condition),
                block(node.then_block),
                block(node.else_block) if node.else_block else None,
            )
        if isinstance(node, ast.IfLetStmt):
            return ast.IfLetStmt(
                node.span,
                node.binding,
                expression(node.value),
                block(node.then_block),
                block(node.else_block) if node.else_block else None,
            )
        if isinstance(node, ast.WhileStmt):
            return ast.WhileStmt(node.span, expression(node.condition), block(node.body))
        if isinstance(node, ast.AssignStmt):
            return ast.AssignStmt(node.span, expression(node.target), expression(node.value))
        if isinstance(node, ast.ExprStmt):
            return ast.ExprStmt(node.span, expression(node.value))
        return node

    def block(node: ast.Block) -> ast.Block:
        return ast.Block(tuple(statement(item) for item in node.statements), node.span)

    definitions: list[ast.Definition] = []
    for definition in program.definitions:
        if isinstance(definition, ast.StructDef):
            fields = tuple(
                ast.FieldDef(
                    field.name,
                    type_node(field.type_node),
                    field.visibility,
                    field.span,
                    expression(field.default) if field.default else None,
                )
                for field in definition.fields
            )
            definitions.append(
                ast.StructDef(
                    f"{namespace}.{definition.name}",
                    fields,
                    definition.visibility,
                    definition.type_params,
                    definition.span,
                )
            )
        elif isinstance(definition, ast.FunctionDef):
            receiver = None
            if definition.receiver:
                receiver = ast.Param(
                    definition.receiver.name,
                    type_node(definition.receiver.type_node),
                    definition.receiver.span,
                    definition.receiver.mutable,
                )
            params = tuple(
                ast.Param(param.name, type_node(param.type_node), param.span, param.mutable)
                for param in definition.params
            )
            definitions.append(
                ast.FunctionDef(
                    name=definition.name if receiver is not None else f"{namespace}.{definition.name}",
                    params=params,
                    return_type=type_node(definition.return_type),
                    body=block(definition.body),
                    visibility=definition.visibility,
                    span=definition.span,
                    receiver=receiver,
                    type_params=definition.type_params,
                    throws=type_node(definition.throws) if definition.throws else None,
                )
            )
        elif isinstance(definition, ast.NativeFunctionDef):
            params = tuple(
                ast.Param(param.name, type_node(param.type_node), param.span, param.mutable)
                for param in definition.params
            )
            definitions.append(
                ast.NativeFunctionDef(
                    name=f"{namespace}.{definition.name}",
                    params=params,
                    return_type=type_node(definition.return_type),
                    symbol=definition.symbol,
                    visibility=definition.visibility,
                    span=definition.span,
                    throws=type_node(definition.throws) if definition.throws else None,
                )
            )
        else:
            definitions.append(definition)
    return ast.Program((), tuple(definitions))


def load_program(path: Path, source_override: str | None = None) -> ast.Program:
    return ModuleLoader().load(path, source_override)
