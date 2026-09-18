# ZyenLang 0.3 standard library

Every standard module uses the same module resolver and native declarations
available to third-party packages. Import with a rooted path and call module
functions with `::`:

```zy
import std::io as io
io::print("hello")
```

| Module | Purpose |
|---|---|
| [`io`](io.md) | String-only normal and red terminal output. |
| [`list`](list.md) | Helpers for built-in strongly typed COW `List<T>`. |
| [`option`](option.md) | Helpers for `T | null`. |
| [`path`](path.md) | Portable lexical paths and path queries. |
| [`fs`](fs.md) | UTF-8 text files and bounded directory trees. |
| [`error`](error.md) | Throwing precondition helper. |
| [`process`](process.md) | Process argument/executable facades. |
| [`thread`](thread.md) | Sleep, yield, and CPU count. |
| [`request`](request.md) | Synchronous cross-platform HTTP client. |
| [`server`](server.md) | Small blocking HTTP text server. |
| [`gui`](gui.md) | Cross-platform GUI/drawing classes and callbacks. |
| [`editor`](editor.md) | Native UTF-8 editor buffer used by tooling. |
| [`ptr`](ptr.md) | Safe-reference and Box ownership guide. |
| [`c_module`](c_module.md) | Typed compile-time C compatibility templates. |

The language guide documents `str`, List, references, classes, closures,
tasks, and cleanup because those are compiler/runtime types rather than
ordinary standard-library structs.
