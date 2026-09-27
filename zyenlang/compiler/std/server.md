# std/server 使用說明

`std/server` 是小型 blocking HTTP text server，適合本機工具、測試和原型。

```zy
import std::server as server

fn main() i32 throws Error {
    return server::serve_once("127.0.0.1", 8080, "hello")
}
```

- `serve_once(host, port, body) i32 throws Error` 處理一個 request。
- `serve(host, port, body, max_requests) i32 throws Error` 最多處理指定數量。
- `serve_handler(host, port, max_requests, handler) i32 throws Error` calls
  `handler(method, path, body) -> str` for each request.
- `serve_once_handler(host, port, handler) i32 throws Error` handles one
  routed request.

成功結果是已處理 request 數；bind、listen、accept 或傳送失敗會 `stop`。
The handler callback runs synchronously on the serving thread. The first
version returns a UTF-8 text body with status 200; custom status/headers, TLS,
keep-alive, and an async event loop remain application-server features rather
than hidden behavior in this small standard module.
