from __future__ import annotations

import json

from . import ast
from .diagnostics import CompileError, SourceSpan
from .lexer import Token, lex
from .types import BUILTIN_NAMES


PRECEDENCE = {
    "||": 1,
    "&&": 2,
    "==": 3,
    "!=": 3,
    "<": 4,
    "<=": 4,
    ">": 4,
    ">=": 4,
    "+": 5,
    "-": 5,
    "*": 6,
    "/": 6,
    "%": 6,
}

CAST_OPERAND_STARTS = {
    "INT",
    "FLOAT",
    "STRING",
    "FSTRING",
    "TRUE",
    "FALSE",
    "NULL",
    "IDENT",
    "LIST_LEN__",
    "LIST_GET__",
    "LIST_POP__",
    "LIST_CLEAR__",
    "LIST_PUSH__",
    "LIST_SET__",
    "CLONE__",
    "CLONE_REF__",
    "REF_SET__",
    "PRINT_CMD__",
    "STR_TO_LIST__",
    "STR_LEN__",
    "STR_BYTE_LEN__",
    "STR_GET__",
    "STR_SLICE__",
    "(",
    "[",
    "SPAWN",
    "AWAIT",
    "TYPEOF__",
    "&",
}


class Parser:
    def __init__(self, tokens: list[Token], source_name: str = "<source>") -> None:
        self.tokens = tokens
        self.source_name = source_name
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def previous(self) -> Token:
        return self.tokens[self.index - 1]

    def at(self, kind: str) -> bool:
        return self.current.kind == kind

    def advance(self) -> Token:
        token = self.current
        if token.kind != "EOF":
            self.index += 1
        return token

    def match(self, *kinds: str) -> Token | None:
        if self.current.kind in kinds:
            return self.advance()
        return None

    def expect(self, kind: str, message: str | None = None) -> Token:
        if not self.at(kind):
            wanted = message or f"expected `{kind}`, got `{self.current.value or self.current.kind}`"
            raise CompileError(wanted, self.current.span, self.source_name)
        return self.advance()

    def skip_newlines(self) -> None:
        while self.match("NEWLINE"):
            pass

    def require_statement_end(self) -> None:
        if self.at("NEWLINE"):
            self.skip_newlines()
            return
        if self.at("}") or self.at("EOF"):
            return
        raise CompileError(
            "expected a newline after the statement",
            self.current.span,
            self.source_name,
        )

    def parse_program(self) -> ast.Program:
        imports: list[ast.ImportDef] = []
        definitions: list[ast.Definition] = []
        self.skip_newlines()
        while not self.at("EOF"):
            if token := self.match("IMPORT"):
                imports.append(self.parse_import(token.span))
                self.require_statement_end()
                continue
            definitions.append(self.parse_definition())
            self.skip_newlines()
        return ast.Program(tuple(imports), tuple(definitions))

    def parse_import(self, start: SourceSpan) -> ast.ImportDef:
        if self.at("<") or self.at("STRING"):
            raise CompileError(
                "ZyenLang 0.3 imports use `import std::module as alias` or `import crate::module as alias`",
                self.current.span,
                self.source_name,
            )
        if (
            self.at("IDENT")
            and self.current.value == "c_module"
            and self.index + 2 < len(self.tokens)
            and self.tokens[self.index + 1].kind == "."
            and self.tokens[self.index + 2].kind == "IDENT"
            and self.tokens[self.index + 2].value == "load"
        ):
            raise CompileError(
                "`import c_module.load(\"...\") as name` was removed; write "
                "`import std::c_module as c_module` and initialize "
                "`let name: c_module::Module = c_module::load(\"...\")`",
                self.current.span,
                self.source_name,
            )
        parts = [self.expect("IDENT", "import expects a module path such as `std::io`").value]
        while self.match("::"):
            parts.append(self.expect("IDENT", "expected a module name after `::`").value)
        if len(parts) < 2:
            raise CompileError("import paths require a root and module name", start, self.source_name)
        self.expect("AS", "ZyenLang 0.3 imports require `as alias`")
        alias = self.expect("IDENT", "expected an import alias").value
        return ast.ImportDef("::".join(parts), alias, False, start)

    def parse_import_path_part(self, message: str) -> str:
        values = [self.expect("IDENT", message).value]
        while self.match("-"):
            values.append(self.expect("IDENT", "expected a name after `-`").value)
        return "-".join(values)

    def parse_visibility(self) -> ast.Visibility:
        if self.match("PUBLIC"):
            return "public"
        if self.match("PRIVATE"):
            return "private"
        return "private"

    def parse_definition(self) -> ast.Definition:
        exported = self.match("EXPORT") is not None
        visibility = self.parse_visibility()
        if exported:
            visibility = "public"
        if self.match("STRUCT"):
            if exported:
                raise CompileError("only functions can be exported through the C ABI", self.previous().span, self.source_name)
            return self.parse_struct(visibility, self.previous().span)
        if self.match("CLASS"):
            if exported:
                raise CompileError("classes cannot be exported directly through the C ABI", self.previous().span, self.source_name)
            return self.parse_class(visibility, self.previous().span)
        if self.match("FN"):
            return self.parse_function(visibility, self.previous().span, exported=exported)
        if self.match("NATIVE"):
            if exported:
                raise CompileError("native declarations cannot be re-exported", self.previous().span, self.source_name)
            return self.parse_native(visibility, self.previous().span)
        raise CompileError(
            "top-level declarations must be `struct`, `class`, `fn`, or `native`",
            self.current.span,
            self.source_name,
        )

    def parse_native(self, visibility: ast.Visibility, start: SourceSpan) -> ast.Definition:
        if self.match("FN"):
            name = self.expect("IDENT", "expected a native function name")
            self.expect("(", "expected `(` after native function name")
            params: list[ast.Param] = []
            self.skip_newlines()
            while not self.at(")"):
                params.append(self.parse_param(allow_default=True))
                self.skip_newlines()
                if not self.match(","):
                    break
                self.skip_newlines()
            self.expect(")", "expected `)` after native parameters")
            self.skip_newlines()
            return_type = self.parse_type()
            self.skip_newlines()
            throws = None
            if self.match("THROWS"):
                self.skip_newlines()
                throws = self.parse_type()
                self.skip_newlines()
            self.expect("=", "native function requires `= \"c_symbol\"`")
            symbol = self.expect("STRING", "native function requires a quoted C symbol")
            return ast.NativeFunctionDef(
                name.value,
                tuple(params),
                return_type,
                symbol.value,
                visibility,
                start,
                throws,
            )

        directive = self.expect("IDENT", "expected `source` or `link` after `native`")
        if directive.value == "source":
            path = self.expect("STRING", "native source requires a quoted relative path")
            return ast.NativeSourceDef(path.value, start)
        if directive.value == "link":
            platform = "all"
            if self.at("IDENT") and self.current.value in {"windows", "linux", "macos"}:
                platform = self.advance().value
            library = self.expect("STRING", "native link requires a quoted library name")
            return ast.NativeLinkDef(platform, library.value, start)
        raise CompileError("native directive must be `source`, `link`, or `fn`", directive.span, self.source_name)

    def parse_type_params(self) -> tuple[str, ...]:
        if not self.match("<"):
            return ()
        names: list[str] = []
        self.skip_newlines()
        while not self.at(">"):
            names.append(self.expect("IDENT", "expected a generic type parameter").value)
            self.skip_newlines()
            if not self.match(","):
                break
            self.skip_newlines()
        self.expect(">", "expected `>` after generic type parameters")
        if len(set(names)) != len(names):
            raise CompileError("duplicate generic type parameter", self.previous().span, self.source_name)
        return tuple(names)

    def parse_struct(self, visibility: ast.Visibility, start: SourceSpan) -> ast.StructDef:
        name = self.expect("IDENT", "expected a struct name").value
        type_params = self.parse_type_params()
        self.skip_newlines()
        self.expect("{", "expected `{` after struct name")
        fields: list[ast.FieldDef] = []
        self.skip_newlines()
        while not self.at("}"):
            field_visibility = self.parse_visibility()
            if self.at("FN") or self.at("MUT") or self.at("STATIC") or self.at("INIT") or self.at("DEINIT"):
                raise CompileError(
                    "struct is pure data in ZyenLang 0.3; move behavior to a module function or class",
                    self.current.span,
                    self.source_name,
                )
            self.match("LET")
            if self.at("IDENT") and self.current.value == "this":
                self.advance()
                self.expect(".", "expected `.` after `this` in a field declaration")
            field_token = self.expect("IDENT", "expected a field name")
            self.expect(":", "expected `:` after field name")
            type_node = self.parse_type()
            default = None
            if self.match("="):
                default = self.parse_expression()
            fields.append(ast.FieldDef(field_token.value, type_node, field_visibility, field_token.span, default))
            self.require_statement_end()
        self.expect("}")
        return ast.StructDef(name, tuple(fields), visibility, type_params, start)

    def parse_class(self, visibility: ast.Visibility, start: SourceSpan) -> ast.ClassDef:
        name = self.expect("IDENT", "expected a class name").value
        type_params = self.parse_type_params()
        self.skip_newlines()
        self.expect("{", "expected `{` after class name")
        fields: list[ast.FieldDef] = []
        methods: list[ast.ClassMethodDef] = []
        initializer: ast.ClassInitDef | None = None
        deinitializer: ast.ClassDeinitDef | None = None
        self.skip_newlines()
        while not self.at("}"):
            member_visibility = self.parse_visibility()
            if token := self.match("INIT"):
                if initializer is not None:
                    raise CompileError("class can define only one init block", token.span, self.source_name)
                params, throws, body = self.parse_callable_tail("init")
                initializer = ast.ClassInitDef(tuple(params), body, member_visibility, token.span, throws)
                self.skip_newlines()
                continue
            if token := self.match("DEINIT"):
                if member_visibility == "public":
                    raise CompileError("deinit is always private", token.span, self.source_name)
                if deinitializer is not None:
                    raise CompileError("class can define only one deinit block", token.span, self.source_name)
                self.skip_newlines()
                deinitializer = ast.ClassDeinitDef(self.parse_block(), token.span)
                self.skip_newlines()
                continue

            mutable = self.match("MUT") is not None
            static = self.match("STATIC") is not None
            if mutable and static:
                raise CompileError("static functions cannot be marked mut", self.previous().span, self.source_name)
            if token := self.match("FN"):
                method_name = self.expect("IDENT", "expected a class method name")
                if self.at("<"):
                    raise CompileError("class methods cannot declare additional generic parameters", method_name.span, self.source_name)
                params, return_type, throws, body = self.parse_named_callable_tail(method_name)
                methods.append(
                    ast.ClassMethodDef(
                        method_name.value,
                        tuple(params),
                        return_type,
                        body,
                        member_visibility,
                        token.span,
                        mutable,
                        static,
                        throws,
                    )
                )
                self.skip_newlines()
                continue
            if mutable or static:
                raise CompileError("mut/static in a class must be followed by fn", self.current.span, self.source_name)

            self.match("LET")
            field_token = self.expect("IDENT", "expected a class field or method")
            self.expect(":", "expected `:` after class field name")
            type_node = self.parse_type()
            default = None
            if self.match("="):
                default = self.parse_expression()
            fields.append(ast.FieldDef(field_token.value, type_node, member_visibility, field_token.span, default))
            self.require_statement_end()
        self.expect("}")
        return ast.ClassDef(
            name,
            tuple(fields),
            tuple(methods),
            initializer,
            deinitializer,
            visibility,
            type_params,
            start,
        )

    def parse_callable_tail(self, label: str) -> tuple[list[ast.Param], ast.TypeNode | None, ast.Block]:
        self.expect("(", f"expected `(` after {label}")
        params: list[ast.Param] = []
        self.skip_newlines()
        while not self.at(")"):
            params.append(self.parse_param(allow_default=True))
            self.skip_newlines()
            if not self.match(","):
                break
            self.skip_newlines()
        self.expect(")", f"expected `)` after {label} parameters")
        self.skip_newlines()
        throws = None
        if self.match("THROWS"):
            self.skip_newlines()
            throws = self.parse_type()
            self.skip_newlines()
        return params, throws, self.parse_block()

    def parse_named_callable_tail(
        self,
        name: Token,
    ) -> tuple[list[ast.Param], ast.TypeNode, ast.TypeNode | None, ast.Block]:
        self.expect("(", "expected `(` after method name")
        params: list[ast.Param] = []
        self.skip_newlines()
        while not self.at(")"):
            params.append(self.parse_param(allow_default=True))
            self.skip_newlines()
            if not self.match(","):
                break
            self.skip_newlines()
        self.expect(")", "expected `)` after method parameters")
        self.skip_newlines()
        return_type: ast.TypeNode
        if self.at("{") or self.at("THROWS"):
            return_type = ast.NamedTypeNode(name.span, "void")
        else:
            return_type = self.parse_type()
            self.skip_newlines()
        throws = None
        if self.match("THROWS"):
            self.skip_newlines()
            throws = self.parse_type()
            self.skip_newlines()
        return params, return_type, throws, self.parse_block()

    def parse_function(self, visibility: ast.Visibility, start: SourceSpan, *, exported: bool = False) -> ast.FunctionDef:
        receiver: ast.Param | None = None
        if self.match("("):
            raise CompileError(
                "receiver functions were removed in ZyenLang 0.3; place methods inside a class",
                start,
                self.source_name,
            )

        name_token = self.expect("IDENT", "expected a function name")
        type_params = self.parse_type_params()
        self.skip_newlines()
        self.expect("(", "expected `(` after function name")
        params: list[ast.Param] = []
        self.skip_newlines()
        while not self.at(")"):
            params.append(self.parse_param(allow_default=True))
            self.skip_newlines()
            if not self.match(","):
                break
            self.skip_newlines()
        self.expect(")", "expected `)` after parameters")
        self.skip_newlines()

        if self.at("{") or self.at("THROWS"):
            return_type: ast.TypeNode = ast.NamedTypeNode(name_token.span, "void")
        else:
            return_type = self.parse_type()
            self.skip_newlines()

        throws = None
        if self.match("THROWS"):
            self.skip_newlines()
            throws = self.parse_type()
            self.skip_newlines()

        body = self.parse_block()
        return ast.FunctionDef(
            name=name_token.value,
            params=tuple(params),
            return_type=return_type,
            body=body,
            visibility=visibility,
            span=start,
            receiver=receiver,
            type_params=type_params,
            throws=throws,
            exported=exported,
        )

    def parse_param(self, *, allow_default: bool = False) -> ast.Param:
        mutable = self.match("MUT") is not None
        name = self.expect("IDENT", "expected a parameter name")
        self.expect(":", "expected `:` after parameter name")
        type_node = self.parse_type()
        default = None
        if self.match("="):
            if not allow_default:
                raise CompileError("method receivers cannot have default values", name.span, self.source_name)
            default = self.parse_expression()
        return ast.Param(name.value, type_node, name.span, mutable, default)

    def parse_type(self) -> ast.TypeNode:
        self.skip_newlines()
        start = self.current.span
        if self.match("&&"):
            raise CompileError(
                "references cannot point to references; nested `&&T` is not allowed",
                start,
                self.source_name,
            )
        if self.match("&"):
            mutable = self.match("MUT") is not None
            self.skip_newlines()
            node = ast.ReferenceTypeNode(start, self.parse_type(), mutable)
        elif self.match("FN"):
            self.expect("(", "expected `(` after `fn` in function type")
            self.skip_newlines()
            params: list[ast.TypeNode] = []
            while not self.at(")"):
                params.append(self.parse_type())
                self.skip_newlines()
                if not self.match(","):
                    break
                self.skip_newlines()
            self.expect(")", "expected `)` after function type parameters")
            self.skip_newlines()
            if self.match("->"):
                raise CompileError(
                    "ZyenLang 0.3 function types use `fn(P...) R` without `->`",
                    self.previous().span,
                    self.source_name,
                )
            node: ast.TypeNode = ast.FunctionTypeNode(start, tuple(params), self.parse_type())
        elif self.match("("):
            self.skip_newlines()
            items: list[ast.TypeNode] = []
            if not self.at(")"):
                while True:
                    items.append(self.parse_type())
                    self.skip_newlines()
                    if not self.match(","):
                        break
                    self.skip_newlines()
            self.expect(")", "expected `)` after tuple type")
            if len(items) < 2:
                raise CompileError("tuple types need at least two members", start, self.source_name)
            node = ast.TupleTypeNode(start, tuple(items))
        else:
            first = self.expect("IDENT", "expected a type")
            name_parts = [first.value]
            while self.match("::"):
                name_parts.append(self.expect("IDENT", "expected a type name after `::`").value)
            if self.at("."):
                raise CompileError(
                    "ZyenLang 0.3 type paths use `::`, not `.`",
                    self.current.span,
                    self.source_name,
                )
            args: list[ast.TypeNode] = []
            if self.match("<"):
                self.skip_newlines()
                while not self.at(">"):
                    args.append(self.parse_type())
                    self.skip_newlines()
                    if not self.match(","):
                        break
                    self.skip_newlines()
                self.expect(">", "expected `>` after generic type arguments")
            node = ast.NamedTypeNode(start, "::".join(name_parts), tuple(args))

        if self.match("|"):
            self.skip_newlines()
            if not self.match("NULL"):
                raise CompileError(
                    "ZyenLang 0.3 permits only optional unions written `T | null`",
                    self.current.span,
                    self.source_name,
                )
            node = ast.OptionalTypeNode(start, node)
        return node

    def parse_block(self) -> ast.Block:
        start = self.expect("{", "expected `{` to start a block").span
        statements: list[ast.Stmt] = []
        self.skip_newlines()
        while not self.at("}"):
            if self.at("EOF"):
                raise CompileError("unterminated block", start, self.source_name)
            statements.append(self.parse_statement())
            self.skip_newlines()
        self.expect("}")
        return ast.Block(tuple(statements), start)

    def parse_statement(self) -> ast.Stmt:
        if token := self.match("LET"):
            statement = self.parse_let(token.span)
            self.require_statement_end()
            return statement
        if token := self.match("RETURN"):
            values = self.parse_expression_list_to_statement_end()
            self.require_statement_end()
            return ast.ReturnStmt(token.span, tuple(values))
        if token := self.match("STOP"):
            value = self.parse_expression()
            self.require_statement_end()
            return ast.StopStmt(token.span, value)
        if token := self.match("RECOVER"):
            values = self.parse_expression_list_to_statement_end()
            self.require_statement_end()
            return ast.RecoverStmt(token.span, tuple(values))
        if token := self.match("IF"):
            if self.match("LET"):
                binding = self.expect("IDENT", "if let requires a binding name")
                self.expect("=", "if let requires `=` before the optional value")
                value = self.parse_expression(allow_struct_literal=False)
                self.skip_newlines()
                then_block = self.parse_block()
                self.skip_newlines()
                else_block = self.parse_optional_else_block()
                return ast.IfLetStmt(token.span, binding.value, value, then_block, else_block)
            condition = self.parse_expression(allow_struct_literal=False)
            self.skip_newlines()
            then_block = self.parse_block()
            self.skip_newlines()
            else_block = None
            if self.match("ELSE"):
                self.skip_newlines()
                if nested_if := self.match("IF"):
                    nested = self.parse_if_after_keyword(nested_if.span)
                    else_block = ast.Block((nested,), nested.span)
                else:
                    else_block = self.parse_block()
            return ast.IfStmt(token.span, condition, then_block, else_block)
        if token := self.match("WHILE"):
            condition = self.parse_expression(allow_struct_literal=False)
            self.skip_newlines()
            return ast.WhileStmt(token.span, condition, self.parse_block())
        if token := self.match("BREAK"):
            self.require_statement_end()
            return ast.BreakStmt(token.span)
        if token := self.match("CONTINUE"):
            self.require_statement_end()
            return ast.ContinueStmt(token.span)
        if token := self.match("FREE__", "SKIP__"):
            replacement = "DROP__" if token.kind == "FREE__" else "lexical scope"
            raise CompileError(
                f"`{token.value}` was removed in ZyenLang 0.3; use {replacement}",
                token.span,
                self.source_name,
            )
        if token := self.match("DROP__"):
            self.expect("(", f"{token.value} requires `(`")
            name = self.expect("IDENT", f"{token.value} expects one local variable")
            self.expect(")", f"expected `)` after {token.value} local")
            self.require_statement_end()
            return ast.DropStmt(token.span, name.value)
        if token := self.match("DEFER"):
            value = self.parse_expression()
            if not isinstance(value, ast.CallExpr):
                raise CompileError("defer requires a function or class method call", value.span, self.source_name)
            self.require_statement_end()
            return ast.DeferStmt(token.span, value)

        start = self.current.span
        value = self.parse_expression()
        if assignment := self.match("=", "+=", "-=", "*=", "/=", "%="):
            if not isinstance(value, (ast.NameExpr, ast.FieldExpr)):
                raise CompileError("assignment target must be a local or struct field", value.span, self.source_name)
            assigned = self.parse_expression()
            if assignment.kind != "=":
                assigned = ast.BinaryExpr(value.span, value, assignment.kind[0], assigned)
            self.require_statement_end()
            return ast.AssignStmt(start, value, assigned)
        self.require_statement_end()
        return ast.ExprStmt(start, value)

    def parse_optional_else_block(self) -> ast.Block | None:
        if not self.match("ELSE"):
            return None
        self.skip_newlines()
        return self.parse_block()

    def parse_if_after_keyword(self, start: SourceSpan) -> ast.IfStmt:
        condition = self.parse_expression(allow_struct_literal=False)
        self.skip_newlines()
        then_block = self.parse_block()
        self.skip_newlines()
        else_block = None
        if self.match("ELSE"):
            self.skip_newlines()
            if nested_if := self.match("IF"):
                nested = self.parse_if_after_keyword(nested_if.span)
                else_block = ast.Block((nested,), nested.span)
            else:
                else_block = self.parse_block()
        return ast.IfStmt(start, condition, then_block, else_block)

    def parse_let(self, start: SourceSpan) -> ast.LetStmt:
        bindings: list[ast.Binding] = []
        if self.match("("):
            self.skip_newlines()
            while not self.at(")"):
                bindings.append(self.parse_binding())
                self.skip_newlines()
                if not self.match(","):
                    break
                self.skip_newlines()
            self.expect(")", "expected `)` after destructuring bindings")
            if len(bindings) < 2:
                raise CompileError("destructuring requires at least two bindings", start, self.source_name)
        else:
            bindings.append(self.parse_binding())
        self.skip_newlines()
        self.expect("=", "let declarations require an initializer")
        self.skip_newlines()
        return ast.LetStmt(start, tuple(bindings), self.parse_expression())

    def parse_binding(self) -> ast.Binding:
        token = self.expect("IDENT", "expected a variable name")
        type_node = None
        if self.match(":"):
            type_node = self.parse_type()
        return ast.Binding(token.value, type_node, token.span)

    def parse_expression_list_to_statement_end(self) -> list[ast.Expr]:
        if self.at("NEWLINE") or self.at("}") or self.at("EOF"):
            return []
        values = [self.parse_expression()]
        while self.match(","):
            self.skip_newlines()
            values.append(self.parse_expression())
        return values

    def parse_expression(self, min_precedence: int = 0, *, allow_struct_literal: bool = True) -> ast.Expr:
        expression = self.parse_unary(allow_struct_literal=allow_struct_literal)
        while True:
            operator = self.current.kind
            precedence = PRECEDENCE.get(operator)
            if precedence is None or precedence < min_precedence:
                break
            self.advance()
            right = self.parse_expression(precedence + 1, allow_struct_literal=allow_struct_literal)
            expression = ast.BinaryExpr(expression.span, expression, operator, right)
        return expression

    def parse_unary(self, *, allow_struct_literal: bool) -> ast.Expr:
        cast = self.try_parse_cast(allow_struct_literal=allow_struct_literal)
        if cast is not None:
            return cast
        if token := self.match("!", "-"):
            return ast.UnaryExpr(token.span, token.kind, self.parse_unary(allow_struct_literal=allow_struct_literal))
        if token := self.match("&"):
            mutable = self.match("MUT") is not None
            self.skip_newlines()
            return ast.BorrowExpr(
                token.span,
                self.parse_unary(allow_struct_literal=allow_struct_literal),
                mutable,
            )
        if token := self.match("SPAWN"):
            value = self.parse_postfix(allow_struct_literal=allow_struct_literal)
            if not isinstance(value, ast.CallExpr):
                raise CompileError("spawn requires a direct function or method call", token.span, self.source_name)
            return ast.SpawnExpr(token.span, value)
        if token := self.match("AWAIT"):
            return ast.AwaitExpr(token.span, self.parse_unary(allow_struct_literal=allow_struct_literal))
        if token := self.match("TYPEOF__"):
            self.expect("(", "TYPEOF__ now uses `TYPEOF__(value, Type)`")
            self.skip_newlines()
            value = self.parse_expression()
            self.skip_newlines()
            self.expect(",", "TYPEOF__ requires a comma before its type")
            self.skip_newlines()
            target_type = self.parse_type()
            self.skip_newlines()
            self.expect(")", "expected `)` after TYPEOF__ type")
            return ast.TypeOfExpr(token.span, value, target_type)
        return self.parse_postfix(allow_struct_literal=allow_struct_literal)

    def try_parse_cast(self, *, allow_struct_literal: bool) -> ast.CastExpr | None:
        if not self.at("("):
            return None
        checkpoint = self.index
        start = self.advance().span
        try:
            target_type = self.parse_type()
            self.skip_newlines()
            if not self.match(")"):
                self.index = checkpoint
                return None
            builtin_unary = (
                isinstance(target_type, ast.NamedTypeNode)
                and target_type.name in BUILTIN_NAMES
                and not target_type.args
                and self.current.kind in {"!", "-"}
            )
            if self.current.kind not in CAST_OPERAND_STARTS and not builtin_unary:
                self.index = checkpoint
                return None
        except CompileError:
            self.index = checkpoint
            return None
        value = self.parse_unary(allow_struct_literal=allow_struct_literal)
        return ast.CastExpr(start, target_type, value)

    def parse_postfix(self, *, allow_struct_literal: bool) -> ast.Expr:
        expression = self.parse_primary(allow_struct_literal=allow_struct_literal)
        while True:
            type_apply = self.try_parse_type_apply(expression)
            if type_apply is not None:
                expression = type_apply
                continue
            if self.match("."):
                name = self.expect("IDENT", "expected a field or method name after `.`")
                expression = ast.FieldExpr(expression.span, expression, name.value)
                continue
            if token := self.match("::"):
                if isinstance(expression, ast.TypeApplyExpr):
                    name = self.expect("IDENT", "expected a static function name after `::`")
                    expression = ast.AssociatedExpr(token.span, expression, name.value)
                    continue
                raise CompileError(
                    "associated paths must be written as one path before applying type arguments",
                    token.span,
                    self.source_name,
                )
            if token := self.match("["):
                self.skip_newlines()
                index = self.parse_expression()
                self.skip_newlines()
                self.expect("]", "expected `]` after index expression")
                expression = ast.IndexExpr(token.span, expression, index)
                continue
            if self.match("("):
                args: list[ast.Expr] = []
                self.skip_newlines()
                while not self.at(")"):
                    args.append(self.parse_expression())
                    self.skip_newlines()
                    if not self.match(","):
                        break
                    self.skip_newlines()
                self.expect(")", "expected `)` after call arguments")
                expression = ast.CallExpr(expression.span, expression, tuple(args))
                continue
            if token := self.match("CATCH"):
                self.skip_newlines()
                error = self.expect("IDENT", "catch requires an error binding")
                self.skip_newlines()
                handler = self.parse_block()
                expression = ast.CatchExpr(token.span, expression, error.value, handler)
                continue
            break
        return expression

    def try_parse_type_apply(self, expression: ast.Expr) -> ast.TypeApplyExpr | None:
        if not self.at("<") or not isinstance(expression, (ast.NameExpr, ast.PathExpr)):
            return None
        checkpoint = self.index
        self.advance()
        args: list[ast.TypeNode] = []
        try:
            self.skip_newlines()
            while not self.at(">"):
                args.append(self.parse_type())
                self.skip_newlines()
                if not self.match(","):
                    break
                self.skip_newlines()
            self.expect(">", "expected `>` after type arguments")
            if self.at("{"):
                raise CompileError(
                    "generic struct literals are not supported in ZyenLang 0.3; use a generic class constructor",
                    expression.span,
                    self.source_name,
                )
            if not self.at("(") and not self.at("::"):
                self.index = checkpoint
                return None
        except CompileError as exc:
            if exc.message.startswith("generic struct literals"):
                raise
            self.index = checkpoint
            return None
        return ast.TypeApplyExpr(expression.span, expression, tuple(args))

    def parse_primary(self, *, allow_struct_literal: bool) -> ast.Expr:
        if token := self.match("FN"):
            self.expect("(", "closure requires `(` after `fn`")
            self.skip_newlines()
            params: list[ast.Param] = []
            while not self.at(")"):
                params.append(self.parse_param())
                self.skip_newlines()
                if not self.match(","):
                    break
                self.skip_newlines()
            self.expect(")", "expected `)` after closure parameters")
            self.skip_newlines()
            return_type = self.parse_type()
            self.skip_newlines()
            body = self.parse_block()
            return ast.ClosureExpr(token.span, tuple(params), return_type, body)
        if token := self.match("INT"):
            return ast.IntExpr(token.span, int(token.value))
        if token := self.match("FLOAT"):
            return ast.FloatExpr(token.span, token.value)
        if token := self.match("STRING"):
            return ast.StringExpr(token.span, token.value)
        if token := self.match("FSTRING"):
            return self.parse_fstring(token)
        if token := self.match("TRUE", "FALSE"):
            return ast.BoolExpr(token.span, token.kind == "TRUE")
        if token := self.match("NULL"):
            return ast.NullExpr(token.span)
        if token := self.match(
            "LIST_LEN__",
            "LIST_GET__",
            "LIST_PUSH__",
            "LIST_SET__",
            "LIST_POP__",
            "LIST_CLEAR__",
            "PRINT_CMD__",
            "STR_TO_LIST__",
            "STR_LEN__",
            "STR_BYTE_LEN__",
            "STR_GET__",
            "STR_SLICE__",
            "CLONE__",
            "CLONE_REF__",
            "REF_SET__",
        ):
            self.expect("(", f"{token.value} requires `(`")
            args: list[ast.Expr] = []
            self.skip_newlines()
            while not self.at(")"):
                args.append(self.parse_expression())
                self.skip_newlines()
                if not self.match(","):
                    break
                self.skip_newlines()
            self.expect(")", f"expected `)` after {token.value} arguments")
            return ast.CallExpr(token.span, ast.NameExpr(token.span, token.value), tuple(args))
        if token := self.match("IDENT"):
            parts = [token.value]
            while self.match("::"):
                parts.append(self.expect("IDENT", "expected a path segment after `::`").value)
            if allow_struct_literal and self.at("{"):
                qualified = Token("IDENT", "::".join(parts), token.span)
                return self.parse_struct_literal(qualified)
            if len(parts) == 1:
                return ast.NameExpr(token.span, token.value)
            return ast.PathExpr(token.span, tuple(parts))
        if token := self.match("("):
            self.skip_newlines()
            first = self.parse_expression()
            self.skip_newlines()
            if self.match(","):
                items = [first]
                self.skip_newlines()
                while not self.at(")"):
                    items.append(self.parse_expression())
                    self.skip_newlines()
                    if not self.match(","):
                        break
                    self.skip_newlines()
                self.expect(")", "expected `)` after tuple expression")
                return ast.TupleExpr(token.span, tuple(items))
            self.expect(")", "expected `)` after expression")
            return first
        if token := self.match("["):
            items: list[ast.Expr] = []
            self.skip_newlines()
            while not self.at("]"):
                items.append(self.parse_expression())
                self.skip_newlines()
                if not self.match(","):
                    break
                self.skip_newlines()
            self.expect("]", "expected `]` after list literal")
            return ast.ListExpr(token.span, tuple(items))
        raise CompileError("expected an expression", self.current.span, self.source_name)

    def parse_fstring(self, token: Token) -> ast.FStringExpr:
        raw = token.value
        parts: list[str | ast.Expr] = []
        text: list[str] = []
        index = 0

        def flush_text() -> None:
            if not text:
                return
            encoded = '"' + "".join(text) + '"'
            try:
                parts.append(json.loads(encoded))
            except json.JSONDecodeError as exc:
                raise CompileError(f"invalid f-string escape: {exc.msg}", token.span, self.source_name) from exc
            text.clear()

        while index < len(raw):
            if raw.startswith("{{", index):
                text.append("{")
                index += 2
                continue
            if raw.startswith("}}", index):
                text.append("}")
                index += 2
                continue
            if raw[index] != "{":
                text.append(raw[index])
                index += 1
                continue

            flush_text()
            expression_start = index + 1
            index = expression_start
            depth = 1
            in_string = False
            escaped = False
            while index < len(raw) and depth > 0:
                current = raw[index]
                if in_string:
                    if current == '"' and not escaped:
                        in_string = False
                    escaped = current == "\\" and not escaped
                    if current != "\\":
                        escaped = False
                elif current == '"':
                    in_string = True
                elif current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                    if depth == 0:
                        break
                index += 1
            expression_source = raw[expression_start:index]
            if not expression_source.strip():
                raise CompileError("f-string interpolation cannot be empty", token.span, self.source_name)
            nested_tokens = lex(expression_source, self.source_name)
            column_base = token.span.column + 2 + expression_start
            nested_tokens = [
                Token(
                    item.kind,
                    item.value,
                    SourceSpan(token.span.line, column_base + item.span.column - 1, self.source_name),
                )
                for item in nested_tokens
            ]
            nested = Parser(nested_tokens, self.source_name)
            value = nested.parse_expression()
            nested.skip_newlines()
            if not nested.at("EOF"):
                raise CompileError("unexpected token in f-string interpolation", nested.current.span, self.source_name)
            parts.append(value)
            index += 1
        flush_text()
        return ast.FStringExpr(token.span, tuple(parts))

    def looks_like_generic_struct_literal(self) -> bool:
        if not self.at("<"):
            return False
        checkpoint = self.index
        found = False
        try:
            self.advance()
            self.skip_newlines()
            while not self.at(">"):
                self.parse_type()
                self.skip_newlines()
                if not self.match(","):
                    break
                self.skip_newlines()
            self.expect(">")
            found = self.at("{")
        except CompileError:
            found = False
        self.index = checkpoint
        return found

    def looks_like_qualified_struct_literal(self) -> bool:
        index = self.index
        if index >= len(self.tokens) or self.tokens[index].kind != ".":
            return False
        while index < len(self.tokens) and self.tokens[index].kind == ".":
            index += 1
            if index >= len(self.tokens) or self.tokens[index].kind != "IDENT":
                return False
            index += 1
        return index < len(self.tokens) and self.tokens[index].kind == "{"

    def parse_struct_literal(self, name: Token) -> ast.StructExpr:
        self.expect("{")
        fields: list[ast.StructFieldValue] = []
        self.skip_newlines()
        while not self.at("}"):
            field = self.expect("IDENT", "expected a field name in struct literal")
            self.expect(":", "expected `:` after struct field name")
            self.skip_newlines()
            value = self.parse_expression()
            fields.append(ast.StructFieldValue(field.value, value, field.span))
            self.skip_newlines()
            if not self.match(","):
                break
            self.skip_newlines()
        self.expect("}", "expected `}` after struct literal")
        return ast.StructExpr(name.span, name.value, tuple(fields))


def parse(source: str, source_name: str = "<source>") -> ast.Program:
    return Parser(lex(source, source_name), source_name).parse_program()
