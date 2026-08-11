from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from typing import Any

from .diagnostics import render_error

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[no-redef]


MANIFEST_NAME = "zyproject.toml"
LOCK_NAME = "zy.lock"
LOCK_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PACKAGE_FILES = 10_000
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_LOCK_PACKAGES = 1024
PACKAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
IGNORED_DIRECTORIES = {".git", ".hg", ".svn", "__pycache__"}
IGNORED_FILES = {LOCK_NAME, ".DS_Store"}


class PackageError(Exception):
    pass


@dataclass(frozen=True)
class PathDependency:
    path: str


@dataclass(frozen=True)
class PackageManifest:
    root: Path
    name: str
    version: str
    entry: str
    zyen: str
    dependencies: dict[str, PathDependency]


@dataclass(frozen=True)
class LockedPackage:
    name: str
    version: str
    source: str
    digest: str
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class LockFile:
    manifest_sha256: str
    root_dependencies: tuple[str, ...]
    packages: tuple[LockedPackage, ...]

    def by_name(self) -> dict[str, LockedPackage]:
        return {package.name: package for package in self.packages}


def zyen_home() -> Path:
    configured = os.environ.get("ZYEN_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".zyen"


def package_cache_root() -> Path:
    return zyen_home() / "packages" / "0.2"


def cache_path(digest: str) -> Path:
    return package_cache_root() / digest


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PackageError(f"missing {path.name}: {path}")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise PackageError(f"{path.name} exceeds the {MAX_MANIFEST_BYTES}-byte safety limit")
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise PackageError(f"{path.name} must be valid UTF-8") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PackageError(f"invalid {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"invalid {path.name}: expected a TOML table")
    return value


def _required_string(table: dict[str, Any], key: str, context: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PackageError(f"{context}.{key} must be a non-empty string")
    return value


def _validate_relative_file(value: str, field: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or ":" in part or "\x00" in part for part in path.parts)
    ):
        raise PackageError(f"{field} must stay inside the package root")
    if path.suffix != ".zy":
        raise PackageError(f"{field} must name a .zy source file")
    return value


def load_manifest(root: Path) -> PackageManifest:
    root = root.resolve()
    data = _load_toml(root / MANIFEST_NAME)
    package = data.get("package")
    if not isinstance(package, dict):
        raise PackageError(f"{MANIFEST_NAME} requires a [package] table")
    name = _required_string(package, "name", "package")
    version = _required_string(package, "version", "package")
    entry = package.get("entry", "src/lib.zy")
    zyen = package.get("zyen", ">=0.2.0")
    if not PACKAGE_NAME_RE.fullmatch(name):
        raise PackageError(f"invalid package name `{name}`; use lowercase letters, digits, `-`, or `_`")
    if not SEMVER_RE.fullmatch(version):
        raise PackageError(f"package.version must be semantic versioning, got `{version}`")
    if not isinstance(entry, str) or not entry:
        raise PackageError("package.entry must be a non-empty string")
    if not isinstance(zyen, str) or not zyen:
        raise PackageError("package.zyen must be a non-empty string")
    entry = _validate_relative_file(entry.replace("\\", "/"), "package.entry")

    raw_dependencies = data.get("dependencies", {})
    if not isinstance(raw_dependencies, dict):
        raise PackageError("[dependencies] must be a table")
    dependencies: dict[str, PathDependency] = {}
    for dependency_name, raw_spec in raw_dependencies.items():
        if not isinstance(dependency_name, str) or not PACKAGE_NAME_RE.fullmatch(dependency_name):
            raise PackageError(f"invalid dependency name `{dependency_name}`")
        if not isinstance(raw_spec, dict) or set(raw_spec) != {"path"} or not isinstance(raw_spec.get("path"), str):
            raise PackageError(
                f"dependency `{dependency_name}` must use {{ path = \"../package\" }}; registry and Git sources are not supported yet"
            )
        dependency_path = raw_spec["path"].strip()
        if not dependency_path or "\x00" in dependency_path:
            raise PackageError(f"dependency `{dependency_name}` has an empty path")
        dependencies[dependency_name] = PathDependency(dependency_path)
    return PackageManifest(root, name, version, entry, zyen, dependencies)


def find_project_root(start: Path, *, required: bool = True) -> Path | None:
    current = (start.parent if start.is_file() else start).resolve()
    for candidate in (current, *current.parents):
        if (candidate / MANIFEST_NAME).is_file():
            return candidate
    if required:
        raise PackageError(f"no {MANIFEST_NAME} found from {start}")
    return None


def manifest_sha256(root: Path) -> str:
    path = root / MANIFEST_NAME
    if not path.is_file():
        raise PackageError(f"missing {MANIFEST_NAME}: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_files(root: Path) -> list[tuple[Path, str, int]]:
    root = root.resolve()
    result: list[tuple[Path, str, int]] = []
    total_bytes = 0
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            path = current_path / name
            if path.is_symlink():
                raise PackageError(f"package contains a symlink: {path.relative_to(root).as_posix()}")
            if name not in IGNORED_DIRECTORIES:
                kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(file_names):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if name in IGNORED_FILES and "/" not in relative:
                continue
            if path.is_symlink():
                raise PackageError(f"package contains a symlink: {relative}")
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode):
                raise PackageError(f"package contains an unsupported file: {relative}")
            size = path.stat().st_size
            total_bytes += size
            if len(result) + 1 > MAX_PACKAGE_FILES:
                raise PackageError(f"package exceeds the {MAX_PACKAGE_FILES}-file safety limit")
            if total_bytes > MAX_PACKAGE_BYTES:
                raise PackageError(f"package exceeds the {MAX_PACKAGE_BYTES}-byte safety limit")
            result.append((path, relative, size))
    result.sort(key=lambda item: item[1])
    return result


def compute_package_digest(root: Path) -> str:
    digest = hashlib.sha256()
    # Keep the original digest domain so existing 0.2 lockfiles remain valid.
    digest.update(b"ZyenLang package v1\0")
    for path, relative, size in _package_files(root):
        encoded_path = relative.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def ensure_cached(source: Path, digest: str) -> Path:
    root = package_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / digest
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir() or compute_package_digest(destination) != digest:
            raise PackageError(f"cached package `{digest}` failed integrity verification")
        return destination

    temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=root))
    try:
        for path, relative, _ in _package_files(source):
            output = temporary / Path(*PurePosixPath(relative).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output)
        if compute_package_digest(temporary) != digest:
            raise PackageError(f"package changed while it was being cached: {source}")
        try:
            temporary.replace(destination)
        except FileExistsError:
            if compute_package_digest(destination) != digest:
                raise PackageError(f"cached package `{digest}` failed integrity verification")
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _relative_source(project_root: Path, source: Path) -> str:
    try:
        return Path(os.path.relpath(source, project_root)).as_posix()
    except ValueError:
        return source.as_posix()


