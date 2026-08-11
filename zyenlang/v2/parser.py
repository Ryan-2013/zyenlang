from __future__ import annotations

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
    "STRING",
    "TRUE",
    "FALSE",
    "NULL",
    "IDENT",
    "LIST_LEN__",
    "LIST_PUSH__",
    "LIST_SET__",
    "(",
    "[",
    "SPAWN",
    "AWAIT",
    "TYPEOF__",
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
        if self.match("<"):
            parts = [self.parse_import_path_part("expected a package or standard-library module path")]
            while self.match("/"):
                parts.append(self.parse_import_path_part("expected a module name after `/`"))
            self.expect(">", "expected `>` after package import")
            path = "/".join(parts)
            is_angle = True
        elif token := self.match("STRING"):
            path = token.value
            is_angle = False
        else:
            raise CompileError("import expects `<package/module>` or a quoted relative path", self.current.span, self.source_name)
        alias = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if self.match("AS"):
            alias = self.expect("IDENT", "expected an import alias").value
        elif "-" in alias:
            raise CompileError("imports ending in a hyphenated name require `as alias`", start, self.source_name)
        return ast.ImportDef(path, alias, is_angle, start)

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
        return "public"

    def parse_definition(self) -> ast.Definition:
        visibility = self.parse_visibility()
        if self.match("STRUCT"):
            return self.parse_struct(visibility, self.previous().span)
        if self.match("FN"):
            return self.parse_function(visibility, self.previous().span)
        if self.match("NATIVE"):
            return self.parse_native(visibility, self.previous().span)
        raise CompileError(
            "top-level declarations must be `struct`, `fn`, or `native`",
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
                params.append(self.parse_param())
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

    def parse_function(self, visibility: ast.Visibility, start: SourceSpan) -> ast.FunctionDef:
        receiver: ast.Param | None = None
        if self.match("("):
            self.skip_newlines()
            receiver = self.parse_param()
            self.skip_newlines()
            self.expect(")", "receiver must contain exactly one typed binding")
            self.skip_newlines()

        name_token = self.expect("IDENT", "expected a function name")
        type_params = self.parse_type_params()
        self.skip_newlines()
        self.expect("(", "expected `(` after function name")
        params: list[ast.Param] = []
        self.skip_newlines()
        while not self.at(")"):
            params.append(self.parse_param())
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
        )

    def parse_param(self) -> ast.Param:
        mutable = self.match("MUT") is not None
        name = self.expect("IDENT", "expected a parameter name")
        self.expect(":", "expected `:` after parameter name")
        return ast.Param(name.value, self.parse_type(), name.span, mutable)

    def parse_type(self) -> ast.TypeNode:
        self.skip_newlines()
        start = self.current.span
        if self.match("("):
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
            node: ast.TypeNode = ast.TupleTypeNode(start, tuple(items))
        else:
            first = self.expect("IDENT", "expected a type")
            name_parts = [first.value]
            while self.match("."):
                name_parts.append(self.expect("IDENT", "expected a type name after `.`").value)
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
            node = ast.NamedTypeNode(start, ".".join(name_parts), tuple(args))

        if self.match("|"):
            self.skip_newlines()
            if not self.match("NULL"):
                raise CompileError(
                    "ZyenLang 0.2 currently permits only optional unions written `T | null`",
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
            if self.match("."):
                name = self.expect("IDENT", "expected a field or method name after `.`")
                expression = ast.FieldExpr(expression.span, expression, name.value)
                continue
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

    def parse_primary(self, *, allow_struct_literal: bool) -> ast.Expr:
        if token := self.match("INT"):
            return ast.IntExpr(token.span, int(token.value))
        if token := self.match("STRING"):
            return ast.StringExpr(token.span, token.value)
        if token := self.match("TRUE", "FALSE"):
            return ast.BoolExpr(token.span, token.kind == "TRUE")
        if token := self.match("NULL"):
            return ast.NullExpr(token.span)
        if token := self.match("LIST_LEN__", "LIST_PUSH__", "LIST_SET__"):
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
            if allow_struct_literal and self.looks_like_generic_struct_literal():
                raise CompileError(
                    "generic struct construction is scheduled after the bootstrap milestone",
                    token.span,
                    self.source_name,
                )
            if allow_struct_literal and self.looks_like_qualified_struct_literal():
                parts = [token.value]
                while self.match("."):
                    parts.append(self.expect("IDENT", "expected a struct name after `.`").value)
                qualified = Token("IDENT", ".".join(parts), token.span)
                return self.parse_struct_literal(qualified)
            if allow_struct_literal and self.at("{"):
                return self.parse_struct_literal(token)
            return ast.NameExpr(token.span, token.value)
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
