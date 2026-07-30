# `std/request`

`std/request` is a synchronous HTTP client implemented as an ordinary
`c_module`. It does not use a compiler-only native hook, launch Python, or
build shell commands.

```zy
import <std/request>;

fn main() -> int {
    let response: Response = request.get("https://example.com");
    if (response.ok()) {
        print(response.body);
    } else {
        print(response.error);
    }
    return 0;
}
```

## API

```zy
request.send(method, url, body, content_type, timeout_ms) -> Response
request.get(url, timeout_ms = 30000) -> Response
request.post(url, body, content_type = "text/plain; charset=utf-8", timeout_ms = 30000) -> Response
request.post_json(url, json, timeout_ms = 30000) -> Response
request.put(url, body, content_type = "text/plain; charset=utf-8", timeout_ms = 30000) -> Response
request.delete(url, timeout_ms = 30000) -> Response
request.download(url, path, timeout_ms = 30000) -> Response
```

`Response` contains `status: int`, `body: str`, and `error: str`.
`response.ok()` is true when transport succeeded and the status is in the
200-299 range. HTTP errors such as 404 remain valid responses; network, TLS,
timeout, URL, and file errors are reported through `error`.

`download()` writes the exact response bytes, including embedded zero bytes,
and returns an empty `body` on success.

## Backends

- Windows uses the system WinHTTP API and links `winhttp` through the
  `.zlcm.h` template.
- Linux and macOS dynamically load the system libcurl at runtime. Linux links
  `dl`; macOS resolves it through libSystem.

The first implementation is synchronous and intentionally has no global
session object. Custom headers, streaming, cookies, and async requests can be
added without changing the `Response` shape.

The native bridge keeps one last response while a call is being wrapped. The
public ZyenLang layer copies `body` and `error` before returning, so a later
request does not overwrite an earlier `Response`.

## Tests

`tests/request_test.zy` covers GET, POST, DELETE, status handling, body reads,
and binary download against a local-only fixture server.
