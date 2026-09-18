from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Sequence

from .compiler import Compiler, CompilerOptions
from .ir import IRFunction, IRProgram
from .package_manager import (
    LOCK_NAME,
    PackageError,
    PackageManifest,
    TargetSpec,
    find_project_root,
    load_manifest,
    validate_lock,
    write_lock,
)
from .toolchain import compile_c, compile_objects, create_shared_library, create_static_library
from .types import BOOL, STR, VOID, PrimitiveType, Type


ARTIFACT_STATE_VERSION = 1
ARTIFACT_STATE_NAME = ".zyen-artifacts.json"
MAX_ARTIFACT_STATE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ArtifactResult:
    target: str
    kind: str
    output_dir: Path
    primary: Path
    files: tuple[Path, ...]


def _entry_path(manifest: PackageManifest, target: TargetSpec) -> Path:
    entry = (manifest.root / Path(*PurePosixPath(target.entry).parts)).resolve()
    if manifest.root != entry and manifest.root not in entry.parents:
        raise PackageError(f"target `{target.name}` entry escapes the project root")
    if not entry.is_file():
        raise PackageError(f"target `{target.name}` entry does not exist: {entry}")
    return entry


def _profile(release: bool) -> str:
    return "release" if release else "debug"


def _output_directory(
    manifest: PackageManifest,
    target: TargetSpec,
    release: bool,
    override: Path | None,
) -> Path:
    if override is not None:
        return (override if override.is_absolute() else manifest.root / override).resolve()
    if target.out_dir is not None:
        configured = Path(target.out_dir)
        return (configured if configured.is_absolute() else manifest.root / configured).resolve()
    return (manifest.root / manifest.build.target_dir / _profile(release) / target.name).resolve()


def _library_filename(kind: str, output_name: str) -> str:
    if kind == "staticlib":
        return f"{output_name}.lib" if sys.platform.startswith("win") else f"lib{output_name}.a"
    if kind == "sharedlib":
        if sys.platform.startswith("win"):
            return f"{output_name}.dll"
        if sys.platform == "darwin":
            return f"lib{output_name}.dylib"
        return f"lib{output_name}.so"
    raise PackageError(f"`{kind}` is not a library target")


def _binary_filename(output_name: str) -> str:
    return output_name + (".exe" if sys.platform.startswith("win") else "")


def _c_abi_type(typ: Type) -> str:
    if typ == STR:
        return "ZL_String"
    if typ == BOOL:
        return "bool"
    if typ == VOID:
        return "void"
    if isinstance(typ, PrimitiveType):
        mapping = {
            "i8": "int8_t",
            "i16": "int16_t",
            "i32": "int32_t",
            "i64": "int64_t",
            "u8": "uint8_t",
            "u16": "uint16_t",
            "u32": "uint32_t",
            "u64": "uint64_t",
            "f32": "float",
            "f64": "double",
        }
        if typ.name in mapping:
            return mapping[typ.name]
    raise PackageError(f"type `{typ.display()}` cannot appear in an exported C ABI function")


def exported_functions(program: IRProgram) -> tuple[IRFunction, ...]:
    return tuple(
        function
        for function in program.functions
        if function.exported and "::" not in function.name and function.receiver_type is None
    )


