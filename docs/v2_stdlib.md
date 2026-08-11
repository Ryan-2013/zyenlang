# ZyenLang 0.2 standard library

All modules use explicit imports and the same loader as third-party code.

## Process

```zy
import <std/process> as process

let args: List<str> = process.args(GET_ARGS__)
let executable: str = process.executable(GET_EXE__)
let source_file: str = FILE__
```

The special values `GET_ARGS__` and `GET_EXE__` contain borrowed process-lifetime
data and may appear only inside `main`. The process facade accepts explicitly
passed values without receiving a compiler privilege.

`FILE__` is the absolute path of the `.zy` module containing the expression.
Unlike process values, it may be used in any function and resolves separately
inside imported modules.

`List<T>` is a compiler-specialized built-in generic container, not a standard
library struct. `List` is the type constructor and `T` is its element type.
Each concrete element type gets a distinct C storage type containing `items`,
`len`, and `capacity`.
List literals create owned growable storage. `GET_ARGS__` and struct metadata
start as borrowed views and use copy-on-write on their first mutation.

Use `LIST_LEN__(list)` for length and `list[index]` for checked access. These are
compiler operations, not struct methods. `LIST_PUSH__(list, value)` appends and
`LIST_SET__(list, index, value)` replaces an element. `pop`, `remove`, and `clear`
remain container intrinsics; `capacity` and `is_empty` expose storage
state. Indexing, `set`, `pop`, and `remove` perform bounds checks and return
`throws Error`. Copies share one ARC storage, so a mutation through one owned
alias is visible through the other aliases. The old method-shaped spellings
remain temporary source-compatibility aliases.

Nested `List<List<T>>` and `List<Box<T>>` recursively retain and release their
elements. A List may also be a struct field; the containing struct receives
generated recursive retain/release helpers and can itself be nested or stored
in another List. Lists inside tuples, optionals, and self-referential managed
struct graphs remain pending.

## Paths

```zy
import <std/path> as path

let source: str = path.join("src", "main.zy")
let name: str = path.basename(source)
```

`std/path` provides `separator`, `normalize`, `join`, `basename`, `dirname`,
`parent`, `extension`, `stem`, `with_extension`, `is_absolute`, `exists`,
`is_file`, and `is_dir`. Lexical results use `/` as the portable separator.
Returned strings use rotating thread-local borrowed buffers until owned v2
strings are available.

## Filesystem

```zy
import <std/fs> as fs

let listing: str = fs.tree(".")
let source: str = fs.read_text("main.zy")
fs.write_text("tree.txt", listing)
```

`std/fs` provides `read_text`, `write_text`, `append_text`, and a sorted ASCII
`tree`. All operations use `throws Error`; directory traversal does not follow
symbolic links or Windows reparse-point directories and has bounded depth and
output size.

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

`std/gui` provides a Qt-like first retained layer with `Application`, `Panel`,
`Label`, `Button`, and `Column`, plus `Window`, `line`, `rect`, `circle`, `text`,
`code_view`, and event parsing. Widgets are ordinary typed structs; a `Button`
stores a checked `fn() void` callback and handles release events. Rendering uses
the bundled cross-platform raylib runtime without Python or Tk.

`ButtonGroup` demonstrates managed aggregate ownership in the standard library:
its `buttons: List<Button>` field is recursively retained and released whenever
the group is copied, assigned, passed, returned, or leaves scope.

See [`zyenlang/v2/std/gui.md`](../zyenlang/v2/std/gui.md) for the complete
window lifecycle, drawing API, event format, and runnable examples. User
`examples/v2_gui_button.zy` demonstrates the retained layout and checked button
callback.

## Module manuals

Each shipped module has its contract beside its source: [`io`](../zyenlang/v2/std/io.md),
[`error`](../zyenlang/v2/std/error.md), [`option`](../zyenlang/v2/std/option.md),
[`list`](../zyenlang/v2/std/list.md), [`process`](../zyenlang/v2/std/process.md),
[`path`](../zyenlang/v2/std/path.md), [`fs`](../zyenlang/v2/std/fs.md),
[`thread`](../zyenlang/v2/std/thread.md),
[`request`](../zyenlang/v2/std/request.md), [`server`](../zyenlang/v2/std/server.md),
[`gui`](../zyenlang/v2/std/gui.md), and [`editor`](../zyenlang/v2/std/editor.md).

## Editor

`std/editor` contains the mutable UTF-8 document state used by the ZyenLang
IDE. It supports checked open/save, held-key navigation, mouse and Shift+Arrow
selection, replacement of selected text, and completion generated from
language words plus identifiers in the current file. The application imports
the module normally and does not declare C symbols itself.
