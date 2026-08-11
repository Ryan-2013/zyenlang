"""Compatibility command for the ZyenLang v1 computer-vision helper."""

from .v1.cv_cli import *  # noqa: F401,F403
from .v1.cv_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
