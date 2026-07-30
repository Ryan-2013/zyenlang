from __future__ import annotations

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
            return CBackend().emit(program)
        raise ValueError(f"unknown backend `{self.options.backend}`")

    def check_file(self, path: Path) -> IRProgram:
        return lower(load_program(path), str(path), require_main=self.options.require_main)

    def check_file_source(self, path: Path, source: str) -> IRProgram:
        resolved = path.resolve()
        return lower(load_program(resolved, source), str(resolved), require_main=self.options.require_main)

    def emit_file(self, path: Path) -> str:
        program = self.check_file(path)
        if self.options.backend == "c":
            return CBackend().emit(program)
        raise ValueError(f"unknown backend `{self.options.backend}`")

    def build_file(self, path: Path, output: Path) -> None:
        program = self.check_file(path)
        if self.options.backend != "c":
            raise ValueError(f"unknown backend `{self.options.backend}`")
        c_source = CBackend().emit(program)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.suffix.lower() == ".c":
            output.write_text(c_source, encoding="utf-8")
            return
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as temp:
            c_path = Path(temp) / f"{path.stem}.c"
            c_path.write_text(c_source, encoding="utf-8")
            compile_c(
                c_path,
                output,
                release=self.options.release,
                native_sources=program.native_sources,
                native_links=program.native_links,
            )

    def run_file(self, path: Path) -> int:
        program = self.check_file(path)
        if self.options.backend != "c":
            raise ValueError(f"unknown backend `{self.options.backend}`")
        c_source = CBackend().emit(program)
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
            )
            return subprocess.run([str(exe_path)], check=False).returncode
