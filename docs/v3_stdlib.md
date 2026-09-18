# ZyenLang 0.3 Standard Library

All standard modules use ordinary rooted imports and the same native boundary
available to third-party packages.

```zy
import std::io as io
io::print("hello")
```

## `std::io`

```text
print(text: str) void
eprint(text: str) void
```

`print` writes white/default terminal text; `eprint` writes red text when color
is enabled. Both require `str`. Use a cast or f-string for numbers.

## `std::list`

```text
length<T>(values: List<T>) usize
is_empty<T>(values: List<T>) bool
```

Mutation remains explicit through `LIST_*__()` intrinsics so compiler
ownership/COW analysis sees the receiver operation.

## `std::option`

```text
is_null<T>(value: T | null) bool
is_some<T>(value: T | null) bool
```

Prefer `if let` when the branch needs the narrowed value.

## `std::process`

```text
args(value: List<str>) List<str>
executable(value: str) str
```

These wrappers normalize values from `GET_ARGS__()` and `GET_EXE__()`.

## `std::path`

```text
parent(value: str) str
```

Returns the lexical parent path using the native platform path rules.

## `std::fs`

```text
read_text(path: str) str throws Error
write_text(path: str, value: str) i32 throws Error
append_text(path: str, value: str) i32 throws Error
tree(path: str) str throws Error
```

Text files are UTF-8. `tree` returns a deterministic printable directory tree
and reports inaccessible paths through `Error`.

## `std::thread`

```text
sleep_ms(milliseconds: i32) i32
yield_now() i32
cpu_count() i32
```

The language-level `spawn`/`await` runtime uses OS threads. Atomic ARC makes
owner retain/release thread safe; captured mutable data does not automatically
become thread safe.

## `std::request`

```zy
import std::request as request

let response: request::Response = request::get("https://example.com")
if request::response_ok(response) {
    io::print(response.body)
}
```

Functions include `get`, `post`, `post_json`, `put`, `delete`, `download`,
timeout variants, and the general `send`. `Response` is a pure-data struct
with `status`, `body`, and `error`; behavior is a module function, not a struct
method. The implementation uses WinHTTP on Windows and a dynamically resolved
system libcurl on Linux/macOS.

## `std::server`

```text
serve_once(host: str, port: i32, body: str) i32 throws Error
serve(host: str, port: i32, body: str, max_requests: i32) i32 throws Error
```

This is a small synchronous HTTP server intended for tools and examples, not a
production application server.

## `std::gui`

The cross-platform native backend exposes ARC classes:

```text
Window Application Panel Label Button ButtonGroup Column
```

Module factories include `window`, `window_colored`, `application`,
`application_colored`, `panel`, `label`, `button`, `button_group`, and
`column`. Drawing functions include `line`, `rect`, `circle`, `text`,
`code_view`, `code_editor`, and `completion`.

```zy
import std::gui as gui

fn clicked() void {
}

fn main() i32 {
    let app = gui::application_colored("Demo", 800, 480, "#20201F")
    let button = gui::button(24, 24, 180, 44, "Run", clicked)
    if app.open() != 0 { return 1 }
    while true {
        if app.begin_frame() != 0 { break }
        button.draw()
        if app.present() != 0 { break }
        let event = app.next_event(16)
        button.handle(event)
        if event == "quit" { break }
    }
    return app.close()
}
```

The backend is available on Windows, Linux, and macOS when the platform GUI
runtime requirements are present. It is not a Windows-only API.

## `std::editor`

Provides the native text-buffer operations used by the example editor:
open/save/dispose, text/path/status, cursor and scroll state, selection state,
completion state, revision/dirty state, and event handling. It is a low-level
editor module rather than a full IDE framework.

## `std::error`

```text
require(condition: bool, message: str) void throws Error
```

Stops with `message` when the condition is false.

## `std::c_module`

`Module` and `load` are compiler intrinsics with a dependent type. See
[c_module.md](c_module.md) for templates, callbacks, native build metadata,
and the ABI boundary.
