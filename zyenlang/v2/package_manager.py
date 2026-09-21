from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, TypeAlias
from urllib.request import Request, urlopen

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[no-redef]


MANIFEST_NAME = "zyproject.toml"
LOCK_NAME = "zy.lock"
LOCK_VERSION = 2
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PACKAGE_FILES = 10_000
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_LOCK_PACKAGES = 1024
MAX_REGISTRY_BYTES = 1024 * 1024
REGISTRY_SCHEMA = 1
DEFAULT_REGISTRY_URL = "https://raw.githubusercontent.com/Ryan-2013/zyenlang/main/registry/index.json"
PACKAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TARGET_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
OUTPUT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
TARGET_KINDS = frozenset({"bin", "c-source", "staticlib", "sharedlib"})
IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".zyen",
    "__pycache__",
    "build",
    "dist",
    "target",
}
IGNORED_FILES = {LOCK_NAME, ".DS_Store"}


class PackageError(Exception):
    pass


def _valid_git_source(value: str) -> bool:
    if not value or any(char in value for char in "\0\r\n") or value.startswith("-"):
        return False
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
        return bool(
            re.fullmatch(r"https://[A-Za-z0-9._~-]+(?::[0-9]+)?/[^\s]+", value)
            or re.fullmatch(
                r"ssh://(?:[A-Za-z0-9._~-]+@)?[A-Za-z0-9._~-]+(?::[0-9]+)?/[^\s]+",
                value,
            )
        )
    return re.fullmatch(r"(?:[A-Za-z0-9._~-]+@)?[A-Za-z0-9._~-]+:[^\s:][^\s]*", value) is not None


@dataclass(frozen=True)
class PathDependency:
    path: str


@dataclass(frozen=True)
class GitDependency:
    git: str
    rev: str


DependencySpec: TypeAlias = PathDependency | GitDependency


@dataclass(frozen=True)
class TargetSpec:
    name: str
    kind: str
    entry: str
    out_dir: str | None = None
    output_name: str | None = None


@dataclass(frozen=True)
class BuildSpec:
    default_target: str
    target_dir: str


@dataclass(frozen=True)
class PackageManifest:
    root: Path
    name: str
    version: str
    zyen: str
    build: BuildSpec
    targets: dict[str, TargetSpec]
    dependencies: dict[str, DependencySpec]
    library_entry: str

    @property
    def entry(self) -> str:
        return self.library_entry

    def target(self, name: str | None = None) -> TargetSpec:
        selected = name or self.build.default_target
        try:
            return self.targets[selected]
        except KeyError as exc:
            available = ", ".join(sorted(self.targets)) or "<none>"
            raise PackageError(f"unknown target `{selected}`; available targets: {available}") from exc


@dataclass(frozen=True)
class LockedPackage:
    name: str
    package_name: str
    version: str
    source_type: str
    source: str
    revision: str
    digest: str
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class LockFile:
    manifest_sha256: str
    root_dependencies: tuple[str, ...]
    packages: tuple[LockedPackage, ...]

    def by_name(self) -> dict[str, LockedPackage]:
        return {package.name: package for package in self.packages}


@dataclass(frozen=True)
class RegistryDependency:
    name: str
    version: str
    git: str
    rev: str