def render_c_header(program: IRProgram, output_name: str) -> str:
    guard = "ZYENLANG_" + re.sub(r"[^A-Za-z0-9]", "_", output_name).upper() + "_H"
    lines = [
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <stdbool.h>",
        "#include <stdint.h>",
        '#include "zyenlang_c_abi.h"',
        "",
        "#ifdef __cplusplus",
        'extern "C" {',
        "#endif",
        "",
    ]
    for function in exported_functions(program):
        if function.throws is not None:
            raise PackageError(f"export function `{function.name}` cannot throw")
        result = _c_abi_type(function.return_type)
        parameters = ", ".join(
            f"{_c_abi_type(parameter.typ)} {parameter.name}" for parameter in function.params
        ) or "void"
        lines.append(f"ZYENLANG_API {result} {function.c_name}({parameters});")
    lines.extend(
        [
            "",
            "#ifdef __cplusplus",
            "}",
            "#endif",
            "",
            f"#endif /* {guard} */",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _runtime_headers() -> tuple[Path, ...]:
    root = Path(__file__).resolve().parent / "runtime"
    return (root / "zy2_runtime.h", root / "zyenlang_c_abi.h")


def _copy_runtime_headers(output_dir: Path) -> tuple[Path, ...]:
    copied: list[Path] = []
    for source in _runtime_headers():
        destination = output_dir / source.name
        shutil.copyfile(source, destination)
        copied.append(destination)
    return tuple(copied)


def _quoted_includes(path: Path) -> tuple[Path, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ()
    found: list[Path] = []
    for match in re.finditer(r'^\s*#\s*include\s+"([^"\r\n]+)"', text, re.MULTILINE):
        candidate = (path.parent / match.group(1)).resolve()
        if candidate.is_file():
            found.append(candidate)
    return tuple(found)


def _native_bundle(
    native_sources: tuple[str, ...],
    output_dir: Path,
    native_headers: tuple[str, ...] = (),
) -> tuple[Path, ...]:
    queue = [Path(value).resolve() for value in (*native_sources, *native_headers)]
    seen: set[Path] = set()
    copied_by_name: dict[str, Path] = {}
    copied: list[Path] = []
    while queue:
        source = queue.pop(0)
        if source in seen:
            continue
        seen.add(source)
        if not source.is_file():
            raise PackageError(f"native source disappeared before bundling: {source}")
        previous = copied_by_name.get(source.name)
        if previous is not None and previous.read_bytes() != source.read_bytes():
            raise PackageError(f"native bundle has conflicting file names: {source.name}")
        destination = output_dir / source.name
        if previous is None:
            shutil.copyfile(source, destination)
            copied_by_name[source.name] = source
            copied.append(destination)
        queue.extend(_quoted_includes(source))
    return tuple(copied)


def _metadata_document(
    target: TargetSpec,
    output_name: str,
    sources: Sequence[Path],
    headers: Sequence[Path],
    program: IRProgram,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "language_version": "0.3.0",
        "target": target.name,
        "kind": target.kind,
        "output_name": output_name,
        "c_standard": "c11",
        "sources": [path.name for path in sources],
        "headers": [path.name for path in headers],
        "include_paths": ["."],
        "defines": [],
        "compile_flags": ["-std=c11", *program.native_cflags],
        "link_flags": list(program.native_ldflags),
        "library_paths": list(program.native_lib_dirs),
        "libraries": [
            {"platform": link.platform, "name": link.library} for link in program.native_links
        ],
        "exports": [function.c_name for function in exported_functions(program)],
    }


def _state_path(manifest: PackageManifest) -> Path:
    return manifest.root / manifest.build.target_dir / ARTIFACT_STATE_NAME


def _load_artifact_state(manifest: PackageManifest) -> dict[str, Any]:
    path = _state_path(manifest)
    if not path.exists():
        return {"version": ARTIFACT_STATE_VERSION, "targets": {}}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_STATE_BYTES:
        raise PackageError(f"invalid artifact state file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"invalid artifact state file: {path}") from exc
    if not isinstance(value, dict) or value.get("version") != ARTIFACT_STATE_VERSION:
        raise PackageError(f"unsupported artifact state file: {path}")
    targets = value.get("targets")
    if not isinstance(targets, dict):
        raise PackageError(f"invalid artifact target records: {path}")
    return value


def _remove_recorded_files(record: Any) -> list[Path]:
    if not isinstance(record, dict) or not isinstance(record.get("files"), list):
        raise PackageError("artifact state contains an invalid target record")
    removed: list[Path] = []
    for raw_path in record["files"]:
        if not isinstance(raw_path, str) or "\x00" in raw_path:
            raise PackageError("artifact state contains an invalid file path")
        path = Path(raw_path)
        if not path.is_absolute():
            raise PackageError("artifact state paths must be absolute")
        if path.is_symlink():
            raise PackageError(f"refusing to clean a symlink recorded as an artifact: {path}")
        if path.exists():
            if not path.is_file():
                raise PackageError(f"refusing to recursively clean artifact path: {path}")
            path.unlink()
            removed.append(path)
    return removed


def _record_key(target: str, release: bool) -> str:
    return f"{_profile(release)}:{target}"


def _prepare_record(manifest: PackageManifest, target: str, release: bool) -> dict[str, Any]:
    state = _load_artifact_state(manifest)
    key = _record_key(target, release)
    previous = state["targets"].pop(key, None)
    if previous is not None:
        _remove_recorded_files(previous)
    _atomic_write_json(_state_path(manifest), state)
    return state


def _save_record(
    manifest: PackageManifest,
    state: dict[str, Any],
    target: TargetSpec,
    release: bool,
    files: Sequence[Path],
) -> None:
    state["targets"][_record_key(target.name, release)] = {
        "kind": target.kind,
        "files": [str(path.resolve()) for path in files],
    }
    _atomic_write_json(_state_path(manifest), state)


def _ensure_lock(manifest: PackageManifest) -> None:
    lock_path = manifest.root / LOCK_NAME
    if not manifest.dependencies:
        try:
            validate_lock(manifest.root)
        except PackageError:
            write_lock(manifest.root, ())
        return
    if not lock_path.is_file():
        raise PackageError(f"missing {LOCK_NAME}; run `zy fetch`")
    validate_lock(manifest.root)


class ArtifactBuilder:
    def __init__(self, project: Path, *, release: bool = False) -> None:
        root = find_project_root(project)
        assert root is not None
        self.manifest = load_manifest(root)
        self.release = release

    def check(self, target_name: str | None = None) -> IRProgram:
        _ensure_lock(self.manifest)
        target = self.manifest.target(target_name)
        compiler = Compiler(
            CompilerOptions(release=self.release, require_main=target.kind == "bin")
        )
        return compiler.check_file(_entry_path(self.manifest, target))

    def build(self, target_name: str | None = None, *, out_dir: Path | None = None) -> ArtifactResult:
        _ensure_lock(self.manifest)
        target = self.manifest.target(target_name)
        entry = _entry_path(self.manifest, target)
        output_dir = _output_directory(self.manifest, target, self.release, out_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        state = _prepare_record(self.manifest, target.name, self.release)
        output_name = target.output_name or target.name
        require_main = target.kind == "bin"
        compiler = Compiler(CompilerOptions(release=self.release, require_main=require_main))
        program = compiler.check_file(entry)
        c_source = compiler.emit_program(program, include_main=require_main)
        files: list[Path] = []

        if target.kind == "bin":
            primary = output_dir / _binary_filename(output_name)
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as temporary:
                generated = Path(temporary) / f"{output_name}.c"
                generated.write_text(c_source, encoding="utf-8", newline="\n")
                compile_c(
                    generated,
                    primary,
                    release=self.release,
                    native_sources=program.native_sources,
                    native_links=program.native_links,
                    include_dirs=tuple(Path(value) for value in program.native_include_dirs),
                    lib_dirs=tuple(Path(value) for value in program.native_lib_dirs),
                    cflags=program.native_cflags,
                    ldflags=program.native_ldflags,
                )
            files.append(primary)
        elif target.kind == "c-source":
            primary = output_dir / f"{output_name}.c"
            header = output_dir / f"{output_name}.h"
            primary.write_text(c_source, encoding="utf-8", newline="\n")
            header.write_text(render_c_header(program, output_name), encoding="utf-8", newline="\n")
            runtime_headers = _copy_runtime_headers(output_dir)
            native_files = _native_bundle(program.native_sources, output_dir, program.native_headers)
            sources = (primary, *(path for path in native_files if path.suffix.lower() == ".c"))
            headers = (header, *runtime_headers, *(path for path in native_files if path.suffix.lower() == ".h"))
            metadata = output_dir / f"{output_name}.metadata.json"
            _atomic_write_json(metadata, _metadata_document(target, output_name, sources, headers, program))
            files.extend([*sources, *headers, metadata])
        else:
            primary = output_dir / _library_filename(target.kind, output_name)
            header = output_dir / f"{output_name}.h"
            header.write_text(render_c_header(program, output_name), encoding="utf-8", newline="\n")
            runtime_headers = _copy_runtime_headers(output_dir)
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as temporary:
                temp = Path(temporary)
                generated = temp / f"{output_name}.c"
                generated.write_text(c_source, encoding="utf-8", newline="\n")
                sources = (generated, *(Path(value) for value in program.native_sources))
                if target.kind == "staticlib":
                    objects = compile_objects(
                        sources,
                        temp / "objects",
                        release=self.release,
                        include_dirs=tuple(Path(value) for value in program.native_include_dirs),
                        cflags=program.native_cflags,
                    )
                    create_static_library(objects, primary)
                else:
                    import_library = (
                        output_dir / f"lib{output_name}.dll.a" if sys.platform.startswith("win") else None
                    )
                    create_shared_library(
                        sources,
                        primary,
                        release=self.release,
                        include_dirs=tuple(Path(value) for value in program.native_include_dirs),
                        native_links=program.native_links,
                        lib_dirs=tuple(Path(value) for value in program.native_lib_dirs),
                        cflags=program.native_cflags,
                        ldflags=program.native_ldflags,
                        import_library=import_library,
                    )
                    if import_library is not None:
                        files.append(import_library)
            files.extend([primary, header, *runtime_headers])

        unique_files = tuple(dict.fromkeys(path.resolve() for path in files))
        _save_record(self.manifest, state, target, self.release, unique_files)
        return ArtifactResult(target.name, target.kind, output_dir, primary.resolve(), unique_files)

    def run(
        self,
        target_name: str | None = None,
        *,
        program_args: Sequence[str] = (),
        out_dir: Path | None = None,
    ) -> int:
        target = self.manifest.target(target_name)
        if target.kind != "bin":
            raise PackageError(f"target `{target.name}` is `{target.kind}`; only bin targets can run")
        result = self.build(target.name, out_dir=out_dir)
        return subprocess.run([str(result.primary), *program_args], check=False).returncode

    def metadata(self) -> dict[str, Any]:
        _ensure_lock(self.manifest)
        lock = validate_lock(self.manifest.root)
        return {
            "schema_version": 1,
            "package": {
                "name": self.manifest.name,
                "version": self.manifest.version,
                "root": str(self.manifest.root),
                "zyen": self.manifest.zyen,
            },
            "build": asdict(self.manifest.build),
            "targets": {name: asdict(target) for name, target in sorted(self.manifest.targets.items())},
            "dependencies": [asdict(package) for package in lock.packages],
        }

    def clean(self, target_name: str | None = None) -> tuple[Path, ...]:
        state = _load_artifact_state(self.manifest)
        selected: list[str]
        if target_name is None:
            selected = list(state["targets"])
        else:
            self.manifest.target(target_name)
            selected = [key for key in state["targets"] if key.endswith(f":{target_name}")]
        removed: list[Path] = []
        for key in selected:
            removed.extend(_remove_recorded_files(state["targets"].pop(key)))
        path = _state_path(self.manifest)
        if state["targets"]:
            _atomic_write_json(path, state)
        elif path.exists():
            path.unlink()
        return tuple(removed)


def emit_single_file(source: Path, output_dir: Path, *, output_name: str | None = None) -> ArtifactResult:
    source = source.resolve()
    if not source.is_file() or source.suffix != ".zy":
        raise PackageError(f"zy emit requires an existing .zy file: {source}")
    name = output_name or source.stem
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
        raise PackageError("--output-name must be a safe file stem")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    compiler = Compiler(CompilerOptions(require_main=False))
    program = compiler.check_file(source)
    include_main = any(function.name == "main" and function.receiver_type is None for function in program.functions)
    generated = output_dir / f"{name}.c"
    generated.write_text(
        compiler.emit_program(program, include_main=include_main),
        encoding="utf-8",
        newline="\n",
    )
    headers = _copy_runtime_headers(output_dir)
    native_files = _native_bundle(program.native_sources, output_dir, program.native_headers)
    files = tuple(dict.fromkeys(path.resolve() for path in (generated, *headers, *native_files)))
    return ArtifactResult(name, "c", output_dir, generated, files)