def _source_from_lock(project_root: Path, value: str) -> Path:
    source = Path(value)
    return source.resolve() if source.is_absolute() else (project_root / source).resolve()


def resolve_path_dependencies(project_root: Path) -> tuple[LockedPackage, ...]:
    project_root = project_root.resolve()
    root_manifest = load_manifest(project_root)
    selected: dict[str, tuple[Path, LockedPackage]] = {}
    visiting: list[Path] = []

    def visit(name: str, dependency: PathDependency, owner: PackageManifest) -> None:
        source = (owner.root / dependency.path).resolve()
        if source == project_root:
            raise PackageError(f"dependency cycle returns to root package `{root_manifest.name}`")
        if source in visiting:
            cycle = " -> ".join(path.name for path in visiting + [source])
            raise PackageError(f"dependency cycle: {cycle}")
        manifest = load_manifest(source)
        if manifest.name != name:
            raise PackageError(f"dependency key `{name}` does not match package name `{manifest.name}` at {source}")
        previous = selected.get(name)
        if previous is not None:
            if previous[0] != source:
                raise PackageError(f"package `{name}` is required from both {previous[0]} and {source}")
            return

        visiting.append(source)
        try:
            for child_name, child_dependency in sorted(manifest.dependencies.items()):
                visit(child_name, child_dependency, manifest)
        finally:
            visiting.pop()
        digest = compute_package_digest(source)
        ensure_cached(source, digest)
        locked = LockedPackage(
            name=manifest.name,
            version=manifest.version,
            source=_relative_source(project_root, source),
            digest=digest,
            dependencies=tuple(sorted(manifest.dependencies)),
        )
        selected[name] = (source, locked)

    for dependency_name, dependency in sorted(root_manifest.dependencies.items()):
        visit(dependency_name, dependency, root_manifest)
    return tuple(selected[name][1] for name in sorted(selected))


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: tuple[str, ...] | list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def write_lock(project_root: Path, packages: tuple[LockedPackage, ...]) -> LockFile:
    manifest = load_manifest(project_root)
    lock = LockFile(
        manifest_sha256(project_root),
        tuple(sorted(manifest.dependencies)),
        tuple(sorted(packages, key=lambda package: package.name)),
    )
    lines = [
        "# Generated by ZyenLang. Do not edit by hand.",
        f"lock_version = {LOCK_VERSION}",
        f"manifest_sha256 = {_toml_string(lock.manifest_sha256)}",
        f"root_dependencies = {_toml_array(lock.root_dependencies)}",
        "",
    ]
    for package in lock.packages:
        lines.extend(
            [
                "[[package]]",
                f"name = {_toml_string(package.name)}",
                f"version = {_toml_string(package.version)}",
                'source_type = "path"',
                f"source = {_toml_string(package.source)}",
                f"digest = {_toml_string(package.digest)}",
                f"dependencies = {_toml_array(package.dependencies)}",
                "",
            ]
        )
    _atomic_write(project_root / LOCK_NAME, "\n".join(lines))
    return lock


