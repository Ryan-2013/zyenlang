from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import tempfile

from .backends import CBackend
from .ir import IRProgram
from .modules import load_program
from .parser import parse
from .semantic import lower
from .toolchain import compile_c


@dataclass(frozen=True)
class CompilerOptions:
    backend: str = "c"
    release: bool = False
    require_main: bool = True


class Compiler:
    def __init__(self, options: CompilerOptions | None = None) -> None:
        self.options = options or CompilerOptions()

    def check_source(self, source: str, source_name: str = "<source>") -> IRProgram:
        program = parse(source, source_name)
        if program.imports:
            raise ValueError("imports require file-based compilation so relative paths have a stable base")
        return lower(program, source_name, require_main=self.options.require_main)

    def emit_source(self, source: str, source_name: str = "<source>") -> str:
        program = self.check_source(source, source_name)
        if self.options.backend == "c":
            return CBackend().emit(program, include_main=self.options.require_main)
        raise ValueError(f"unknown backend `{self.options.backend}`")

    def check_file(self, path: Path) -> IRProgram:
        return lower(load_program(path), str(path), require_main=self.options.require_main)

    def check_file_source(self, path: Path, source: str) -> IRProgram:
        resolved = path.resolve()
        return lower(load_program(resolved, source), str(resolved), require_main=self.options.require_main)

    def emit_program(self, program: IRProgram, *, include_main: bool | None = None) -> str:
        if self.options.backend != "c":
            raise ValueError(f"unknown backend `{self.options.backend}`")
        return CBackend().emit(
            program,
            include_main=self.options.require_main if include_main is None else include_main,
        )

    def emit_file(self, path: Path, *, include_main: bool | None = None) -> str:
        program = self.check_file(path)
        return self.emit_program(program, include_main=include_main)

    def build_file(self, path: Path, output: Path) -> None:
        """Build an executable. Artifact kind is never inferred from the output suffix."""
        self.build_executable(path, output)

    def emit_c_file(self, path: Path, output: Path, *, include_main: bool = False) -> IRProgram:
        program = self.check_file(path)
        c_source = self.emit_program(program, include_main=include_main)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(c_source, encoding="utf-8", newline="\n")
        return program

    def build_executable(self, path: Path, output: Path) -> None:
        program = self.check_file(path)
        c_source = self.emit_program(program, include_main=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as temp:
            c_path = Path(temp) / f"{path.stem}.c"
            c_path.write_text(c_source, encoding="utf-8")
            compile_c(
                c_path,
                output,
                release=self.options.release,
                native_sources=program.native_sources,
                native_links=program.native_links,
                include_dirs=tuple(Path(value) for value in program.native_include_dirs),
                lib_dirs=tuple(Path(value) for value in program.native_lib_dirs),
                cflags=program.native_cflags,
                ldflags=program.native_ldflags,
            )

    def run_file(self, path: Path, program_args: Sequence[str] = ()) -> int:
        program = self.check_file(path)
        c_source = self.emit_program(program, include_main=True)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as temp:
            temp_path = Path(temp)
            c_path = temp_path / f"{path.stem}.c"
            exe_name = path.stem + (".exe" if sys.platform.startswith("win") else "")
            exe_path = temp_path / exe_name
            c_path.write_text(c_source, encoding="utf-8")
            compile_c(
                c_path,
                exe_path,
                release=self.options.release,
                native_sources=program.native_sources,
                native_links=program.native_links,
                include_dirs=tuple(Path(value) for value in program.native_include_dirs),
                lib_dirs=tuple(Path(value) for value in program.native_lib_dirs),
                cflags=program.native_cflags,
                ldflags=program.native_ldflags,
            )
            return subprocess.run([str(exe_path), *program_args], check=False).returncode
