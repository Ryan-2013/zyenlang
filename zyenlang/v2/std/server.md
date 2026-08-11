# std/server 使用說明

`std/server` 是小型 blocking HTTP text server，適合本機工具、測試和原型。

```zy
import <std/server> as server

fn main() i32 throws Error {
    return server.serve_once("127.0.0.1", 8080, "hello")
}
```

- `serve_once(host, port, body) i32 throws Error` 處理一個 request。
- `serve(host, port, body, max_requests) i32 throws Error` 最多處理指定數量。

成功結果是已處理 request 數；bind、listen、accept 或傳送失敗會 `stop`。
目前每個 request 回傳固定文字，尚未提供 route callback、TLS 或非同步 server。