def _lock_string(table: dict[str, Any], key: str, context: str) -> str:
    value = table.get(key)
    if not isinstance(value, str):
        raise PackageError(f"invalid {LOCK_NAME}: {context}.{key} must be a string")
    return value


def _lock_names(value: Any, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PackageError(f"invalid {LOCK_NAME}: {context} must be an array of package names")
    names = tuple(value)
    if len(set(names)) != len(names) or any(not PACKAGE_NAME_RE.fullmatch(name) for name in names):
        raise PackageError(f"invalid {LOCK_NAME}: {context} contains invalid or duplicate names")
    return names


def read_lock(project_root: Path) -> LockFile:
    data = _load_toml(project_root / LOCK_NAME)
    if data.get("lock_version") != LOCK_VERSION:
        raise PackageError(f"unsupported {LOCK_NAME} version; run `zy pkg install`")
    manifest_digest = _lock_string(data, "manifest_sha256", "lock")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_digest):
        raise PackageError(f"invalid {LOCK_NAME}: manifest_sha256 is not SHA-256")
    root_dependencies = _lock_names(data.get("root_dependencies"), "root_dependencies")
    raw_packages = data.get("package", [])
    if not isinstance(raw_packages, list):
        raise PackageError(f"invalid {LOCK_NAME}: package entries must be an array")
    if len(raw_packages) > MAX_LOCK_PACKAGES:
        raise PackageError(f"invalid {LOCK_NAME}: exceeds the {MAX_LOCK_PACKAGES}-package safety limit")
    packages: list[LockedPackage] = []
    names: set[str] = set()
    for index, raw_package in enumerate(raw_packages):
        context = f"package[{index}]"
        if not isinstance(raw_package, dict):
            raise PackageError(f"invalid {LOCK_NAME}: {context} must be a table")
        name = _lock_string(raw_package, "name", context)
        version = _lock_string(raw_package, "version", context)
        source_type = _lock_string(raw_package, "source_type", context)
        source = _lock_string(raw_package, "source", context)
        digest = _lock_string(raw_package, "digest", context)
        dependencies = _lock_names(raw_package.get("dependencies"), f"{context}.dependencies")
        if not PACKAGE_NAME_RE.fullmatch(name) or name in names:
            raise PackageError(f"invalid {LOCK_NAME}: duplicate or invalid package `{name}`")
        if not SEMVER_RE.fullmatch(version):
            raise PackageError(f"invalid {LOCK_NAME}: package `{name}` has invalid version `{version}`")
        if source_type != "path":
            raise PackageError(f"the current package manager only supports path sources, got `{source_type}`")
        if not source or "\x00" in source:
            raise PackageError(f"invalid {LOCK_NAME}: package `{name}` has an invalid source path")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PackageError(f"invalid {LOCK_NAME}: package `{name}` digest is not SHA-256")
        names.add(name)
        packages.append(LockedPackage(name, version, source, digest, dependencies))
    if any(name not in names for name in root_dependencies):
        raise PackageError(f"invalid {LOCK_NAME}: a root dependency has no package entry")
    for package in packages:
        if any(name not in names for name in package.dependencies):
            raise PackageError(f"invalid {LOCK_NAME}: package `{package.name}` has an unknown dependency")
    return LockFile(manifest_digest, root_dependencies, tuple(packages))


