# Changelog

## v0.1.73 - 2026-07-18

### Changed

- `print(expr)` now requires `expr` to have type `str`. Use `(str)value` or an
  f-string for numbers, booleans, Lists, pointers, and dynamic List values.
- F-strings remain `str` expressions and continue to format supported
  interpolation values automatically.
- Pointer-to-string casts preserve full-expression ARC cleanup, so formatting
  an owned pointer temporary releases it after producing the address text.

## v0.1.63 - 2026-07-18

Managed function-cell pointers and checked calls through erased pointers.

### Added

- `ptr<fn(P...)->R>` values backed by checked `ZL_Function` memory cells.
- Borrowed pointers to top-level functions with `&function` and ARC-owned
  function cells with `let *pointer: ptr<fn(...)> = function_value;`.
- Function-pointer calls through `(*pointer)(args)`, `*pointer(args)`, and an
  explicitly restored `ptr<void>`.
- Runtime pointer-tag and exact-signature validation before indirect calls.
- ZEP-0015, executable examples, positive coverage, and compile/runtime error
  fixtures for function-cell pointers.

### Changed

- Function factories and standalone indirect-call statements share the typed
  postfix expression path used by normal function values.
- Nested generic parsing no longer treats the arrow in `fn(...) -> T` as a
  generic closing token.
- Shift operators `<<` and `>>` are no longer misclassified as comparisons.
- Portable archives and extracted folders are named `zyv163`.

## v0.1.53 - 2026-07-18

Path-ready portable release and nested managed-pointer expressions.

### Added

- Portable archives named `zyv153-<platform>` that extract to one `zyv153`
  folder with `zy` directly at its root.
- Idempotent `add-to-user-path` helpers for Windows, Linux, and macOS.
- Recursive pointer expressions such as `**p` and `***p`.
- ARC-preserving `&*managed_pointer` aliases, including `&**p`.
- Pointer ownership escape tests and a Traditional Chinese pointer/function
  value video tutorial.

### Changed

- Portable-release metadata is derived from `pyproject.toml` instead of being
  fixed to v0.1.50.
- Returned pointers, nested pointers, and managed struct fields are documented
  as transferring a retained ownership graph to the caller.

## v0.1.50 - 2026-07-17

First cross-platform portable release.

### Added

- Standalone archives for Windows x64, Linux x64/ARM64, and macOS
  Intel/Apple Silicon with an embedded Python runtime and Zig 0.16.0 C
  toolchain.
- raylib 6.0 backend for `std/tk` drawing, images, text, input events, live
  sessions, and one-shot scenes on Windows, Linux, and macOS.
- Platform-qualified native metadata such as `c_libs_linux`,
  `c_cflags_windows`, and `c_ldflags_macos`.
- Reproducible GitHub Actions builds with per-platform portable smoke tests.

### Changed

- `std/tk` no longer links directly to Win32/GDI and no longer has no-op
  non-Windows stubs.
- Portable `zy run` and `zy build --exe` use the bundled Zig compiler by
  default; `ZY_CC` can select a system compiler.
- Windows temporary executable cleanup no longer turns a successful run into
  an error when virus scanning briefly keeps the file locked.

## v0.1.50-rc.1 - 2026-07-17

Windows preview release.

### Added

- First-class `fn(...) -> T` values for parameters, returns, struct fields,
  chained calls, `None`, named functions, closures, and native callbacks.
- Nested named-function closures with atomic ARC environments.
- Compiler-native `import <std/c_module> as c_module;` and dependent
  `c_module.Module = c_module.load("module.zlcm.h")` declarations.
- ABI v2 `ZL_Function`, `ZL_ptr`, callback helpers, adopted/borrowed native
  pointers, nested `ptr<T>`, and `ptr<void>` casts.
- Recursive owned pointer initialization such as
  `let *p: ptr<ptr<int>> = 10;`.
- Native in-process Win32/GDI `std/tk` session backend for the Windows GUI.
- Traditional Chinese current-syntax reference and release smoke coverage.

### Changed

- Managed pointers, closure captures, managed struct fields, assignments,
  returns, and lexical-scope exits use compiler-inserted ARC.
- `break` and `continue` release managed locals before leaving their scope.
- `str == str` and `str != str` compare contents.
- Normal `zy check`, `zy run`, and `zy build` never invoke the optional Python
  c_module wrapper generator.

### Diagnostics

- Reject malformed pointer casts such as `(ptr<int>value)` with the corrected
  `(ptr<int>)value` spelling.
- Reject managed pointer arithmetic, `ptr<void>` dereference, incompatible
  pointer casts, fn signature mismatches, fn-to-ptr casts, List fn values, and
  obvious stack-pointer returns.

### Compatibility

- ABI v2 changes the by-value layouts of `ZL_ptr` and native function values.
  Precompiled native wrappers from v0.1.49 must be rebuilt.
- `std/tk` and the bundled GUI are Windows only in this release candidate.
- ARC does not collect cycles and is not a complete borrow checker.
