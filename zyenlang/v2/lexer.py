from __future__ import annotations

from dataclasses import dataclass
import json

from .diagnostics import CompileError, SourceSpan


KEYWORDS = {
    "as",
    "await",
    "break",
    "catch",
    "continue",
    "else",
    "false",
    "fn",
    "if",
    "import",
    "let",
    "LIST_LEN__",
    "LIST_PUSH__",
    "LIST_SET__",
    "mut",
    "native",
    "null",
    "private",
    "public",
    "recover",
    "return",
    "stop",
    "spawn",
    "struct",
    "throws",
    "true",
    "TYPEOF__",
    "while",
}

RENAMED_SPECIAL_WORDS = {
    "GET_ARGS": "GET_ARGS__",
    "GET_EXE": "GET_EXE__",
    "LEN": "LIST_LEN__",
    "LEN__": "LIST_LEN__",
    "PUSH": "LIST_PUSH__",
    "PUSH__": "LIST_PUSH__",
    "SET": "LIST_SET__",
    "SET__": "LIST_SET__",
    "typeof": "TYPEOF__",
}

SPECIAL_VALUE_WORDS = {"GET_ARGS__", "GET_EXE__"}

TWO_CHAR_SYMBOLS = {"==", "!=", "<=", ">=", "&&", "||", "->", "+=", "-=", "*=", "/=", "%="}
ONE_CHAR_SYMBOLS = set("{}()[],:.=+-*/%<>!|")


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    span: SourceSpan


def lex(source: str, source_name: str = "<source>") -> list[Token]:
    tokens: list[Token] = []
    i = 0
    line = 1
    column = 1

    def span() -> SourceSpan:
        return SourceSpan(line, column, source_name)

    while i < len(source):
        ch = source[i]

        if i == 0 and ch == "\ufeff":
            i += 1
            continue

        if ch in " \t\f\v":
            i += 1
            column += 1
            continue

        if ch == "\r":
            if i + 1 < len(source) and source[i + 1] == "\n":
                i += 1
            tokens.append(Token("NEWLINE", "\n", span()))
            i += 1
            line += 1
            column = 1
            continue

        if ch == "\n":
            tokens.append(Token("NEWLINE", "\n", span()))
            i += 1
            line += 1
            column = 1
            continue

        if source.startswith("//", i):
            while i < len(source) and source[i] not in "\r\n":
                i += 1
                column += 1
            continue

        if source.startswith("/*", i):
            start = span()
            i += 2
            column += 2
            while i < len(source) and not source.startswith("*/", i):
                if source[i] == "\n":
                    i += 1
                    line += 1
                    column = 1
                elif source[i] == "\r":
                    if i + 1 < len(source) and source[i + 1] == "\n":
                        i += 1
                    i += 1
                    line += 1
                    column = 1
                else:
                    i += 1
                    column += 1
            if i >= len(source):
                raise CompileError("unterminated block comment", start, source_name)
            i += 2
            column += 2
            continue

        if ch == ";":
            raise CompileError("semicolons were removed in ZyenLang 0.2", span(), source_name)

        if ch == '"':
            start = span()
            start_i = i
            i += 1
            column += 1
            escaped = False
            while i < len(source):
                current = source[i]
                if current in "\r\n":
                    raise CompileError("string literals cannot contain a raw newline", start, source_name)
                i += 1
                column += 1
                if current == '"' and not escaped:
                    break
                escaped = current == "\\" and not escaped
                if current != "\\":
                    escaped = False
            else:
                raise CompileError("unterminated string literal", start, source_name)
            raw = source[start_i:i]
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise CompileError(f"invalid string escape: {exc.msg}", start, source_name) from exc
            tokens.append(Token("STRING", value, start))
            continue

        if ch.isdigit():
            start = span()
            start_i = i
            while i < len(source) and (source[i].isdigit() or source[i] == "_"):
                i += 1
                column += 1
            value = source[start_i:i].replace("_", "")
            tokens.append(Token("INT", value, start))
            continue

        if ch.isalpha() or ch == "_":
            start = span()
            start_i = i
            while i < len(source) and (source[i].isalnum() or source[i] == "_"):
                i += 1
                column += 1
            value = source[start_i:i]
            if replacement := RENAMED_SPECIAL_WORDS.get(value):
                raise CompileError(f"`{value}` was renamed to `{replacement}`", start, source_name)
            if (
                value.endswith("__")
                and value.upper() == value
                and value not in KEYWORDS
                and value not in SPECIAL_VALUE_WORDS
            ):
                raise CompileError(
                    f"`{value}` is reserved for compiler special forms",
                    start,
                    source_name,
                )
            kind = value.upper() if value in KEYWORDS else "IDENT"
            tokens.append(Token(kind, value, start))
            continue

        pair = source[i:i + 2]
        if pair in TWO_CHAR_SYMBOLS:
            tokens.append(Token(pair, pair, span()))
            i += 2
            column += 2
            continue

        if ch in ONE_CHAR_SYMBOLS:
            tokens.append(Token(ch, ch, span()))
            i += 1
            column += 1
            continue

        raise CompileError(f"unexpected character `{ch}`", span(), source_name)

    tokens.append(Token("EOF", "", SourceSpan(line, column, source_name)))
    return tokens
