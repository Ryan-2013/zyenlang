from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from zyenlang.compiler.compiler import Compiler


def test_time_and_os_modules_build_and_run(tmp_path: Path) -> None:
    source = tmp_path / "time_os.zy"
    source.write_text(
        """import std::os as os
import std::time as time

fn main() i32 throws Error {
    if os::name() == "" || os::architecture() == "" || os::process_id() <= 0 {
        return 1
    }
    if os::current_dir() == "" || os::home_dir() == "" || os::temp_dir() == "" || os::hostname() == "" {
        return 2
    }
    os::set_env("ZYENLANG_STD_OS_TEST", "ready")
    if !os::has_env("ZYENLANG_STD_OS_TEST") || os::env("ZYENLANG_STD_OS_TEST") != "ready" {
        return 3
    }
    os::unset_env("ZYENLANG_STD_OS_TEST")
    if os::has_env("ZYENLANG_STD_OS_TEST") {
        return 4
    }
    let started = time::monotonic_milliseconds()
    if time::sleep_ms(5) != 0 {
        return 5
    }
    let finished = time::monotonic_milliseconds()
    let minimum_seconds: i64 = 1700000000
    let minimum_milliseconds: i64 = 1700000000000
    if finished < started || time::unix_seconds() < minimum_seconds || time::unix_milliseconds() < minimum_milliseconds {
        return 6
    }
    if time::utc_iso8601() == "" || time::local_iso8601() == "" {
        return 7
    }
    return 0
}
""",
        encoding="utf-8",
    )

    compiler = Compiler()
    program = compiler.check_file(source)
    executable = tmp_path / ("time-os.exe" if sys.platform.startswith("win") else "time-os")
    compiler.build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert any(path.endswith("time_native.c") for path in program.native_sources)
    assert any(path.endswith("os_native.c") for path in program.native_sources)
    assert result.returncode == 0, result.stdout + result.stderr
