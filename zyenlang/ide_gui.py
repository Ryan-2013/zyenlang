"""Compatibility command for the ZyenLang v1 Tk IDE."""

from .v1.ide_gui import *  # noqa: F401,F403
from .v1.ide_gui import main


if __name__ == "__main__":
    raise SystemExit(main())
