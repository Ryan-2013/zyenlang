# Changelog

## Unreleased

### Added

- Decimal and scientific-notation literals, defaulting to `f64` and honoring
  an immediate `f32` type context.
- `SKIP__(local)` one-block binding promotion and `FREE__(local)` explicit
  binding termination with ARC-aware retain/release behavior.
- `PRINT_CMD__(text, color)` terminal truecolor output and pure-ZyenLang
  `std/io` wrappers for white `print` and red `eprint`.
- `STR_TO_LIST__(text)`, which splits valid UTF-8 into an ARC-managed
  `List<str>` with one Unicode code point per element.
- Typed v2 f-strings with expression interpolation, escaped braces, once-only
  evaluation, and checked formatting for strings, numeric values, booleans,
  and optional strings.
- `FILE__`, a compiler special value containing the absolute path of the `.zy`
  module where it appears.
- `std/gui` retained `Application`, `Panel`, `Label`, `Button`, and `Column`
  structs over the existing cross-platform raylib drawing API.
- List fields in structs, including nested managed structs, managed struct
  parameters/returns/assignment, `List<StructWithList>`, and GUI `ButtonGroup`.
- Individual manuals for every implemented v2 standard-library module.
- v2 function and native-function parameters can declare typed default values.
  Missing positional arguments are filled at the call site, including imported
  functions and a defaulted parameter before a later required parameter.

### Changed

- A catch used as a standalone statement may now use bare `recover` or one
  recovery value of any type because the result is discarded. Value-producing
  catches remain strongly typed, and discarded managed success/recovery values
  are released immediately.
- Generic `TYPEOF__` branches now narrow matching locals during template
  validation, and concrete monomorphizations omit statically impossible
  branches instead of reporting false `List<T>` assignment errors.
- Retired the v0.1 compiler, standard library, examples, tests, editor assets,
  compatibility imports, and `zy1`/`zy2` command split. ZyenLang now ships one
  v0.2 compiler through `zy`, `zyen`, and `python -m zyenlang`.
- Portable archives, Windows MSI packages, CI, `zy doctor`, and the VS Code
  extension now validate and publish only the v0.2 compiler and standard
  library.
- Compiler diagnostics, uncaught `stop`, runtime panics, and `io.eprint` use red
  text on interactive terminals, with `NO_COLOR` and `ZYEN_COLOR` control.
- Mixed numeric arithmetic now uses deterministic promotion. Integer/float
  expressions such as `i32 + f64` produce `f64`, mixed integers choose the
  smallest lossless fixed-width type, and impossible `i64`/`u64` combinations
  require an explicit cast. Floating-point modulo is rejected during checking.
- Equality for user structs, tuples, and optionals now compares values
  structurally and evaluates both operands once. Generic equality is checked
  again after specialization, while unsupported types such as `List<T>` report
  a compiler diagnostic instead of producing invalid C struct comparisons.
- Generic function templates are now checked before monomorphization. Mixing a
  concrete type such as `i32` with unconstrained `T` requires an explicit cast,
  and every concrete specialization validates that cast again.
- VS Code symbol parsing recognizes generic functions and keeps default values
  out of parameter type names and in function signature help.
- Added a complete `std/gui` usage guide covering the cross-platform raylib
  lifecycle, drawing functions, and events.

## v0.2.1 - 2026-07-30

### Changed

- Compiler special words now use uppercase names ending in `__`:
  `GET_ARGS__`, `GET_EXE__`, and `TYPEOF__`. The old spellings report a
  migration diagnostic naming their replacements.

### Added

- ZEP-0017 phase 1 package manager through `zy pkg`, `zy2 pkg`, and `zypkg`.
- Local path dependencies, deterministic `zy.lock`, SHA-256 content cache,
  transitive dependency edges, and checked `<package/module>` imports.
- Package integrity, size, symlink, direct-dependency, and import-root safety
  checks in both installation and compilation.

## v0.2.0 - 2026-07-30

### Added

- New `zy2` compiler frontend, typed IR, semantic checker, C backend, and
  explicit LLVM backend boundary.
- Fixed-width types, receiver methods, tuples, generics, typed Lists,
  optionals, structured errors, tasks, `typeof`, modules, and native C
  declarations.
- Cross-platform v2 process, thread, request, server, GUI, and editor modules.
- Full VS Code extension with completion, navigation, diagnostics, Run, Build,
  C emission, snippets, and the ZyenLang Ember theme.
- Per-user Windows MSI with bundled Zig, automatic PATH setup, clean uninstall,
  deterministic components, and major-upgrade metadata.

- High-level `std/tk` `FONT`, `Window`, `Text`, `Button`, and `Renderer`
  structs over the cross-platform native raylib session.
- ARC-safe `fn()->void` button callbacks, mouse hit testing, custom font
  loading and caching, target FPS control, and allocation-free event parsing.
- List literals inside struct fields, enabling concise retained UI trees such
  as `Renderer { children: [heading, button] }`.
- Platform-specific `.zlcm.h` metadata macros for Windows, Linux, macOS, and
  Unix native headers, sources, include/library paths, libraries, and flags.
- `let *this.field: ptr<T> = value;` owned struct-field defaults with recursive
  ARC allocation for nested pointer targets.
- `std/request`, an ordinary c_module-based synchronous HTTP client using
  WinHTTP on Windows and system libcurl on Linux/macOS.
- ZEP-0017 package-manager plan and ZEP-0018 pointer precedence specification.

### Security

- Restricted compiler execution in untrusted VS Code workspaces and replaced
  shell command strings with process tasks.
- Added diagnostic timeout, input-size, and output-size limits.
- Added source, import-depth, module-count, and graph-expansion limits.
- Rejected archive traversal, symlinks, and unsupported archive entries.
- Pinned Zig downloads, checksums, GitHub Actions commits, and build tool
  versions; release artifacts now receive GitHub provenance attestations.

### Changed

- `std/tk` now imports `std/c_module` and loads `tk.zlcm.h` like an ordinary
  third-party package. The compiler no longer hard-codes `zl_tk_*` prototypes
  or native source metadata.
- Pointer field, index, and call postfix operations now bind before prefix
  dereference, address-of, and casts. Function pointers use the single
  unambiguous `(*pointer)(args)` spelling.

### Fixed

- Module namespace expansion no longer renames a struct method declaration
  that shares a name with a top-level module function.
- Parenthesized dereferenced struct receivers preserve their type through
  field access and method calls, including `(*car_ptr).field`.

## v0.1.83 - 2026-07-18

### Added

- ARC-boxed user struct values in heterogeneous Lists.
- Structural calls through `List.get()` and `List.pop()` when all inferred
  element structs provide one exact shared method signature.
- Runtime-checked dispatch for Lists whose hidden element set is erased.
- Exact casts from dynamic List values back to user struct values.
- ZEP-0016, executable examples, positive coverage, compile-time diagnostics,
  and a runtime type-guard fixture.

### Changed

- Native interoperability is ABI v3. `ZL_Value` now carries boxed struct
  address, canonical type name, and ARC owner fields.
- List copy, replace, clear, and pop paths now retain, release, or transfer
  boxed struct references consistently.
- Zero-field structs emit a portable one-byte placeholder for ISO C compilers.
- Portable archives and extracted folders are named `zyv183`.

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
