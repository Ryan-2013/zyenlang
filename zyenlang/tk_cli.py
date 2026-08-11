"""Compatibility command for the ZyenLang v1 Tk bridge."""

from .v1.tk_cli import *  # noqa: F401,F403
from .v1.tk_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
