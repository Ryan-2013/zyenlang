# std/request 使用說明

`std/request` 是同步 HTTP client。Windows 使用 WinHTTP；Linux/macOS 動態載入
系統 libcurl，API 不分平台。

```zy
import <std/request> as request
import <std/io> as io

let response = request.get("https://example.com")
if response.ok() {
    io.print(response.body)
} else {
    io.eprint(response.error)
}
```

`Response` 有 `status: i32`、`body: str`、`error: str`，`ok()` 表示沒有 transport
error 且狀態碼為 2xx。

公開函式包含 `get`、`get_with_timeout`、`post`、`post_with_timeout`、`post_json`、
`put`、`put_with_timeout`、`delete`、`download`、`download_with_timeout` 和低階
`send(method, url, body, content_type, timeout_ms)`。

所有 timeout 單位是毫秒。`body` 與 `error` 是 thread-local borrowed string，
只保證到同一 thread 的下一次 request；需要長期保存時，owned string runtime 完成前
應立即處理或寫入檔案。
