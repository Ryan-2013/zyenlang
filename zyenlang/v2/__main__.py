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
    GIT_COMMIT_RE,
    GitDependency,
    PackageError,
    PathDependency,
    add_dependency,
    add_dependency_spec,
    checkout_git,
    fetch_project,
    find_project_root,
    init_project,
    load_manifest,
    new_project,
    remove_dependency,
    resolve_registry_dependency,
    validate_lock,
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

    install = commands.add_parser("install", help="install a registry, local, or pinned Git package")
    _project_option(install)
    install.add_argument(
        "dependency",
        nargs="?",
        help="name[==version], local path, Git URL with --rev, or git+URL@COMMIT",
    )
    install.add_argument("--alias", help="local import name; defaults to the package name")
    install.add_argument("--rev", help="exact 40- or 64-hex Git commit")
    install.add_argument("--locked", action="store_true", help="install exactly from the existing zy.lock")

    uninstall = commands.add_parser("uninstall", help="remove an installed direct package")
    _project_option(uninstall)
    uninstall.add_argument("dependency")

    package_list = commands.add_parser("list", help="list installed packages")
    _project_option(package_list)

    show = commands.add_parser("show", help="show one installed package")
    _project_option(show)
    show.add_argument("dependency")

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


def _git_requirement(value: str, revision: str | None) -> tuple[str, str] | None:
    source = value
    selected_revision = revision
    if value.startswith("git+"):
        source, separator, embedded_revision = value[4:].rpartition("@")
        if not separator:
            raise PackageError("Git package syntax is `git+URL@FULL_COMMIT`")
        if selected_revision is not None and selected_revision != embedded_revision:
            raise PackageError("Git revision was provided twice with different values")
        selected_revision = embedded_revision
    elif revision is None:
        return None
    if selected_revision is None or not GIT_COMMIT_RE.fullmatch(selected_revision):
        raise PackageError("Git packages require an exact 40- or 64-hex commit")
    return source, selected_revision


def _handle_install(args: argparse.Namespace) -> int:
    root = _root(args.project)
    if args.dependency is None:
        if args.alias or args.rev:
            raise PackageError("--alias and --rev require a package argument")
        lock = fetch_project(root, locked=args.locked)
        print(f"installed {len(lock.packages)} package(s)")
        return 0
    if args.locked:
        raise PackageError("--locked cannot be combined with a package argument")

    git_requirement = _git_requirement(args.dependency, args.rev)
    if git_requirement is not None:
        source, revision = git_requirement
        checkout, commit = checkout_git(GitDependency(source, revision), locked_commit=revision)
        manifest = load_manifest(checkout)
        alias = args.alias or manifest.name.replace("-", "_")
        lock = add_dependency_spec(root, alias, GitDependency(source, commit))
        print(f"installed {manifest.name} {manifest.version} as {alias} ({len(lock.packages)} locked package(s))")
        return 0

    supplied_path = Path(args.dependency)
    path_like = (
        supplied_path.is_absolute()
        or args.dependency.startswith((".", "/", "\\"))
        or "/" in args.dependency
        or "\\" in args.dependency
    )
    path = supplied_path if supplied_path.is_absolute() else Path.cwd() / supplied_path
    if path_like:
        if not path.is_dir():
            raise PackageError(f"local package path not found: {path.resolve()}")
        manifest = load_manifest(path.resolve())
        alias = args.alias or manifest.name.replace("-", "_")
        lock = add_dependency(root, path, alias)
        print(f"installed {manifest.name} {manifest.version} as {alias} ({len(lock.packages)} locked package(s))")
        return 0

    registry = resolve_registry_dependency(args.dependency)
    checkout, commit = checkout_git(
        GitDependency(registry.git, registry.rev),
        locked_commit=registry.rev,
    )
    manifest = load_manifest(checkout)
    if manifest.name != registry.name or manifest.version != registry.version:
        raise PackageError(
            f"registry expected `{registry.name}` {registry.version}, "
            f"but the package declares `{manifest.name}` {manifest.version}"
        )
    alias = args.alias or manifest.name.replace("-", "_")
    lock = add_dependency_spec(root, alias, GitDependency(registry.git, commit))
    print(f"installed {manifest.name} {manifest.version} as {alias} ({len(lock.packages)} locked package(s))")
    return 0


def _handle_list(args: argparse.Namespace) -> int:
    root = _root(args.project)
    manifest = load_manifest(root)
    lock = validate_lock(root)
    records = lock.by_name()
    if not records:
        print("No packages installed.")
        return 0
    print("ALIAS\tPACKAGE\tVERSION\tSOURCE")
    for name in sorted(records):
        package = records[name]
        direct = "direct" if name in manifest.dependencies else "transitive"
        print(f"{name}\t{package.package_name}\t{package.version}\t{package.source_type} ({direct})")
    return 0


def _handle_show(args: argparse.Namespace) -> int:
    root = _root(args.project)
    lock = validate_lock(root)
    package = next(
        (
            item
            for item in lock.packages
            if item.name == args.dependency or item.package_name == args.dependency
        ),
        None,
    )
    if package is None:
        raise PackageError(f"package `{args.dependency}` is not installed")
    print(f"Name: {package.package_name}")
    print(f"Alias: {package.name}")
    print(f"Version: {package.version}")
    print(f"Source: {package.source_type} {package.source}")
    if package.revision:
        print(f"Revision: {package.revision}")
    print(f"Digest: {package.digest}")
    dependencies = ", ".join(package.dependencies) if package.dependencies else "<none>"
    print(f"Dependencies: {dependencies}")
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
        if args.command == "install":
            return _handle_install(args)
        if args.command == "uninstall":
            lock = remove_dependency(_root(args.project), args.dependency)
            print(f"uninstalled {args.dependency} ({len(lock.packages)} locked package(s))")
            return 0
        if args.command == "list":
            return _handle_list(args)
        if args.command == "show":
            return _handle_show(args)
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
