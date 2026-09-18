from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from . import ast
from .c_module import NativeTemplate, load_template
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
        self.c_module_templates: dict[Path, NativeTemplate] = {}
        self.c_module_emitted: set[Path] = set()

    def load(self, root: Path, source_override: str | None = None) -> ast.Program:
        root = root.resolve()
        self.project_root = find_project_root(root, required=False)
        self._account_module(root, 0)
        if source_override is not None and len(source_override.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise CompileError(f"source exceeds the {MAX_SOURCE_BYTES}-byte safety limit", source_name=str(root))
        program = parse(source_override, str(root)) if source_override is not None else self.read(root)
        program = self.expand_c_modules(program, root)
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
        parts = item.path.split("::")
        root = parts[0]
        if root == "std":
            relative = Path(*parts[1:]).with_suffix(".zy")
            resolved = (self.std_root / relative).resolve()
            if self.std_root.resolve() not in resolved.parents:
                raise CompileError("standard import escapes the std root", item.span)
            return resolved
        if root == "crate":
            project = self.project_root or parent.parent
            source_root = project / "src" if (project / "src").is_dir() else project
            resolved = (source_root / Path(*parts[1:])).with_suffix(".zy").resolve()
            if source_root.resolve() not in resolved.parents:
                raise CompileError("crate import escapes the project source root", item.span)
            return resolved
        return self.resolve_package(parent, parts, item)

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
        program = self.expand_c_modules(self.read(path), path)
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
            definitions.extend(self.load_namespaced(child, f"{namespace}::{item.alias}", stack + [path]))
        definitions.extend(namespace_program(program, namespace, aliases).definitions)
        return definitions

    def native_package_root(self, owner: Path) -> Path:
        owner = owner.resolve()
        std_root = self.std_root.resolve()
        if owner == std_root or std_root in owner.parents:
            return std_root
        package = self.containing_package(owner)
        if package is not None:
            return package
        if self.project_root is not None:
            project = self.project_root.resolve()
            if owner == project or project in owner.parents:
                return project
        return owner.parent

    @staticmethod
    def is_c_module_type(node: ast.TypeNode, aliases: set[str]) -> str | None:
        if isinstance(node, ast.NamedTypeNode) and not node.args and "::" in node.name:
            alias, name = node.name.split("::", 1)
            if alias in aliases and name == "Module":
                return alias
        return None

    @staticmethod
    def c_module_load_alias(node: ast.Expr) -> tuple[str, ast.CallExpr] | None:
        if not isinstance(node, ast.CallExpr):
            return None
        if isinstance(node.callee, ast.PathExpr) and len(node.callee.parts) == 2 and node.callee.parts[1] == "load":
            return node.callee.parts[0], node
        return None

    def template_for_load(
        self,
        call: ast.CallExpr,
        owner: Path,
        aliases: set[str],
    ) -> NativeTemplate:
        match = self.c_module_load_alias(call)
        assert match is not None
        alias, _ = match
        if alias not in aliases:
            raise CompileError(
                f"`{alias}::load` requires `import std::c_module as {alias}`",
                call.span,
            )
        if len(call.args) != 1 or not isinstance(call.args[0], ast.StringExpr):
            raise CompileError("c_module::load requires one string literal path", call.span)
        raw = Path(call.args[0].value)
        if raw.is_absolute():
            raise CompileError("c_module template paths must be relative to the declaring .zy file", call.span)
        resolved = (owner.parent / raw).resolve()
        template = self.c_module_templates.get(resolved)
        if template is None:
            template = load_template(resolved, call.span, self.native_package_root(owner))
            self.c_module_templates[resolved] = template
        return template

    @staticmethod
    def rewrite_template_type(node: ast.TypeNode, template: NativeTemplate) -> ast.TypeNode:
        struct_names = {item.name for item in template.structs}
        digest = template.hidden_name.removeprefix("__zlcm_").split("__", 1)[0]

        def visit(value: ast.TypeNode) -> ast.TypeNode:
            if isinstance(value, ast.NamedTypeNode):
                name = (
                    f"__zlcm_struct_{digest}__{value.name}"
                    if value.name in struct_names
                    else value.name
                )
                return ast.NamedTypeNode(value.span, name, tuple(visit(arg) for arg in value.args))
            if isinstance(value, ast.FunctionTypeNode):
                return ast.FunctionTypeNode(
                    value.span,
                    tuple(visit(item) for item in value.params),
                    visit(value.return_type) if value.return_type else None,
                )
            if isinstance(value, ast.OptionalTypeNode):
                return ast.OptionalTypeNode(value.span, visit(value.inner) if value.inner else None)
            if isinstance(value, ast.TupleTypeNode):
                return ast.TupleTypeNode(value.span, tuple(visit(item) for item in value.items))
            return value

        return visit(node)

    def c_module_definitions(self, template: NativeTemplate, span) -> list[ast.Definition]:
        if template.path in self.c_module_emitted:
            return []
        self.c_module_emitted.add(template.path)
        digest = template.hidden_name.removeprefix("__zlcm_").split("__", 1)[0]
        definitions: list[ast.Definition] = []
        for item in template.structs:
            hidden_struct = f"__zlcm_struct_{digest}__{item.name}"
            definitions.append(ast.StructDef(
                hidden_struct,
                tuple(
                    ast.FieldDef(
                        field.name,
                        self.rewrite_template_type(field.type_node, template),
                        "public",
                        span,
                    )
                    for field in item.fields
                ),
                "private",
                (),
                span,
                item.name,
            ))
        wrapper_fields: list[ast.FieldDef] = []
        for function in template.functions:
            return_type = self.rewrite_template_type(function.return_type, template)
            params = tuple(
                ast.Param(name, self.rewrite_template_type(typ, template), span)
                for name, typ in function.params
            )
            function_type = ast.FunctionTypeNode(span, tuple(param.type_node for param in params), return_type)
            wrapper_fields.append(ast.FieldDef(function.name, function_type, "public", span))
            definitions.append(ast.NativeFunctionDef(
                f"__zlcm_{digest}_fn_{function.name}",
                params,
                return_type,
                function.symbol,
                "private",
                span,
            ))
        definitions.append(ast.StructDef(
            template.hidden_name,
            tuple(wrapper_fields),
            "private",
            (),
            span,
        ))
        definitions.append(ast.NativeBuildDef(
            tuple(str(value) for value in template.headers),
            tuple(str(value) for value in template.sources),
            tuple(str(value) for value in template.include_dirs),
            tuple(str(value) for value in template.lib_dirs),
            template.libraries,
            template.cflags,
            template.ldflags,
            span,
        ))
        return definitions

    def c_module_initializer(self, template: NativeTemplate, span) -> ast.StructExpr:
        digest = template.hidden_name.removeprefix("__zlcm_").split("__", 1)[0]
        return ast.StructExpr(
            span,
            template.hidden_name,
            tuple(
                ast.StructFieldValue(
                    function.name,
                    ast.NameExpr(span, f"__zlcm_{digest}_fn_{function.name}"),
                    span,
                )
                for function in template.functions
            ),
        )

    def expand_c_modules(self, program: ast.Program, owner: Path) -> ast.Program:
        aliases = {item.alias for item in program.imports if item.path == "std::c_module"}
        generated: list[ast.Definition] = []

        def reject_type(node: ast.TypeNode) -> ast.TypeNode:
            alias = self.is_c_module_type(node, aliases)
            if alias is not None:
                raise CompileError(
                    f"{alias}::Module is dependent on c_module::load and is only valid on a directly initialized local or field",
                    node.span,
                )
            if isinstance(node, ast.NamedTypeNode):
                return ast.NamedTypeNode(node.span, node.name, tuple(reject_type(arg) for arg in node.args))
            if isinstance(node, ast.FunctionTypeNode):
                return ast.FunctionTypeNode(
                    node.span,
                    tuple(reject_type(item) for item in node.params),
                    reject_type(node.return_type) if node.return_type else None,
                )
            if isinstance(node, ast.ReferenceTypeNode):
                return ast.ReferenceTypeNode(node.span, reject_type(node.inner) if node.inner else None, node.mutable)
            if isinstance(node, ast.OptionalTypeNode):
                return ast.OptionalTypeNode(node.span, reject_type(node.inner) if node.inner else None)
            if isinstance(node, ast.TupleTypeNode):
                return ast.TupleTypeNode(node.span, tuple(reject_type(item) for item in node.items))
            return node

        def expression(node: ast.Expr) -> ast.Expr:
            load = self.c_module_load_alias(node)
            if load is not None:
                alias, _ = load
                if alias not in aliases:
                    raise CompileError(
                        f"`{alias}::load` requires `import std::c_module as {alias}`",
                        node.span,
                    )
                raise CompileError(
                    "c_module::load must directly initialize a local or struct/class field typed as the same alias::Module",
                    node.span,
                )
            if isinstance(node, ast.UnaryExpr):
                return replace(node, operand=expression(node.operand))
            if isinstance(node, ast.BinaryExpr):
                return replace(node, left=expression(node.left), right=expression(node.right))
            if isinstance(node, ast.FStringExpr):
                return replace(node, parts=tuple(expression(item) if isinstance(item, ast.Expr) else item for item in node.parts))
            if isinstance(node, ast.FieldExpr):
                return replace(node, receiver=expression(node.receiver))
            if isinstance(node, ast.IndexExpr):
                return replace(node, receiver=expression(node.receiver), index=expression(node.index))
            if isinstance(node, ast.CallExpr):
                return replace(node, callee=expression(node.callee), args=tuple(expression(item) for item in node.args))
            if isinstance(node, ast.TupleExpr):
                return replace(node, items=tuple(expression(item) for item in node.items))
            if isinstance(node, ast.ListExpr):
                return replace(node, items=tuple(expression(item) for item in node.items))
            if isinstance(node, ast.StructExpr):
                return replace(node, fields=tuple(replace(item, value=expression(item.value)) for item in node.fields))
            if isinstance(node, ast.CatchExpr):
                return replace(node, value=expression(node.value), handler=block(node.handler))
            if isinstance(node, ast.SpawnExpr):
                return replace(node, call=expression(node.call))
            if isinstance(node, ast.AwaitExpr):
                return replace(node, task=expression(node.task))
            if isinstance(node, ast.TypeOfExpr):
                return replace(node, value=expression(node.value), target_type=reject_type(node.target_type))
            if isinstance(node, ast.CastExpr):
                return replace(node, target_type=reject_type(node.target_type), value=expression(node.value))
            if isinstance(node, ast.BorrowExpr):
                return replace(node, value=expression(node.value))
            if isinstance(node, ast.ClosureExpr):
                return replace(
                    node,
                    params=tuple(replace(item, type_node=reject_type(item.type_node)) for item in node.params),
                    return_type=reject_type(node.return_type),
                    body=block(node.body),
                )
            if isinstance(node, ast.TypeApplyExpr):
                return replace(node, callee=expression(node.callee), type_args=tuple(reject_type(item) for item in node.type_args))
            if isinstance(node, ast.AssociatedExpr):
                return replace(node, receiver=expression(node.receiver))
            return node

        def declaration(type_node: ast.TypeNode | None, value: ast.Expr, span):
            type_alias = self.is_c_module_type(type_node, aliases) if type_node is not None else None
            load = self.c_module_load_alias(value)
            if type_alias is None and load is None:
                return reject_type(type_node) if type_node else None, expression(value)
            if load is not None and load[0] not in aliases:
                raise CompileError(
                    f"`{load[0]}::load` requires `import std::c_module as {load[0]}`",
                    span,
                )
            if type_alias is None:
                raise CompileError("c_module::load requires an explicit alias::Module declaration type", span)
            if load is None:
                raise CompileError("c_module::Module requires a direct c_module::load(\"template.zlcm.h\") initializer", span)
            load_alias, call = load
            if load_alias != type_alias:
                raise CompileError(
                    f"c_module type alias `{type_alias}` and load alias `{load_alias}` must match",
                    span,
                )
            template = self.template_for_load(call, owner, aliases)
            generated.extend(self.c_module_definitions(template, span))
            return ast.NamedTypeNode(type_node.span, template.hidden_name), self.c_module_initializer(template, value.span)

        def statement(node: ast.Stmt) -> ast.Stmt:
            if isinstance(node, ast.LetStmt):
                if len(node.bindings) == 1:
                    binding = node.bindings[0]
                    typ, value = declaration(binding.type_node, node.value, node.span)
                    return replace(node, bindings=(replace(binding, type_node=typ),), value=value)
                return replace(
                    node,
                    bindings=tuple(replace(item, type_node=reject_type(item.type_node) if item.type_node else None) for item in node.bindings),
                    value=expression(node.value),
                )
            if isinstance(node, ast.ReturnStmt):
                return replace(node, values=tuple(expression(item) for item in node.values))
            if isinstance(node, ast.StopStmt):
                return replace(node, value=expression(node.value))
            if isinstance(node, ast.RecoverStmt):
                return replace(node, values=tuple(expression(item) for item in node.values))
            if isinstance(node, ast.IfStmt):
                return replace(node, condition=expression(node.condition), then_block=block(node.then_block), else_block=block(node.else_block) if node.else_block else None)
            if isinstance(node, ast.IfLetStmt):
                return replace(node, value=expression(node.value), then_block=block(node.then_block), else_block=block(node.else_block) if node.else_block else None)
            if isinstance(node, ast.WhileStmt):
                return replace(node, condition=expression(node.condition), body=block(node.body))
            if isinstance(node, ast.AssignStmt):
                return replace(node, target=expression(node.target), value=expression(node.value))
            if isinstance(node, ast.DeferStmt):
                call = expression(node.call)
                assert isinstance(call, ast.CallExpr)
                return replace(node, call=call)
            if isinstance(node, ast.ExprStmt):
                return replace(node, value=expression(node.value))
            return node

        def block(node: ast.Block) -> ast.Block:
            return replace(node, statements=tuple(statement(item) for item in node.statements))

        rewritten: list[ast.Definition] = []
        for definition in program.definitions:
            if isinstance(definition, ast.StructDef):
                fields = []
                for field in definition.fields:
                    if field.default is None:
                        fields.append(replace(field, type_node=reject_type(field.type_node)))
                    else:
                        typ, default = declaration(field.type_node, field.default, field.span)
                        fields.append(replace(field, type_node=typ, default=default))
                rewritten.append(replace(definition, fields=tuple(fields)))
            elif isinstance(definition, ast.ClassDef):
                fields = []
                for field in definition.fields:
                    if field.default is None:
                        fields.append(replace(field, type_node=reject_type(field.type_node)))
                    else:
                        typ, default = declaration(field.type_node, field.default, field.span)
                        fields.append(replace(field, type_node=typ, default=default))
                methods = tuple(replace(
                    method,
                    params=tuple(replace(param, type_node=reject_type(param.type_node), default=expression(param.default) if param.default else None) for param in method.params),
                    return_type=reject_type(method.return_type),
                    body=block(method.body),
                    throws=reject_type(method.throws) if method.throws else None,
                ) for method in definition.methods)
                initializer = definition.initializer
                if initializer:
                    initializer = replace(
                        initializer,
                        params=tuple(replace(param, type_node=reject_type(param.type_node), default=expression(param.default) if param.default else None) for param in initializer.params),
                        body=block(initializer.body),
                        throws=reject_type(initializer.throws) if initializer.throws else None,
                    )
                deinitializer = definition.deinitializer
                if deinitializer:
                    deinitializer = replace(deinitializer, body=block(deinitializer.body))
                rewritten.append(replace(definition, fields=tuple(fields), methods=methods, initializer=initializer, deinitializer=deinitializer))
            elif isinstance(definition, ast.FunctionDef):
                rewritten.append(replace(
                    definition,
                    params=tuple(replace(param, type_node=reject_type(param.type_node), default=expression(param.default) if param.default else None) for param in definition.params),
                    return_type=reject_type(definition.return_type),
                    body=block(definition.body),
                    receiver=replace(definition.receiver, type_node=reject_type(definition.receiver.type_node)) if definition.receiver else None,
                    throws=reject_type(definition.throws) if definition.throws else None,
                ))
            elif isinstance(definition, ast.NativeFunctionDef):
                rewritten.append(replace(
                    definition,
                    params=tuple(replace(param, type_node=reject_type(param.type_node), default=expression(param.default) if param.default else None) for param in definition.params),
                    return_type=reject_type(definition.return_type),
                    throws=reject_type(definition.throws) if definition.throws else None,
                ))
            else:
                rewritten.append(definition)
        return ast.Program(program.imports, tuple(generated + rewritten))


def namespace_program(program: ast.Program, namespace: str, import_aliases: set[str] | None = None) -> ast.Program:
    import_aliases = import_aliases or set()
    local_types = {
        item.name
        for item in program.definitions
        if isinstance(item, (ast.StructDef, ast.ClassDef))
    }
    local_functions = {
        item.name
        for item in program.definitions
        if (
            isinstance(item, ast.NativeFunctionDef)
            or (isinstance(item, ast.FunctionDef) and item.receiver is None)
        )
    }
    shadowed_names: set[str] = set()

    def compiler_global(name: str) -> bool:
        return name.startswith("__zlcm_")

    def type_node(node: ast.TypeNode) -> ast.TypeNode:
        if isinstance(node, ast.NamedTypeNode):
            first = node.name.split("::", 1)[0]
            if (node.name in local_types and not compiler_global(node.name)) or first in import_aliases:
                name = f"{namespace}::{node.name}"
            else:
                name = node.name
            return ast.NamedTypeNode(node.span, name, tuple(type_node(arg) for arg in node.args))
        if isinstance(node, ast.TupleTypeNode):
            return ast.TupleTypeNode(node.span, tuple(type_node(item) for item in node.items))
        if isinstance(node, ast.OptionalTypeNode):
            return ast.OptionalTypeNode(node.span, type_node(node.inner) if node.inner else None)
        if isinstance(node, ast.FunctionTypeNode):
            return ast.FunctionTypeNode(
                node.span,
                tuple(type_node(item) for item in node.params),
                type_node(node.return_type) if node.return_type else None,
            )
        if isinstance(node, ast.ReferenceTypeNode):
            return ast.ReferenceTypeNode(
                node.span,
                type_node(node.inner) if node.inner else None,
                node.mutable,
            )
        return node

    def expression(node: ast.Expr) -> ast.Expr:
        nonlocal shadowed_names
        if isinstance(node, ast.NameExpr):
            if (
                (
                    (node.name in local_functions or node.name in local_types)
                    and node.name not in shadowed_names
                    and not compiler_global(node.name)
                )
                or node.name in import_aliases
            ):
                name = f"{namespace}::{node.name}"
            else:
                name = node.name
            return ast.NameExpr(node.span, name)
        if isinstance(node, ast.PathExpr):
            first = node.parts[0]
            parts = (
                (namespace, *node.parts)
                if first in import_aliases or (
                    first in local_types and first not in shadowed_names and not compiler_global(first)
                )
                else node.parts
            )
            return ast.PathExpr(node.span, tuple(parts))
        if isinstance(node, ast.TypeApplyExpr):
            return ast.TypeApplyExpr(
                node.span,
                expression(node.callee),
                tuple(type_node(arg) for arg in node.type_args),
            )
        if isinstance(node, ast.AssociatedExpr):
            return ast.AssociatedExpr(node.span, expression(node.receiver), node.name)
        if isinstance(node, ast.BorrowExpr):
            return ast.BorrowExpr(node.span, expression(node.value), node.mutable)
        if isinstance(node, ast.ClosureExpr):
            previous_shadowed = shadowed_names
            shadowed_names = set(previous_shadowed)
            shadowed_names.update(param.name for param in node.params)
            try:
                params = tuple(
                    ast.Param(
                        param.name,
                        type_node(param.type_node),
                        param.span,
                        param.mutable,
                        None,
                    )
                    for param in node.params
                )
                return ast.ClosureExpr(
                    node.span,
                    params,
                    type_node(node.return_type),
                    block(node.body),
                )
            finally:
                shadowed_names = previous_shadowed
        if isinstance(node, ast.UnaryExpr):
            return ast.UnaryExpr(node.span, node.operator, expression(node.operand))
        if isinstance(node, ast.BinaryExpr):
            return ast.BinaryExpr(node.span, expression(node.left), node.operator, expression(node.right))
        if isinstance(node, ast.FStringExpr):
            return ast.FStringExpr(
                node.span,
                tuple(expression(item) if isinstance(item, ast.Expr) else item for item in node.parts),
            )
        if isinstance(node, ast.FieldExpr):
            return ast.FieldExpr(node.span, expression(node.receiver), node.name)
        if isinstance(node, ast.IndexExpr):
            return ast.IndexExpr(node.span, expression(node.receiver), expression(node.index))
        if isinstance(node, ast.CallExpr):
            if (
                isinstance(node.callee, ast.NameExpr)
                and node.callee.name in local_functions
                and node.callee.name not in shadowed_names
                and not compiler_global(node.callee.name)
            ):
                callee: ast.Expr = ast.NameExpr(node.callee.span, f"{namespace}::{node.callee.name}")
            else:
                callee = expression(node.callee)
            return ast.CallExpr(node.span, callee, tuple(expression(arg) for arg in node.args))
        if isinstance(node, ast.TupleExpr):
            return ast.TupleExpr(node.span, tuple(expression(item) for item in node.items))
        if isinstance(node, ast.ListExpr):
            return ast.ListExpr(node.span, tuple(expression(item) for item in node.items))
        if isinstance(node, ast.StructExpr):
            first = node.name.split("::", 1)[0]
            name = (
                f"{namespace}::{node.name}"
                if (node.name in local_types and not compiler_global(node.name)) or first in import_aliases
                else node.name
            )
            fields = tuple(ast.StructFieldValue(item.name, expression(item.value), item.span) for item in node.fields)
            return ast.StructExpr(node.span, name, fields)
        if isinstance(node, ast.CatchExpr):
            return ast.CatchExpr(
                node.span,
                expression(node.value),
                node.error_name,
                block(node.handler, (node.error_name,)),
            )
        if isinstance(node, ast.SpawnExpr):
            return ast.SpawnExpr(node.span, expression(node.call))
        if isinstance(node, ast.AwaitExpr):
            return ast.AwaitExpr(node.span, expression(node.task))
        if isinstance(node, ast.TypeOfExpr):
            return ast.TypeOfExpr(node.span, expression(node.value), type_node(node.target_type))
        if isinstance(node, ast.CastExpr):
            return ast.CastExpr(node.span, type_node(node.target_type), expression(node.value))
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
                block(node.then_block, (node.binding,)),
                block(node.else_block) if node.else_block else None,
            )
        if isinstance(node, ast.WhileStmt):
            return ast.WhileStmt(node.span, expression(node.condition), block(node.body))
        if isinstance(node, ast.AssignStmt):
            return ast.AssignStmt(node.span, expression(node.target), expression(node.value))
        if isinstance(node, ast.DeferStmt):
            call = expression(node.call)
            assert isinstance(call, ast.CallExpr)
            return ast.DeferStmt(node.span, call)
        if isinstance(node, ast.ExprStmt):
            return ast.ExprStmt(node.span, expression(node.value))
        return node

    def block(node: ast.Block, initial_names: tuple[str, ...] = ()) -> ast.Block:
        nonlocal shadowed_names
        previous_shadowed = shadowed_names
        shadowed_names = set(previous_shadowed)
        shadowed_names.update(initial_names)
        statements: list[ast.Stmt] = []
        try:
            for item in node.statements:
                statements.append(statement(item))
                if isinstance(item, ast.LetStmt):
                    shadowed_names.update(binding.name for binding in item.bindings)
            return ast.Block(tuple(statements), node.span)
        finally:
            shadowed_names = previous_shadowed

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
                    definition.name if compiler_global(definition.name) else f"{namespace}::{definition.name}",
                    fields,
                    definition.visibility,
                    definition.type_params,
                    definition.span,
                    definition.native_name,
                )
            )
        elif isinstance(definition, ast.ClassDef):
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
            methods: list[ast.ClassMethodDef] = []
            for method in definition.methods:
                previous_shadowed = shadowed_names
                shadowed_names = {"this", *(param.name for param in method.params)}
                params = tuple(
                    ast.Param(
                        param.name,
                        type_node(param.type_node),
                        param.span,
                        param.mutable,
                        expression(param.default) if param.default else None,
                    )
                    for param in method.params
                )
                methods.append(
                    ast.ClassMethodDef(
                        method.name,
                        params,
                        type_node(method.return_type),
                        block(method.body),
                        method.visibility,
                        method.span,
                        method.mutable,
                        method.static,
                        type_node(method.throws) if method.throws else None,
                    )
                )
                shadowed_names = previous_shadowed
            initializer = None
            if definition.initializer is not None:
                item = definition.initializer
                previous_shadowed = shadowed_names
                shadowed_names = {"this", *(param.name for param in item.params)}
                params = tuple(
                    ast.Param(
                        param.name,
                        type_node(param.type_node),
                        param.span,
                        param.mutable,
                        expression(param.default) if param.default else None,
                    )
                    for param in item.params
                )
                initializer = ast.ClassInitDef(
                    params,
                    block(item.body),
                    item.visibility,
                    item.span,
                    type_node(item.throws) if item.throws else None,
                )
                shadowed_names = previous_shadowed
            deinitializer = None
            if definition.deinitializer is not None:
                previous_shadowed = shadowed_names
                shadowed_names = {"this"}
                deinitializer = ast.ClassDeinitDef(
                    block(definition.deinitializer.body),
                    definition.deinitializer.span,
                )
                shadowed_names = previous_shadowed
            definitions.append(
                ast.ClassDef(
                    f"{namespace}::{definition.name}",
                    fields,
                    tuple(methods),
                    initializer,
                    deinitializer,
                    definition.visibility,
                    definition.type_params,
                    definition.span,
                )
            )
        elif isinstance(definition, ast.FunctionDef):
            previous_shadowed = shadowed_names
            shadowed_names = {param.name for param in definition.params}
            if definition.receiver:
                shadowed_names.add(definition.receiver.name)
            receiver = None
            if definition.receiver:
                receiver = ast.Param(
                    definition.receiver.name,
                    type_node(definition.receiver.type_node),
                    definition.receiver.span,
                    definition.receiver.mutable,
                    None,
                )
            params = tuple(
                ast.Param(
                    param.name,
                    type_node(param.type_node),
                    param.span,
                    param.mutable,
                    expression(param.default) if param.default else None,
                )
                for param in definition.params
            )
            definitions.append(
                ast.FunctionDef(
                    name=definition.name if receiver is not None else f"{namespace}::{definition.name}",
                    params=params,
                    return_type=type_node(definition.return_type),
                    body=block(definition.body),
                    visibility=definition.visibility,
                    span=definition.span,
                    receiver=receiver,
                    type_params=definition.type_params,
                    throws=type_node(definition.throws) if definition.throws else None,
                    exported=definition.exported,
                )
            )
            shadowed_names = previous_shadowed
        elif isinstance(definition, ast.NativeFunctionDef):
            previous_shadowed = shadowed_names
            shadowed_names = {param.name for param in definition.params}
            params = tuple(
                ast.Param(
                    param.name,
                    type_node(param.type_node),
                    param.span,
                    param.mutable,
                    expression(param.default) if param.default else None,
                )
                for param in definition.params
            )
            definitions.append(
                ast.NativeFunctionDef(
                    name=(definition.name if compiler_global(definition.name) else f"{namespace}::{definition.name}"),
                    params=params,
                    return_type=type_node(definition.return_type),
                    symbol=definition.symbol,
                    visibility=definition.visibility,
                    span=definition.span,
                    throws=type_node(definition.throws) if definition.throws else None,
                )
            )
            shadowed_names = previous_shadowed
        else:
            definitions.append(definition)
    return ast.Program((), tuple(definitions))


def load_program(path: Path, source_override: str | None = None) -> ast.Program:
    return ModuleLoader().load(path, source_override)
