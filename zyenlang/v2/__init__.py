"""ZyenLang 0.2 compiler pipeline.

Frontend nodes and typed IR do not contain C source fragments, so multiple
backends can consume the same checked program.
"""

from .compiler import Compiler, CompilerOptions
from .diagnostics import CompileError

__all__ = ["CompileError", "Compiler", "CompilerOptions"]

__version__ = "0.2.1"