def _validate_locked_manifest(package: LockedPackage, root: Path) -> PackageManifest:
    manifest = load_manifest(root)
    if manifest.name != package.name:
        raise PackageError(f"locked package `{package.name}` contains manifest for `{manifest.name}`")
    if manifest.version != package.version:
        raise PackageError(
            f"locked package `{package.name}` expected version `{package.version}`, got `{manifest.version}`"
        )
    if set(manifest.dependencies) != set(package.dependencies):
        raise PackageError(f"locked package `{package.name}` dependency metadata does not match its manifest")
    return manifest


def validate_lock(project_root: Path, *, populate_cache: bool) -> LockFile:
    project_root = project_root.resolve()
    manifest = load_manifest(project_root)
    lock = read_lock(project_root)
    if lock.manifest_sha256 != manifest_sha256(project_root):
        raise PackageError(f"{LOCK_NAME} is out of date; run `zy pkg install`")
    if set(lock.root_dependencies) != set(manifest.dependencies):
        raise PackageError(f"{LOCK_NAME} dependencies do not match {MANIFEST_NAME}; run `zy pkg install`")
    for package in lock.packages:
        destination = cache_path(package.digest)
        if destination.is_dir():
            if destination.is_symlink() or compute_package_digest(destination) != package.digest:
                raise PackageError(f"cached package `{package.name}` failed integrity verification")
            _validate_locked_manifest(package, destination)
            continue
        if not populate_cache:
            raise PackageError(f"package `{package.name}` is not installed; run `zy pkg install --locked`")
        source = _source_from_lock(project_root, package.source)
        _validate_locked_manifest(package, source)
        if compute_package_digest(source) != package.digest:
            raise PackageError(f"locked package `{package.name}` changed at {source}")
        ensure_cached(source, package.digest)
    return lock


def install_project(project_root: Path, *, locked: bool = False) -> LockFile:
    project_root = project_root.resolve()
    if locked:
        return validate_lock(project_root, populate_cache=True)
    packages = resolve_path_dependencies(project_root)
    return write_lock(project_root, packages)


def locked_packages_for_project(project_root: Path) -> tuple[LockFile, dict[str, Path]]:
    lock = validate_lock(project_root, populate_cache=False)
    return lock, {package.name: cache_path(package.digest) for package in lock.packages}


def _atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _manifest_with_dependencies(path: Path, dependencies: dict[str, PathDependency]) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((index for index, line in enumerate(lines) if re.fullmatch(r"\s*\[dependencies\]\s*", line)), None)
    rendered = [f"{name} = {{ path = {_toml_string(spec.path)} }}" for name, spec in sorted(dependencies.items())]
    if start is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.extend(["", "[dependencies]", *rendered, ""])
        return "\n".join(lines)
    end = start + 1
    while end < len(lines) and not re.fullmatch(r"\s*\[+[^]]+\]+\s*", lines[end]):
        end += 1
    replacement = [lines[start], *rendered]
    if end == len(lines) or (replacement and replacement[-1].strip()):
        replacement.append("")
    return "\n".join(lines[:start] + replacement + lines[end:]).rstrip() + "\n"


def update_manifest_dependencies(project_root: Path, dependencies: dict[str, PathDependency]) -> None:
    manifest_path = project_root / MANIFEST_NAME
    _atomic_write(manifest_path, _manifest_with_dependencies(manifest_path, dependencies))
    load_manifest(project_root)


def _default_package_name(root: Path) -> str:
    value = re.sub(r"[^a-z0-9_-]+", "-", root.name.lower()).strip("-_")
    if not value or not value[0].isalpha():
        value = f"package-{value}" if value else "my-package"
    return value


def init_project(root: Path, name: str | None = None, *, force: bool = False) -> PackageManifest:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    package_name = name or _default_package_name(root)
    if not PACKAGE_NAME_RE.fullmatch(package_name):
        raise PackageError(f"invalid package name `{package_name}`")
    manifest_path = root / MANIFEST_NAME
    if manifest_path.exists() and not force:
        raise PackageError(f"{manifest_path} already exists; use --force to replace it")
    manifest_text = "\n".join(
        [
            "[package]",
            f"name = {_toml_string(package_name)}",
            'version = "0.1.0"',
            'entry = "src/main.zy"',
            'zyen = ">=0.2.1"',
            "",
            "[dependencies]",
            "",
        ]
    )
    _atomic_write(manifest_path, manifest_text)
    entry = root / "src" / "main.zy"
    if not entry.exists():
        _atomic_write(entry, "fn main() i32 {\n    return 0\n}\n")
    write_lock(root, ())
    return load_manifest(root)


