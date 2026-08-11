from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from zyenlang.v2.compiler import Compiler


def test_v2_path_module_builds_and_runs_cross_platform(tmp_path: Path) -> None:
    source = tmp_path / "path_module.zy"
    source.write_text(
        """import <std/path> as path

fn main() i32 {
    if path.separator() != "/" {
        return 1
    }
    if path.normalize("src//lib///main.zy") != "src/lib/main.zy" {
        return 2
    }
    if path.join("src", "main.zy") != "src/main.zy" {
        return 3
    }
    if path.basename("src/main.zy") != "main.zy" {
        return 4
    }
    if path.dirname("src/main.zy") != "src" || path.parent("src/main.zy") != "src" {
        return 5
    }
    if path.extension("src/main.zy") != ".zy" || path.stem("src/main.zy") != "main" {
        return 6
    }
    if path.with_extension("src/main.zy", "c") != "src/main.c" {
        return 7
    }
    if !path.is_absolute("/tmp/value") || path.is_absolute("src/main.zy") {
        return 8
    }
    if !path.exists(".") || !path.is_dir(".") || path.is_file(".") {
        return 9
    }
    return 0
}
""",
        encoding="utf-8",
    )

    compiler = Compiler()
    program = compiler.check_file(source)
    executable = tmp_path / ("path-module.exe" if sys.platform.startswith("win") else "path-module")
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert any(path.endswith("path_native.c") for path in program.native_sources)
    assert result.returncode == 0, result.stdout + result.stderr
