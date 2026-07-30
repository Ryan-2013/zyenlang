from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpan:
    line: int
    column: int
    source_name: str = "<source>"


class CompileError(Exception):
    def __init__(self, message: str, span: SourceSpan | None = None, source_name: str = "<source>") -> None:
        self.message = message
        self.span = span
        self.source_name = span.source_name if span is not None else source_name
        if span is None:
            rendered = f"{self.source_name}: {message}"
        else:
            rendered = f"{self.source_name}:{span.line}:{span.column}: {message}"
        super().__init__(rendered)
