"""Compatibility alias for the ZyenLang v1 native module loader."""

import sys as _sys

from .v1 import c_module as _implementation

_sys.modules[__name__] = _implementation
