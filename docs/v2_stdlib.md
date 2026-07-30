# ZyenLang 0.2 standard library

All modules use explicit imports and the same loader as third-party code.

## Process

```zy
import <std/process> as process

let args: List<str> = process.args()
let executable: str = process.executable()
```

The direct special values `GET_ARGS` and `GET_EXE` provide the same borrowed
process-lifetime data.

`List<T>.len()` returns `usize`. `List<T>.get(i32)` performs bounds checking
and returns `T throws Error`, so callers can use `catch` and `recover`.

## Threads and tasks

`std/thread` provides `sleep_ms`, `yield_now`, and `cpu_count`. `spawn call()`
starts a zero-argument call on an OS thread and returns `Task<T>`; `await task`
joins it and releases its control block.

## HTTP client

`std/request` provides `get`, `get_with_timeout`, `post`,
`post_with_timeout`, `post_json`, `put`, `put_with_timeout`, `delete`,
`download`, `download_with_timeout`, and the lower-level `send`.

`Response` contains `status`, `body`, and `error`, with `response.ok()` for the
2xx case. Response strings are borrowed until the next request on that thread.

## HTTP server

`std/server` provides `serve_once(host, port, body)` and
`serve(host, port, body, max_requests)`. Both are blocking and `throws Error`.
Typed request routing waits for closure callbacks and managed request strings.

## GUI

`std/gui` provides `Window`, `window`, `line`, `rect`, `circle`, `text`,
`code_view`, and event parsing. `code_view` renders visible source lines with
line numbers and ZyenLang syntax highlighting. `Window` methods are `open`,
`begin_frame`, `present`, `next_event`, and `close`. Rendering uses the bundled
cross-platform raylib runtime without Python or Tk.

## Editor

`std/editor` contains the mutable UTF-8 document state used by the ZyenLang
IDE. It supports checked open/save, held-key navigation, mouse and Shift+Arrow
selection, replacement of selected text, and completion generated from
language words plus identifiers in the current file. The application imports
the module normally and does not declare C symbols itself.
