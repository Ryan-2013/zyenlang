from __future__ import annotations

from dataclasses import dataclass
import os
from typing import TextIO


ANSI_RED = "\x1b[31m"
ANSI_RESET = "\x1b[0m"


def color_enabled(stream: TextIO) -> bool:
    if "NO_COLOR" in os.environ:
        return False
    setting = os.environ.get("ZYEN_COLOR", "auto").lower()
    if setting == "always":
        return True
    if setting == "never":
        return False
    try:
        return stream.isatty()
    except (AttributeError, OSError):
        return False


def render_error(value: object, stream: TextIO) -> str:
    message = str(value)
    if color_enabled(stream):
        return f"{ANSI_RED}{message}{ANSI_RESET}"
    return message


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