def zyen_home() -> Path:
    configured = os.environ.get("ZYEN_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".zyen"


def package_cache_root() -> Path:
    return zyen_home() / "packages" / "0.3"


def git_cache_root() -> Path:
    return zyen_home() / "git" / "0.3"


def cache_path(digest: str) -> Path:
    return package_cache_root() / digest


def _registry_bytes(source: str) -> bytes:
    path = Path(source).expanduser()
    if path.is_file():
        if path.stat().st_size > MAX_REGISTRY_BYTES:
            raise PackageError(f"registry index exceeds the {MAX_REGISTRY_BYTES}-byte safety limit")
        return path.read_bytes()
    if not source.startswith("https://"):
        raise PackageError("registry must be an HTTPS URL or a local index file")
    try:
        request = Request(source, headers={"User-Agent": "ZyenLang/0.3 package-manager"})
        with urlopen(request, timeout=10) as response:
            final_url = response.geturl()
            if not final_url.startswith("https://"):
                raise PackageError("registry redirected to a non-HTTPS URL")
            value = response.read(MAX_REGISTRY_BYTES + 1)
    except PackageError:
        raise
    except OSError as exc:
        raise PackageError(f"cannot read package registry: {exc}") from exc
    if len(value) > MAX_REGISTRY_BYTES:
        raise PackageError(f"registry index exceeds the {MAX_REGISTRY_BYTES}-byte safety limit")
    return value


def resolve_registry_dependency(requirement: str, source: str | None = None) -> RegistryDependency:
    name, separator, requested_version = requirement.partition("==")
    if (
        not PACKAGE_NAME_RE.fullmatch(name)
        or (separator and not SEMVER_RE.fullmatch(requested_version))
        or "==" in requested_version
    ):
        raise PackageError("package requirement must be `name` or `name==version`")
    selected_version = requested_version if separator else None
    registry_source = source or os.environ.get("ZYEN_REGISTRY") or DEFAULT_REGISTRY_URL
    raw = _registry_bytes(registry_source)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"invalid registry index: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != REGISTRY_SCHEMA:
        raise PackageError(f"registry index must use schema {REGISTRY_SCHEMA}")
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise PackageError("registry index packages must be an object")
    package = packages.get(name)
    if not isinstance(package, dict):
        raise PackageError(f"package `{name}` was not found in the registry")
    versions = package.get("versions")
    if not isinstance(versions, dict):
        raise PackageError(f"registry package `{name}` has no versions")
    version = selected_version or package.get("latest")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        raise PackageError(f"registry package `{name}` has an invalid latest version")
    selected = versions.get(version)
    if not isinstance(selected, dict):
        raise PackageError(f"package `{name}` has no version `{version}`")
    git = selected.get("git")
    rev = selected.get("rev")
    if not isinstance(git, str) or not _valid_git_source(git):
        raise PackageError(f"registry package `{name}` version `{version}` has an invalid Git source")
    if not isinstance(rev, str) or not GIT_COMMIT_RE.fullmatch(rev):
        raise PackageError(f"registry package `{name}` version `{version}` must pin a full Git commit")
    return RegistryDependency(name, version, git, rev)


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
    return value.strip()


def _validate_relative_file(value: str, field: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or ":" in part or "\x00" in part for part in path.parts)
    ):
        raise PackageError(f"{field} must stay inside the package root")
    if path.suffix != ".zy":
        raise PackageError(f"{field} must name a .zy source file")
    return path.as_posix()


def _validate_relative_directory(value: str, field: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or ":" in part or "\x00" in part for part in path.parts)
    ):
        raise PackageError(f"{field} must be a relative directory inside the package root")
    return path.as_posix()


def _validate_out_dir(value: str, field: str) -> str:
    if not value.strip() or "\x00" in value or "\n" in value or "\r" in value:
        raise PackageError(f"{field} must be a non-empty path")
    return value.replace("\\", "/")


def _parse_dependencies(data: dict[str, Any]) -> dict[str, DependencySpec]:
    raw_dependencies = data.get("dependencies", {})
    if not isinstance(raw_dependencies, dict):
        raise PackageError("[dependencies] must be a table")
    dependencies: dict[str, DependencySpec] = {}
    for alias, raw_spec in raw_dependencies.items():
        if not isinstance(alias, str) or not ALIAS_RE.fullmatch(alias):
            raise PackageError(
                f"invalid dependency alias `{alias}`; aliases use lowercase letters, digits, and `_`"
            )
        if not isinstance(raw_spec, dict):
            raise PackageError(f"dependency `{alias}` must be an inline table")
        if set(raw_spec) == {"path"} and isinstance(raw_spec.get("path"), str):
            path = raw_spec["path"].strip()
            if not path or "\x00" in path or "\n" in path or "\r" in path:
                raise PackageError(f"dependency `{alias}` has an invalid path")
            dependencies[alias] = PathDependency(path)
            continue
        if set(raw_spec) == {"git", "rev"} and all(
            isinstance(raw_spec.get(key), str) for key in ("git", "rev")
        ):
            git = raw_spec["git"].strip()
            rev = raw_spec["rev"].strip()
            if not _valid_git_source(git):
                raise PackageError(f"dependency `{alias}` has an invalid Git URL")
            if not GIT_COMMIT_RE.fullmatch(rev):
                raise PackageError(
                    f"dependency `{alias}` Git rev must be a full 40- or 64-hex commit"
                )
            dependencies[alias] = GitDependency(git, rev)
            continue
        raise PackageError(
            f"dependency `{alias}` must use {{ path = \"../package\" }} or "
            "{ git = \"https://example/repo.git\", rev = \"COMMIT\" }"
        )
    return dependencies


