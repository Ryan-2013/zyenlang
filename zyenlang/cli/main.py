from __future__ import annotations

import sys

from zyenlang.v2.__main__ import main as compiler_main


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["version"]:
        arguments = ["--version"]
    return compiler_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