def add_dependency(project_root: Path, source: Path) -> LockFile:
    project_root = project_root.resolve()
    manifest = load_manifest(project_root)
    source = source.resolve()
    if not source.is_dir():
        raise PackageError(
            f"local package path not found: {source}; registry and Git sources are not supported yet"
        )
    dependency = load_manifest(source)
    if dependency.name == manifest.name:
        raise PackageError("a package cannot depend on itself")
    dependencies = dict(manifest.dependencies)
    dependencies[dependency.name] = PathDependency(_relative_source(project_root, source))
    manifest_path = project_root / MANIFEST_NAME
    original = manifest_path.read_text(encoding="utf-8")
    try:
        update_manifest_dependencies(project_root, dependencies)
        return install_project(project_root)
    except Exception:
        _atomic_write(manifest_path, original)
        raise


def remove_dependency(project_root: Path, name: str) -> LockFile:
    manifest = load_manifest(project_root)
    if name not in manifest.dependencies:
        raise PackageError(f"dependency `{name}` is not in {MANIFEST_NAME}")
    dependencies = dict(manifest.dependencies)
    del dependencies[name]
    manifest_path = project_root / MANIFEST_NAME
    original = manifest_path.read_text(encoding="utf-8")
    try:
        update_manifest_dependencies(project_root, dependencies)
        return install_project(project_root)
    except Exception:
        _atomic_write(manifest_path, original)
        raise


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="project directory or a path inside it")
    commands = parser.add_subparsers(dest="pkg_command", required=True)

    init = commands.add_parser("init", help="create zyproject.toml and src/main.zy")
    init.add_argument("--name")
    init.add_argument("--force", action="store_true")

    add = commands.add_parser("add", help="add a local path dependency")
    add.add_argument("source", type=Path)

    remove = commands.add_parser("remove", help="remove a direct dependency")
    remove.add_argument("name")

    install = commands.add_parser("install", help="resolve and install dependencies")
    install.add_argument("--locked", action="store_true", help="require the existing lockfile without changing it")

    commands.add_parser("list", help="list direct and transitive dependencies")


def handle_command(args: argparse.Namespace) -> int:
    if args.pkg_command == "init":
        manifest = init_project(args.project, args.name, force=args.force)
        print(f"initialized {manifest.name} at {manifest.root}")
        return 0

    project_root = find_project_root(args.project)
    assert project_root is not None
    if args.pkg_command == "add":
        source = args.source if args.source.is_absolute() else Path.cwd() / args.source
        lock = add_dependency(project_root, source)
        added = load_manifest(source.resolve())
        print(f"added {added.name} {added.version} ({len(lock.packages)} locked package(s))")
        return 0
    if args.pkg_command == "remove":
        lock = remove_dependency(project_root, args.name)
        print(f"removed {args.name} ({len(lock.packages)} locked package(s))")
        return 0
    if args.pkg_command == "install":
        lock = install_project(project_root, locked=args.locked)
        print(f"installed {len(lock.packages)} package(s)")
        return 0
    if args.pkg_command == "list":
        manifest = load_manifest(project_root)
        lock = validate_lock(project_root, populate_cache=False)
        direct = set(manifest.dependencies)
        if not lock.packages:
            print("No dependencies.")
            return 0
        for package in lock.packages:
            kind = "direct" if package.name in direct else "transitive"
            print(f"{package.name} {package.version} {kind} path:{package.source} sha256:{package.digest[:12]}")
        return 0
    raise PackageError(f"unknown package command `{args.pkg_command}`")


def make_parser(prog: str = "zy pkg") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="ZyenLang 0.2 package manager")
    configure_parser(parser)
    return parser


def main(argv: list[str] | None = None, *, prog: str = "zy pkg") -> int:
    try:
        return handle_command(make_parser(prog).parse_args(argv))
    except PackageError as exc:
        print(render_error(f"{prog}: {exc}", sys.stderr), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