def load_manifest(root: Path) -> PackageManifest:
    root = root.resolve()
    data = _load_toml(root / MANIFEST_NAME)
    package = data.get("package")
    if not isinstance(package, dict):
        raise PackageError(f"{MANIFEST_NAME} requires a [package] table")
    name = _required_string(package, "name", "package")
    version = _required_string(package, "version", "package")
    zyen = package.get("zyen", ">=0.3.0")
    if not PACKAGE_NAME_RE.fullmatch(name):
        raise PackageError(f"invalid package name `{name}`; use lowercase letters, digits, `-`, or `_`")
    if not SEMVER_RE.fullmatch(version):
        raise PackageError(f"package.version must use semantic versioning, got `{version}`")
    if not isinstance(zyen, str) or not zyen.strip():
        raise PackageError("package.zyen must be a non-empty string")

    raw_targets = data.get("targets")
    if not isinstance(raw_targets, dict) or not raw_targets:
        raise PackageError(f"{MANIFEST_NAME} requires at least one [targets.<name>] table")
    targets: dict[str, TargetSpec] = {}
    for target_name, raw_target in raw_targets.items():
        context = f"targets.{target_name}"
        if not isinstance(target_name, str) or not TARGET_NAME_RE.fullmatch(target_name):
            raise PackageError(f"invalid target name `{target_name}`")
        if not isinstance(raw_target, dict):
            raise PackageError(f"[{context}] must be a table")
        unknown = set(raw_target) - {"kind", "entry", "out-dir", "output-name"}
        if unknown:
            raise PackageError(f"[{context}] has unknown key(s): {', '.join(sorted(unknown))}")
        kind = _required_string(raw_target, "kind", context)
        entry = _validate_relative_file(_required_string(raw_target, "entry", context), f"{context}.entry")
        if kind not in TARGET_KINDS:
            raise PackageError(f"{context}.kind must be one of: {', '.join(sorted(TARGET_KINDS))}")
        raw_out_dir = raw_target.get("out-dir")
        if raw_out_dir is not None and not isinstance(raw_out_dir, str):
            raise PackageError(f"{context}.out-dir must be a string")
        out_dir = _validate_out_dir(raw_out_dir, f"{context}.out-dir") if raw_out_dir is not None else None
        raw_output_name = raw_target.get("output-name", target_name)
        if not isinstance(raw_output_name, str) or not OUTPUT_NAME_RE.fullmatch(raw_output_name):
            raise PackageError(f"{context}.output-name must be a safe file stem")
        targets[target_name] = TargetSpec(target_name, kind, entry, out_dir, raw_output_name)

    raw_build = data.get("build", {})
    if not isinstance(raw_build, dict):
        raise PackageError("[build] must be a table")
    unknown_build = set(raw_build) - {"default-target", "target-dir"}
    if unknown_build:
        raise PackageError(f"[build] has unknown key(s): {', '.join(sorted(unknown_build))}")
    default_target = raw_build.get("default-target", next(iter(targets)))
    target_dir = raw_build.get("target-dir", "target")
    if not isinstance(default_target, str) or default_target not in targets:
        raise PackageError("build.default-target must name one of the declared targets")
    if not isinstance(target_dir, str):
        raise PackageError("build.target-dir must be a string")
    build = BuildSpec(default_target, _validate_relative_directory(target_dir, "build.target-dir"))

    raw_library_entry = package.get("entry", "src/lib.zy")
    if not isinstance(raw_library_entry, str):
        raise PackageError("package.entry must be a string when provided")
    library_entry = _validate_relative_file(raw_library_entry, "package.entry")
    return PackageManifest(
        root,
        name,
        version,
        zyen.strip(),
        build,
        targets,
        _parse_dependencies(data),
        library_entry,
    )


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
    digest.update(b"ZyenLang package v0.3\0")
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


