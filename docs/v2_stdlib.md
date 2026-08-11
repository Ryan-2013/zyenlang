# ZyenLang 0.2 standard library

All modules use explicit imports and the same loader as third-party code.

## Process

```zy
import <std/process> as process

let args: List<str> = process.args(GET_ARGS__)
let executable: str = process.executable(GET_EXE__)
```

The special values `GET_ARGS__` and `GET_EXE__` contain borrowed process-lifetime
data and may appear only inside `main`. The process facade accepts explicitly
passed values without receiving a compiler privilege.

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
elements. A managed List cannot yet be placed inside a struct, tuple, or
optional because those aggregate destructors are the next ownership milestone.

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

See [`zyenlang/v2/std/gui.md`](../zyenlang/v2/std/gui.md) for the complete
window lifecycle, drawing API, event format, and a runnable example.

## Editor

`std/editor` contains the mutable UTF-8 document state used by the ZyenLang
IDE. It supports checked open/save, held-key navigation, mouse and Shift+Arrow
selection, replacement of selected text, and completion generated from
language words plus identifiers in the current file. The application imports
the module normally and does not declare C symbols itself.
