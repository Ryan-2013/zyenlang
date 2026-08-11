# ZyenLang 0.2 standard library

This directory is the clean-room standard library for the typed v2 compiler.
It does not import or mirror the v0.1 modules.

## Rules

- Modules are written in ZyenLang wherever an OS or C ABI is not required.
- Collections are generic and strongly typed. `List<any>` must be explicit.
- Native modules use the public structured `native source`, `native link`, and
  typed `native fn` declarations; std modules receive no private loading list.
- `Box<T>` ownership is a tested compiler/runtime primitive. The future
  `std/ptr` facade will add checked `Ref<T>` and native-only `Raw<T>`.
- Every module must pass `python -m zyenlang.v2 check --library` before it is
  added to the portable release.

## Bootstrap status

| Module | Status | Notes |
|---|---|---|
| [`list`](list.md) | Implemented | Growable typed ARC List with checked mutation and nested managed elements. |
| [`path`](path.md) | Implemented | Cross-platform lexical paths plus exists/file/directory queries. |
| [`fs`](fs.md) | Implemented | Portable text file I/O and bounded, cycle-safe directory trees. |
| [`error`](error.md) | Implemented | `require` uses `throws Error` and `stop`. |
| [`option`](option.md) | Implemented | Helpers for the restricted `T | null` optional type. |
| [`io`](io.md) | Implemented | Public `print(str)` and red-terminal `eprint(str)` wrappers. |
| [`process`](process.md) | Implemented | Facade for process arguments plus `FILE__` source-path semantics. |
| [`thread`](thread.md) | Implemented | Portable sleep, yield, and CPU count; language `spawn` creates OS threads. |
| [`request`](request.md) | Implemented | WinHTTP on Windows and dynamically loaded libcurl on Linux/macOS. |
| [`server`](server.md) | Implemented | Blocking HTTP text server with bounded request count and typed errors. |
| [`gui`](gui.md) | Implemented | Qt-like retained widgets over cross-platform raylib drawing and events. |
| [`editor`](editor.md) | Implemented | UTF-8 text buffer, selection, file I/O, and source-aware completion facade. |
| [`ptr`](ptr.md) | Partial | Compiler-managed `Box<T>` is implemented; `Ref<T>` and `Raw<T>` remain pending. |
| `string` | Pending | Will own allocated UTF-8 strings. |
| `channel` | Pending | Requires managed generic payload ownership. |

`List<T>` and structs containing List fields are managed recursively. Lists in
tuples, optionals, and self-referential managed type graphs remain pending.
`request.Response.body` and `.error` are thread-local borrowed strings. They
remain valid until the next request on the same thread. The ownership pass will
upgrade them to owned strings without changing the public fields.