def _git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def _run_git(arguments: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            env=_git_environment(),
            check=True,
            capture_output=capture,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PackageError("Git dependencies require `git` on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Git command failed").strip()
        raise PackageError(detail) from exc
    return result.stdout.strip() if capture else ""


def checkout_git(spec: GitDependency, *, locked_commit: str | None = None) -> tuple[Path, str]:
    if not _valid_git_source(spec.git):
        raise PackageError("invalid Git URL; use HTTPS, SSH, scp syntax, or an absolute local path")
    revision = locked_commit or spec.rev
    if not GIT_COMMIT_RE.fullmatch(revision):
        raise PackageError("Git rev must be a full 40- or 64-hex commit")
    identity = hashlib.sha256(f"{spec.git}\0{locked_commit or spec.rev}".encode("utf-8")).hexdigest()
    destination = git_cache_root() / identity
    marker = destination / ".zyen-commit"
    if destination.is_dir() and marker.is_file():
        commit = marker.read_text(encoding="ascii").strip()
        if locked_commit is None or commit == locked_commit:
            return destination, commit
    git_cache_root().mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".checkout-", dir=git_cache_root()))
    try:
        _run_git(["clone", "--no-checkout", "--filter=blob:none", "--", spec.git, str(temporary)])
        _run_git(["checkout", "--detach", revision], cwd=temporary)
        commit = _run_git(["rev-parse", "HEAD"], cwd=temporary, capture=True)
        if locked_commit is not None and commit != locked_commit:
            raise PackageError(f"Git dependency resolved `{commit}` instead of locked commit `{locked_commit}`")
        shutil.rmtree(temporary / ".git", ignore_errors=True)
        (temporary / ".zyen-commit").write_text(commit + "\n", encoding="ascii")
        if destination.exists():
            shutil.rmtree(destination)
        temporary.replace(destination)
        return destination, commit
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


