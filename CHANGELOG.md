# Changelog

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
