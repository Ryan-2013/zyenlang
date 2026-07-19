from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zyenlang.transpiler import build_file, collect_native_c_metadata, compile_c


def wait_for_server() -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 18765), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("request fixture server did not start")


def test_request_runtime() -> None:
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "request_fixture_server.py")],
        cwd=ROOT,
        creationflags=creationflags,
    )
    download = ROOT / "tests" / "_request_download.bin"
    try:
        wait_for_server()
        source = ROOT / "tests" / "request_test.zy"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=sys.platform.startswith("win")) as tmp:
            temp = Path(tmp)
            c_path = temp / "request_test.c"
            exe_path = temp / ("request_test.exe" if sys.platform.startswith("win") else "request_test")
            build_file(source, c_path)
            compile_c(c_path, exe_path, collect_native_c_metadata(source))
            result = subprocess.run(
                [str(exe_path)], cwd=ROOT, capture_output=True, text=True, timeout=20, check=False
            )
        assert result.returncode == 0, result.stderr
        assert "pass=9 fail=0" in result.stdout
        assert download.read_bytes() == b"downloaded-by-zyenlang\x00binary"
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=3)
        download.unlink(missing_ok=True)


if __name__ == "__main__":
    test_request_runtime()
    print("ALL PASS")
