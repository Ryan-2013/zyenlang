# ZyenLang v0.1.50-rc.1 release notes

This release candidate packages the function-value, closure, native-module,
managed-pointer, and Windows GUI work for integration testing before v0.1.50.

## Install

```powershell
python -m pip install .
zy version
zy doctor
```

The expected version is `zyenlang 0.1.50rc1`.

## Highlights

- Function values and closures share `ZL_Function` with c_module callbacks.
- Owned pointers and closure environments use atomic ARC.
- `ptr<void>`, nested pointers, explicit casts, checked None/Freed
  dereference, and recursive pointer initialization are supported.
- Native templates load directly through `c_module.load()` and compile their
  C sources without spawning a Python wrapper process.
- The `std/tk` long-lived session runs in-process on Win32/GDI without the old
  file-polling Python/Tk CLI path.

## Migration

Replace the removed native import form:

```zy
import c_module.load("math.zlcm.h") as math;
```

with:

```zy
import <std/c_module> as c_module;

let math: c_module.Module = c_module.load("math.zlcm.h");
```

Rebuild every native wrapper that passed the old `ZL_ptr` layout by value.
Include `zyenlang_c_abi.h` from this release when compiling compatibility
sources.

Managed `ZL_ptr` arithmetic is intentionally rejected. Use string APIs,
`std/buffer`, or a native compatibility adapter with explicit bounds.

## Platform

- Compiler/runtime: Windows tested.
- Native GUI and `std/tk`: Windows only.
- Other platforms are not release-gated in this candidate.

## Verification

The release gate covers function values, closures, ARC scope cleanup,
`ptr<void>`, nested pointer initialization, c_module ABI/callbacks, current
syntax smoke tests, C/EXE output, package installation, and native GUI smoke.
