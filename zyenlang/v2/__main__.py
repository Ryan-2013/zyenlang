from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

from . import __version__
from .compiler import Compiler, CompilerOptions
from .diagnostics import CompileError, render_error
from .modules import MAX_SOURCE_BYTES
from .package_manager import PackageError, configure_parser as configure_package_parser, handle_command as handle_package_command


def read_stdin_source(source_name: str, limit: int = MAX_SOURCE_BYTES) -> str:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    value = stream.read(limit + 1)
    if isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = value
    if len(raw) > limit:
        raise CompileError(f"stdin source exceeds the {limit}-byte safety limit", source_name=source_name)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompileError("stdin source must be valid UTF-8", source_name=source_name) from exc


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zy2", description="ZyenLang 0.2 compiler bootstrap")
    parser.add_argument("--version", action="version", version=f"ZyenLang {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="parse and type-check a source file")
    check.add_argument("input", type=Path)
    check.add_argument("--library", action="store_true", help="do not require a main entry point")
    check.add_argument("--stdin", action="store_true", help="check source text read from stdin using the input path for imports")

    build = subparsers.add_parser("build", help="build C source or a native executable")
    build.add_argument("input", type=Path)
    build.add_argument("-o", "--output", type=Path, required=True)
    build.add_argument("--release", action="store_true")

    run = subparsers.add_parser("run", help="build and run a source file")
    run.add_argument("input", type=Path)
    run.add_argument("--release", action="store_true")

    package = subparsers.add_parser("pkg", help="manage project dependencies")
    configure_package_parser(package)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.command == "pkg":
        try:
            return handle_package_command(args)
        except PackageError as exc:
            print(render_error(f"zy2 pkg: {exc}", sys.stderr), file=sys.stderr)
            return 2
    compiler = Compiler(
        CompilerOptions(
            release=getattr(args, "release", False),
            require_main=not getattr(args, "library", False),
        )
    )
    try:
        if args.command == "check":
            if args.stdin:
                compiler.check_file_source(args.input, read_stdin_source(str(args.input)))
            else:
                compiler.check_file(args.input)
            print(f"OK: {args.input}")
            return 0
        if args.command == "build":
            compiler.build_file(args.input, args.output)
            print(args.output)
            return 0
        if args.command == "run":
            return compiler.run_file(args.input)
    except CompileError as exc:
        print(render_error(exc, sys.stderr), file=sys.stderr)
        return 1
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(render_error(f"zy2: {exc}", sys.stderr), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
