from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from zyenlang.compiler.compiler import Compiler


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_server_routes_a_request_through_a_function_value(tmp_path: Path) -> None:
    port = _free_port()
    source = tmp_path / "server_handler.zy"
    source.write_text(
        f"""import std::server as server

fn route(method: str, path: str, body: str) str {{
    return f"{{method}} {{path}} {{body}}"
}}

fn main() i32 throws Error {{
    return server::serve_once_handler("127.0.0.1", {port}, route) - 1
}}
""",
        encoding="utf-8",
    )
    executable = tmp_path / ("server-handler.exe" if sys.platform.startswith("win") else "server-handler")
    Compiler().build_file(source, executable)
    process = subprocess.Popen(
        [str(executable)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        response_body: str | None = None
        last_error: Exception | None = None
        for _ in range(40):
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/hello",
                    data=b"payload",
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=1) as response:
                    response_body = response.read().decode("utf-8")
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                if process.poll() is not None:
                    break
                time.sleep(0.05)
        stdout, stderr = process.communicate(timeout=5)
        assert response_body == "POST /hello payload", last_error
        assert process.returncode == 0, stdout + stderr
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
