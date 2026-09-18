from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import __version__
from .artifacts import ArtifactBuilder, emit_single_file
from .compiler import Compiler, CompilerOptions
from .diagnostics import CompileError, render_error
from .modules import MAX_SOURCE_BYTES
from .package_manager import (
    GitDependency,
    PackageError,
    PathDependency,
    add_dependency,
    add_dependency_spec,
    fetch_project,
    find_project_root,
    init_project,
    load_manifest,
    new_project,
    remove_dependency,
)
from zyenlang.cli.doctor import add_subparser as add_doctor_subparser, handle as handle_doctor


def read_stdin_source(source_name: str, limit: int = MAX_SOURCE_BYTES) -> str:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    value = stream.read(limit + 1)
    raw = value.encode("utf-8") if isinstance(value, str) else value
    if len(raw) > limit:
        raise CompileError(f"stdin source exceeds the {limit}-byte safety limit", source_name=source_name)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompileError("stdin source must be valid UTF-8", source_name=source_name) from exc


def _project_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="project directory or a path inside it",
    )


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zy", description="ZyenLang 0.3 compiler and project manager")
    parser.add_argument("--version", action="version", version=f"ZyenLang {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="create a new ZyenLang project directory")
    new.add_argument("path", type=Path)
    new.add_argument("--name")
    new.add_argument("--lib", action="store_true", help="create a C-source library project")

    init = commands.add_parser("init", help="initialize a project in an existing directory")
    init.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    init.add_argument("--name")
    init.add_argument("--lib", action="store_true")
    init.add_argument("--force", action="store_true")

    add = commands.add_parser("add", help="add a path or exact-revision Git dependency")
    _project_option(add)
    add.add_argument("dependency", help="dependency alias, or a local path when --path/--git is omitted")
    source = add.add_mutually_exclusive_group()
    source.add_argument("--path", type=Path)
    source.add_argument("--git")
    add.add_argument("--rev", help="exact Git revision or commit")

    remove = commands.add_parser("remove", help="remove a direct dependency")
    _project_option(remove)
    remove.add_argument("dependency")

    fetch = commands.add_parser("fetch", help="resolve dependencies and update zy.lock")
    _project_option(fetch)
    fetch.add_argument("--locked", action="store_true", help="use the existing lock without changing it")

    check = commands.add_parser("check", help="type-check a project target")
    _project_option(check)
    check.add_argument("target", nargs="?")
    check.add_argument("--file", type=Path, help="check one editor source file outside target selection")
    check.add_argument("--stdin", action="store_true", help="read unsaved --file text from stdin")
    check.add_argument("--library", action="store_true", help="do not require main for --file")

    build = commands.add_parser("build", help="build a project target")
    _project_option(build)
    build.add_argument("target", nargs="?")
    build.add_argument("--release", action="store_true")
    build.add_argument("--out-dir", type=Path)

    run = commands.add_parser("run", help="build and run a bin target")
    _project_option(run)
    run.add_argument("target", nargs="?")
    run.add_argument("--release", action="store_true")
    run.add_argument("--out-dir", type=Path)

    test = commands.add_parser("test", help="run project test targets or tests/*.zy")
    _project_option(test)
    test.add_argument("--release", action="store_true")

    clean = commands.add_parser("clean", help="remove only files recorded by the artifact manifest")
    _project_option(clean)
    clean.add_argument("target", nargs="?")

    metadata = commands.add_parser("metadata", help="print machine-readable project metadata")
    _project_option(metadata)

    emit = commands.add_parser("emit", help="emit one source file without a project target")
    emit.add_argument("--file", type=Path, required=True)
    emit.add_argument("--kind", choices=("c",), required=True)
    emit.add_argument("--out-dir", type=Path, required=True)
    emit.add_argument("--output-name")

    add_doctor_subparser(commands)
    return parser


def _root(path: Path) -> Path:
    result = find_project_root(path)
    assert result is not None
    return result


def _handle_add(args: argparse.Namespace) -> int:
    root = _root(args.project)
    if args.git is not None:
        if not args.rev:
            raise PackageError("Git dependencies require --rev")
        lock = add_dependency_spec(root, args.dependency, GitDependency(args.git, args.rev))
        print(f"added {args.dependency} at {args.rev} ({len(lock.packages)} locked package(s))")
        return 0
    if args.rev:
        raise PackageError("--rev is valid only together with --git")
    if args.path is not None:
        path = args.path if args.path.is_absolute() else Path.cwd() / args.path
        lock = add_dependency_spec(root, args.dependency, PathDependency(str(path.resolve())))
        print(f"added {args.dependency} from {path.resolve()} ({len(lock.packages)} locked package(s))")
        return 0
    path = Path(args.dependency)
    path = path if path.is_absolute() else Path.cwd() / path
    lock = add_dependency(root, path)
    dependency = load_manifest(path.resolve())
    print(f"added {dependency.name} from {path.resolve()} ({len(lock.packages)} locked package(s))")
    return 0


def _handle_test(args: argparse.Namespace) -> int:
    root = _root(args.project)
    manifest = load_manifest(root)
    target_names = [name for name in manifest.targets if name == "test" or name.startswith("test-")]
    if target_names:
        for name in target_names:
            code = ArtifactBuilder(root, release=args.release).run(name)
            if code != 0:
                return code
        print(f"OK: {len(target_names)} test target(s)")
        return 0
    sources = sorted((root / "tests").rglob("*.zy")) if (root / "tests").is_dir() else []
    if not sources:
        raise PackageError("no test target and no tests/*.zy files were found")
    compiler = Compiler(CompilerOptions(release=args.release, require_main=True))
    for source in sources:
        code = compiler.run_file(source)
        if code != 0:
            return code
        print(f"OK: {source.relative_to(root)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    program_args: list[str] = []
    if arguments and arguments[0] == "pkg":
        print(
            render_error(
                "zy pkg was removed in ZyenLang 0.3; use `zy add`, `zy remove`, or `zy fetch`",
                sys.stderr,
            ),
            file=sys.stderr,
        )
        return 2
    if arguments and arguments[0] == "build" and (
        "-o" in arguments
        or "--output" in arguments
        or any(value.endswith(".zy") for value in arguments[1:] if not value.startswith("-"))
    ):
        print(
            render_error(
                "single-file build was removed; use a project target or `zy emit --file source.zy --kind c --out-dir PATH`",
                sys.stderr,
            ),
            file=sys.stderr,
        )
        return 2
    if arguments and arguments[0] == "run" and "--" in arguments:
        separator = arguments.index("--")
        program_args = arguments[separator + 1 :]
        arguments = arguments[:separator]

    parser = make_parser()
    args = parser.parse_args(arguments)
    try:
        if args.command == "doctor":
            return handle_doctor(args)
        if args.command == "new":
            manifest = new_project(args.path, args.name, library=args.lib)
            print(f"created {manifest.name} at {manifest.root}")
            return 0
        if args.command == "init":
            manifest = init_project(args.path, args.name, force=args.force, library=args.lib)
            print(f"initialized {manifest.name} at {manifest.root}")
            return 0
        if args.command == "add":
            return _handle_add(args)
        if args.command == "remove":
            lock = remove_dependency(_root(args.project), args.dependency)
            print(f"removed {args.dependency} ({len(lock.packages)} locked package(s))")
            return 0
        if args.command == "fetch":
            lock = fetch_project(_root(args.project), locked=args.locked)
            print(f"fetched {len(lock.packages)} package(s)")
            return 0
        if args.command == "check":
            if args.stdin and args.file is None:
                raise PackageError("--stdin requires --file")
            if args.file is not None:
                compiler = Compiler(CompilerOptions(require_main=not args.library))
                if args.stdin:
                    compiler.check_file_source(args.file, read_stdin_source(str(args.file)))
                else:
                    compiler.check_file(args.file)
                print(f"OK: {args.file}")
            else:
                program = ArtifactBuilder(args.project).check(args.target)
                print(f"OK: {args.target or load_manifest(_root(args.project)).build.default_target} ({len(program.functions)} functions)")
            return 0
        if args.command == "build":
            result = ArtifactBuilder(args.project, release=args.release).build(
                args.target,
                out_dir=args.out_dir,
            )
            print(result.primary)
            return 0
        if args.command == "run":
            return ArtifactBuilder(args.project, release=args.release).run(
                args.target,
                program_args=program_args,
                out_dir=args.out_dir,
            )
        if args.command == "test":
            return _handle_test(args)
        if args.command == "clean":
            removed = ArtifactBuilder(args.project).clean(args.target)
            print(f"removed {len(removed)} artifact(s)")
            return 0
        if args.command == "metadata":
            print(json.dumps(ArtifactBuilder(args.project).metadata(), indent=2, sort_keys=True))
            return 0
        if args.command == "emit":
            result = emit_single_file(args.file, args.out_dir, output_name=args.output_name)
            print(result.primary)
            return 0
    except CompileError as exc:
        print(render_error(exc, sys.stderr), file=sys.stderr)
        return 1
    except PackageError as exc:
        print(render_error(f"zy: {exc}", sys.stderr), file=sys.stderr)
        return 2
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(render_error(f"zy: {exc}", sys.stderr), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
