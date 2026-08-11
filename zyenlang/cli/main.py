from __future__ import annotations

import sys

from zyenlang.transpiler import main as legacy_main
from zyenlang.v2.__main__ import main as v2_main


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"legacy", "v1"}:
        return legacy_main(arguments[1:])
    if arguments == ["version"]:
        return v2_main(["--version"])
    return v2_main(arguments)


def legacy_entry(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    return legacy_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
