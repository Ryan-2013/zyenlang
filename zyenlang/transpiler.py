"""Compatibility alias for the ZyenLang v1 transpiler."""

import sys as _sys

from .v1 import transpiler as _implementation


if __name__ == "__main__":
    raise SystemExit(_implementation.main())

_sys.modules[__name__] = _implementation