def resolve_dependencies(project_root: Path) -> tuple[LockedPackage, ...]:
    project_root = project_root.resolve()
    root_manifest = load_manifest(project_root)
    selected: dict[str, tuple[Path, LockedPackage]] = {}
    visiting: list[str] = []

    def visit(alias: str, spec: DependencySpec, owner: PackageManifest) -> None:
        if alias in visiting:
            cycle = " -> ".join([*visiting, alias])
            raise PackageError(f"dependency cycle: {cycle}")
        if isinstance(spec, PathDependency):
            source = (owner.root / spec.path).resolve()
            source_type = "path"
            source_label = _relative_source(project_root, source)
            revision = ""
        else:
            source, revision = checkout_git(spec)
            source_type = "git"
            source_label = spec.git
        if source == project_root:
            raise PackageError(f"dependency `{alias}` resolves to the root package")
        manifest = load_manifest(source)
        previous = selected.get(alias)
        if previous is not None:
            previous_record = previous[1]
            identity = (previous_record.source_type, previous_record.source, previous_record.revision)
            current = (source_type, source_label, revision)
            if identity != current:
                raise PackageError(f"dependency alias `{alias}` resolves to more than one source")
            return
        visiting.append(alias)
        try:
            for child_alias, child_spec in sorted(manifest.dependencies.items()):
                visit(child_alias, child_spec, manifest)
        finally:
            visiting.pop()
        digest = compute_package_digest(source)
        ensure_cached(source, digest)
        selected[alias] = (
            source,
            LockedPackage(
                alias,
                manifest.name,
                manifest.version,
                source_type,
                source_label,
                revision,
                digest,
                tuple(sorted(manifest.dependencies)),
            ),
        )

    for alias, dependency in sorted(root_manifest.dependencies.items()):
        visit(alias, dependency, root_manifest)
    return tuple(selected[name][1] for name in sorted(selected))


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: tuple[str, ...] | list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_lock(project_root: Path, packages: tuple[LockedPackage, ...]) -> LockFile:
    manifest = load_manifest(project_root)
    lock = LockFile(
        manifest_sha256(project_root),
        tuple(sorted(manifest.dependencies)),
        tuple(sorted(packages, key=lambda package: package.name)),
    )
    lines = [
        "# Generated by ZyenLang 0.3. Do not edit by hand.",
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
                f"package_name = {_toml_string(package.package_name)}",
                f"version = {_toml_string(package.version)}",
                f"source_type = {_toml_string(package.source_type)}",
                f"source = {_toml_string(package.source)}",
                f"revision = {_toml_string(package.revision)}",
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
        raise PackageError(f"invalid {LOCK_NAME}: {context} must be an array of dependency aliases")
    names = tuple(value)
    if len(set(names)) != len(names) or any(not ALIAS_RE.fullmatch(name) for name in names):
        raise PackageError(f"invalid {LOCK_NAME}: {context} contains invalid or duplicate aliases")
    return names


def read_lock(project_root: Path) -> LockFile:
    data = _load_toml(project_root / LOCK_NAME)
    if data.get("lock_version") != LOCK_VERSION:
        raise PackageError(f"unsupported {LOCK_NAME} version; run `zy fetch`")
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
        package_name = _lock_string(raw_package, "package_name", context)
        version = _lock_string(raw_package, "version", context)
        source_type = _lock_string(raw_package, "source_type", context)
        source = _lock_string(raw_package, "source", context)
        revision = _lock_string(raw_package, "revision", context)
        digest = _lock_string(raw_package, "digest", context)
        dependencies = _lock_names(raw_package.get("dependencies"), f"{context}.dependencies")
        if not ALIAS_RE.fullmatch(name) or name in names:
            raise PackageError(f"invalid {LOCK_NAME}: duplicate or invalid alias `{name}`")
        if not PACKAGE_NAME_RE.fullmatch(package_name):
            raise PackageError(f"invalid {LOCK_NAME}: invalid package name `{package_name}`")
        if not SEMVER_RE.fullmatch(version):
            raise PackageError(f"invalid {LOCK_NAME}: package `{name}` has invalid version `{version}`")
        if source_type not in {"path", "git"}:
            raise PackageError(f"invalid {LOCK_NAME}: unsupported source type `{source_type}`")
        if not source or "\x00" in source:
            raise PackageError(f"invalid {LOCK_NAME}: package `{name}` has an invalid source")
        if source_type == "git" and not _valid_git_source(source):
            raise PackageError(f"invalid {LOCK_NAME}: Git package `{name}` has an invalid source")
        if source_type == "git" and not GIT_COMMIT_RE.fullmatch(revision):
            raise PackageError(f"invalid {LOCK_NAME}: Git package `{name}` has an invalid commit")
        if source_type == "path" and revision:
            raise PackageError(f"invalid {LOCK_NAME}: path package `{name}` cannot have a revision")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PackageError(f"invalid {LOCK_NAME}: package `{name}` digest is not SHA-256")
        names.add(name)
        packages.append(
            LockedPackage(name, package_name, version, source_type, source, revision, digest, dependencies)
        )
    if any(name not in names for name in root_dependencies):
        raise PackageError(f"invalid {LOCK_NAME}: a root dependency has no package entry")
    for package in packages:
        if any(name not in names for name in package.dependencies):
            raise PackageError(f"invalid {LOCK_NAME}: package `{package.name}` has an unknown dependency")
    return LockFile(manifest_digest, root_dependencies, tuple(packages))


def _validate_locked_manifest(package: LockedPackage, root: Path) -> PackageManifest:
    manifest = load_manifest(root)
    if manifest.name != package.package_name:
        raise PackageError(
            f"locked alias `{package.name}` expected package `{package.package_name}`, got `{manifest.name}`"
        )
    if manifest.version != package.version:
        raise PackageError(
            f"locked package `{package.name}` expected version `{package.version}`, got `{manifest.version}`"
        )
    if tuple(sorted(manifest.dependencies)) != package.dependencies:
        raise PackageError(f"locked dependency graph for `{package.name}` does not match its manifest")
    return manifest


def _populate_locked_package(project_root: Path, package: LockedPackage) -> Path:
    destination = cache_path(package.digest)
    if destination.is_dir():
        if compute_package_digest(destination) != package.digest:
            raise PackageError(f"cached package `{package.name}` failed integrity verification")
        return destination
    if package.source_type == "path":
        source = _source_from_lock(project_root, package.source)
    else:
        source, commit = checkout_git(GitDependency(package.source, package.revision), locked_commit=package.revision)
        if commit != package.revision:
            raise PackageError(f"Git package `{package.name}` did not reproduce its locked commit")
    if not source.is_dir() or compute_package_digest(source) != package.digest:
        raise PackageError(f"source for locked package `{package.name}` changed; run `zy fetch` without --locked")
    return ensure_cached(source, package.digest)


def validate_lock(project_root: Path, *, populate_cache: bool = False) -> LockFile:
    project_root = project_root.resolve()
    manifest = load_manifest(project_root)
    lock = read_lock(project_root)
    if lock.manifest_sha256 != manifest_sha256(project_root):
        raise PackageError(f"{LOCK_NAME} is out of date; run `zy fetch`")
    if lock.root_dependencies != tuple(sorted(manifest.dependencies)):
        raise PackageError(f"{LOCK_NAME} dependencies do not match {MANIFEST_NAME}; run `zy fetch`")
    for package in lock.packages:
        root = _populate_locked_package(project_root, package) if populate_cache else cache_path(package.digest)
        if not root.is_dir():
            raise PackageError(f"package `{package.name}` is not fetched; run `zy fetch --locked`")
        if compute_package_digest(root) != package.digest:
            raise PackageError(f"cached package `{package.name}` failed integrity verification")
        _validate_locked_manifest(package, root)
    return lock


def install_project(project_root: Path, *, locked: bool = False) -> LockFile:
    project_root = project_root.resolve()
    if locked:
        return validate_lock(project_root, populate_cache=True)
    return write_lock(project_root, resolve_dependencies(project_root))


def fetch_project(project_root: Path, *, locked: bool = False) -> LockFile:
    return install_project(project_root, locked=locked)


def locked_packages_for_project(project_root: Path) -> tuple[LockFile, dict[str, Path]]:
    lock = validate_lock(project_root, populate_cache=False)
    return lock, {package.name: cache_path(package.digest) for package in lock.packages}


def _render_dependency(spec: DependencySpec) -> str:
    if isinstance(spec, PathDependency):
        return f"{{ path = {_toml_string(spec.path)} }}"
    return f"{{ git = {_toml_string(spec.git)}, rev = {_toml_string(spec.rev)} }}"


def _manifest_with_dependencies(path: Path, dependencies: dict[str, DependencySpec]) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((index for index, line in enumerate(lines) if re.fullmatch(r"\s*\[dependencies\]\s*", line)), None)
    rendered = [f"{name} = {_render_dependency(spec)}" for name, spec in sorted(dependencies.items())]
    if start is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.extend(["", "[dependencies]", *rendered, ""])
        return "\n".join(lines)
    end = start + 1
    while end < len(lines) and not re.fullmatch(r"\s*\[+[^]]+\]+\s*", lines[end]):
        end += 1
    return "\n".join(lines[:start] + [lines[start], *rendered, ""] + lines[end:]).rstrip() + "\n"


def update_manifest_dependencies(project_root: Path, dependencies: dict[str, DependencySpec]) -> None:
    manifest_path = project_root / MANIFEST_NAME
    _atomic_write(manifest_path, _manifest_with_dependencies(manifest_path, dependencies))
    load_manifest(project_root)


def _default_package_name(root: Path) -> str:
    value = re.sub(r"[^a-z0-9_-]+", "-", root.name.lower()).strip("-_")
    if not value or not value[0].isalpha():
        value = f"package-{value}" if value else "my-package"
    return value


def init_project(
    root: Path,
    name: str | None = None,
    *,
    force: bool = False,
    library: bool = False,
) -> PackageManifest:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    package_name = name or _default_package_name(root)
    if not PACKAGE_NAME_RE.fullmatch(package_name):
        raise PackageError(f"invalid package name `{package_name}`")
    manifest_path = root / MANIFEST_NAME
    if manifest_path.exists() and not force:
        raise PackageError(f"{manifest_path} already exists; use --force to replace it")
    target_name = "lib" if library else "app"
    kind = "c-source" if library else "bin"
    entry_path = "src/lib.zy" if library else "src/main.zy"
    output_name = package_name.replace("-", "_")
    manifest_text = "\n".join(
        [
            "[package]",
            f"name = {_toml_string(package_name)}",
            'version = "0.1.0"',
            'zyen = ">=0.3.0"',
            'entry = "src/lib.zy"',
            "",
            "[build]",
            f"default-target = {_toml_string(target_name)}",
            'target-dir = "target"',
            "",
            f"[targets.{target_name}]",
            f"kind = {_toml_string(kind)}",
            f"entry = {_toml_string(entry_path)}",
            f"output-name = {_toml_string(output_name)}",
            "",
            "[dependencies]",
            "",
        ]
    )
    _atomic_write(manifest_path, manifest_text)
    entry = root / Path(*PurePosixPath(entry_path).parts)
    if not entry.exists():
        source = "export fn version() i32 {\n    return 1\n}\n" if library else "fn main() i32 {\n    return 0\n}\n"
        _atomic_write(entry, source)
    library_entry = root / "src" / "lib.zy"
    if not library_entry.exists() and not library:
        _atomic_write(
            library_entry,
            f"public fn package_name() str {{\n    return {_toml_string(package_name)}\n}}\n",
        )
    write_lock(root, ())
    return load_manifest(root)


def new_project(root: Path, name: str | None = None, *, library: bool = False) -> PackageManifest:
    resolved = root.resolve()
    if resolved.exists() and any(resolved.iterdir()):
        raise PackageError(f"new project destination is not empty: {resolved}")
    return init_project(resolved, name, library=library)


def add_dependency_spec(project_root: Path, alias: str, spec: DependencySpec) -> LockFile:
    project_root = project_root.resolve()
    if not ALIAS_RE.fullmatch(alias):
        raise PackageError(f"invalid dependency alias `{alias}`")
    manifest = load_manifest(project_root)
    dependencies = dict(manifest.dependencies)
    dependencies[alias] = spec
    manifest_path = project_root / MANIFEST_NAME
    original = manifest_path.read_text(encoding="utf-8")
    try:
        update_manifest_dependencies(project_root, dependencies)
        return install_project(project_root)
    except Exception:
        _atomic_write(manifest_path, original)
        raise


def add_dependency(project_root: Path, source: Path, alias: str | None = None) -> LockFile:
    source = source.resolve()
    if not source.is_dir():
        raise PackageError(f"local package path not found: {source}")
    dependency = load_manifest(source)
    selected_alias = alias or dependency.name.replace("-", "_")
    return add_dependency_spec(
        project_root,
        selected_alias,
        PathDependency(_relative_source(project_root.resolve(), source)),
    )


def add_git_dependency(project_root: Path, alias: str, git: str, rev: str) -> LockFile:
    if not _valid_git_source(git):
        raise PackageError("invalid Git URL; use HTTPS, SSH, scp syntax, or an absolute local path")
    if not GIT_COMMIT_RE.fullmatch(rev):
        raise PackageError("Git rev must be a full 40- or 64-hex commit")
    return add_dependency_spec(project_root, alias, GitDependency(git, rev))


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
