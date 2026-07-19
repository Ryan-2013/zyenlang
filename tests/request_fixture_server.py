from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/download":
            self.reply(200, b"downloaded-by-zyenlang\x00binary")
            return
        self.reply(200, b"hello from request fixture")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.reply(201, b"post:" + self.rfile.read(length))

    def do_PUT(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.reply(202, b"put:" + self.rfile.read(length))

    def do_DELETE(self) -> None:
        self.reply(204, b"")

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18765), Handler).serve_forever()
