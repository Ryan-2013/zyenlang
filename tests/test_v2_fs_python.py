from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from zyenlang.v2.compiler import Compiler


def test_v2_fs_module_reads_writes_appends_and_builds_a_tree(tmp_path: Path) -> None:
    root = tmp_path / "tree-root"
    child = root / "a-dir"
    child.mkdir(parents=True)
    (child / "nested.txt").write_text("nested", encoding="utf-8")
    (root / "z.txt").write_text("last", encoding="utf-8")
    (root / "資料.zy").write_text("utf-8", encoding="utf-8")
    output = tmp_path / "tree.txt"
    source = tmp_path / "fs_module.zy"
    source.write_text(
        """import <std/fs> as fs

fn main() i32 {
    let args: List<str> = GET_ARGS__
    let root: str = args[0] catch err {
        recover ""
    }
    let output: str = args[1] catch err {
        recover ""
    }
    let rendered: str = fs.tree(root) catch err {
        recover ""
    }
    if rendered == "" {
        return 10
    }
    let wrote: i32 = fs.write_text(output, rendered) catch err {
        recover -1
    }
    if wrote != 0 {
        return 11
    }
    let loaded: str = fs.read_text(output) catch err {
        recover ""
    }
    if loaded != rendered {
        return 12
    }
    let appended: i32 = fs.append_text(output, "\\nDONE") catch err {
        recover -1
    }
    return appended
}
""",
        encoding="utf-8",
    )

    compiler = Compiler()
    program = compiler.check_file(source)
    executable = tmp_path / ("fs-module.exe" if sys.platform.startswith("win") else "fs-module")
    compiler.build_file(source, executable)
    result = subprocess.run(
        [str(executable), str(root), str(output)],
        capture_output=True,
        text=True,
        check=False,
    )

    expected = (
        f"{root}/\n"
        "|-- a-dir/\n"
        "|   `-- nested.txt\n"
        "|-- z.txt\n"
        "`-- 資料.zy\n"
        "DONE"
    )
    assert any(path.endswith("fs_native.c") for path in program.native_sources)
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text(encoding="utf-8") == expected


def test_v2_run_passes_program_arguments_after_separator(tmp_path: Path) -> None:
    source = tmp_path / "run_args.zy"
    source.write_text(
        """import <std/io> as io

fn main() i32 {
    let args: List<str> = GET_ARGS__
    let value: str = args[0] catch err {
        recover "missing"
    }
    io.print(value)
    return 0
}
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "zyenlang.v2", "run", str(source), "--", "hello from args"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "hello from args"


def test_v2_fs_reports_missing_paths_as_language_errors(tmp_path: Path) -> None:
    source = tmp_path / "fs_error.zy"
    missing = (tmp_path / "missing.txt").as_posix().replace('"', '\\"')
    source.write_text(
        f"""import <std/fs> as fs

fn main() i32 {{
    let value: str = fs.read_text("{missing}") catch err {{
        recover err.message
    }}
    if value == "" {{
        return 1
    }}
    return 0
}}
""",
        encoding="utf-8",
    )

    executable = tmp_path / ("fs-error.exe" if sys.platform.startswith("win") else "fs-error")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
