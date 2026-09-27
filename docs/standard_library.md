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

`LIST_SHAPE__(values)` is a compiler intrinsic that returns `List<usize>`.
For example, a rectangular `List<List<f64>>` with two rows and three columns
returns `[2, 3]`; ragged nested Lists throw `Error`.

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
`GET_ARGS__()[0]` is the program path and user arguments begin at index `1`.

## `std::os`

```text
name() str
architecture() str
process_id() i64
current_dir() str throws Error
home_dir() str throws Error
temp_dir() str throws Error
hostname() str throws Error
env(name: str) str
has_env(name: str) bool
set_env(name: str, value: str) void throws Error
unset_env(name: str) void throws Error
```

The environment API affects the current process. `has_env` distinguishes an
unset variable from one whose value is the empty string.

## `std::time`

```text
unix_seconds() i64
unix_milliseconds() i64
monotonic_milliseconds() i64
utc_iso8601() str
local_iso8601() str
sleep_ms(milliseconds: i32) i32
```

Elapsed-time measurements should use the monotonic clock rather than wall
clock values.

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
list_dir(path: str) str throws Error
create_dir(path: str) void throws Error
create_dirs(path: str) void throws Error
copy_file(source: str, destination: str) void throws Error
rename(source: str, destination: str) void throws Error
remove_file(path: str) void throws Error
remove_dir(path: str) void throws Error
file_size(path: str) i64 throws Error
```

Text files are UTF-8. `tree` returns a deterministic printable directory tree
and reports inaccessible paths through `Error`.
Relative paths resolve from the running executable's directory, independent of
the shell working directory. This applies to read, write, append, and tree.

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
serve_once_handler(host: str, port: i32, handler: fn(str, str, str) str) i32 throws Error
serve_handler(host: str, port: i32, max_requests: i32, handler: fn(str, str, str) str) i32 throws Error
```

This is a small synchronous HTTP server intended for tools and examples, not a
production application server. Handler callbacks receive method, path, and
body and return a UTF-8 response body.

## `std::gui`

The cross-platform native backend exposes ARC classes:

```text
Application Panel Label Button ButtonGroup Column
```

Module factories create `application` or `application_colored`. Widgets are
created through that Application with `app.panel`, `app.label`, `app.button`,
`app.button_group`, and `app.column`, which binds every widget to its drawing
target. Raw drawing is likewise explicit through `app.line`, `app.rect`,
`app.circle`, `app.text`, `app.code_view`, `app.code_editor`, and
`app.completion`.

```zy
import std::gui as gui

fn clicked() void {
}

fn main() i32 {
    let app = gui::application_colored("Demo", 800, 480, "#20201F")
    let button = app.button(24, 24, 180, 44, "Run", clicked)
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
runtime requirements are present. It is not a Windows-only API. The current
Raylib backend supports one open Application per process. Application cleanup
closes an open native session when the final ARC reference is released.

The GUI source API is backend-neutral. Raylib is the current compatibility
implementation; SDL3 plus SDL_GPU is the planned native backend for stronger
windowing, IME, input, and future game-engine rendering without forcing raw
OpenGL into the language API.

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

`load` creates a compile-time native namespace:

```zy
import std::c_module as c
native module sqlite = c::load("native/sqlite.zlcm.h")
```

`slice(&list)` and `mut_slice(&mut list)` create synchronous native-call
borrows. The old dependent `Module` value remains for ABI v2 compatibility
and emits a deprecation warning. See [C interop](c_interop.md) for Handle
ownership, error mapping, callbacks, bindgen, and native build metadata.
