# `std::request`

`std::request` is a synchronous HTTP client. It uses WinHTTP on Windows and a
dynamically resolved system libcurl on Linux/macOS, with one ZyenLang API.

```zy
import std::io as io
import std::request as request

let response: request::Response = request::get("https://example.com")
if request::response_ok(response) {
    io::print(response.body)
} else {
    io::eprint(response.error)
}
```

`Response` is a pure-data struct with public `status: i32`, `body: str`, and
`error: str` fields. As required by the v0.3 struct model, success testing is
the module function `response_ok(response)`, not a struct method.

Functions include `get`, `get_with_timeout`, `post`, `post_with_timeout`,
`post_json`, `put`, `put_with_timeout`, `delete`, `download`,
`download_with_timeout`, and `send`. Timeout values are milliseconds. Response
text is returned as immutable ARC `str` data.
