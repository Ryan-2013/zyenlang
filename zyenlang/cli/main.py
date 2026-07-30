from __future__ import annotations

import sys

from zyenlang.transpiler import main as legacy_main
from zyenlang.v2.package_manager import main as package_main


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "pkg":
        return package_main(arguments[1:], prog="zy pkg")
    return legacy_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
