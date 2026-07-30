# ZyenLang 0.2 standard library

This directory is the clean-room standard library for the typed v2 compiler.
It does not import or mirror the v0.1 modules.

## Rules

- Modules are written in ZyenLang wherever an OS or C ABI is not required.
- Collections are generic and strongly typed. `List<any>` must be explicit.
- Native modules use the public structured `native source`, `native link`, and
  typed `native fn` declarations; std modules receive no private loading list.
- Pointer ownership remains a hidden compiler/runtime primitive. The public
  `std/ptr` facade will expose `Box<T>`, `Ref<T>`, and `Raw<T>` only after that
  ABI is implemented and tested.
- Every module must pass `python -m zyenlang.v2 check --library` before it is
  added to the portable release.

## Bootstrap status

| Module | Status | Notes |
|---|---|---|
| `list` | Implemented | Generic `length` and `is_empty`; backed by typed `List<T>`. |
| `error` | Implemented | `require` uses `throws Error` and `stop`. |
| `option` | Implemented | Helpers for the restricted `T | null` optional type. |
| `io` | Implemented | Public `print(str)` and `eprint(str)` wrappers over runtime I/O primitives. |
| `process` | Implemented | Typed access to `GET_ARGS__` and `GET_EXE__`. |
| `thread` | Implemented | Portable sleep, yield, and CPU count; language `spawn` creates OS threads. |
| `request` | Implemented | WinHTTP on Windows and dynamically loaded libcurl on Linux/macOS. |
| `server` | Implemented | Blocking HTTP text server with bounded request count and typed errors. |
| `gui` | Implemented | Cross-platform raylib window, frame, drawing, and event primitives. |
| `editor` | Implemented | UTF-8 text buffer, selection, file I/O, and source-aware completion facade. |
| `ptr` | Pending | Requires the v2 native ABI; no placeholder implementation. |
| `string` | Pending | Will own allocated UTF-8 strings. |
| `channel` | Pending | Requires managed generic payload ownership. |

`request.Response.body` and `.error` are thread-local borrowed strings. They
remain valid until the next request on the same thread. The ownership pass will
upgrade them to owned strings without changing the public fields.
