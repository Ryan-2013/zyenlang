from __future__ import annotations

from typing import Protocol

from ..ir import IRProgram


class Backend(Protocol):
    name: str

    def emit(self, program: IRProgram) -> str:
        """Emit one backend artifact from verified typed IR."""
        ...
