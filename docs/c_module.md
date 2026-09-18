# c_module: C Compatibility Templates

`std::c_module` turns a declarative `.zlcm.h` template into a typed ZyenLang
value during module loading. It does not translate C into ZyenLang, start a
Python CLI, or load a library at runtime. The selected C sources are compiled
and linked with the generated C11 program.

## ZyenLang side

```zy
import std::c_module as c_module

fn main() i32 {
    let math: c_module::Module = c_module::load("native/math.zlcm.h")
    return math.add(20, 22) - 42
}
```

The type and load alias must match. The declaration must directly initialize:

- one local binding; or
- one struct/class field default.

The path must be a string literal relative to the `.zy` file containing the
declaration. It must remain inside that package root and end in `.zlcm.h`.
`c_module::load()` is invalid in assignments, returns, arguments, or arbitrary
expressions. A bare `c_module::Module` parameter/return is invalid because no
template exists to determine its dependent type.

Each resolved template path receives a stable SHA-256-derived hidden type.
Loading the same path repeatedly reuses its type and metadata. Two templates
with the same `ZLC_MODULE` name but different paths remain distinct.

## Template

`math.zlcm.h`:

```c
ZLC_MODULE(math)
ZLC_HEADER("math.h")
ZLC_SOURCE("math.c")

ZLC_FN(add, math_add, i32,
    ZLC_PARAM(left, i32),
    ZLC_PARAM(right, i32))
```

`math.h`:

```c
#ifndef EXAMPLE_MATH_H
#define EXAMPLE_MATH_H
#include <stdint.h>
int32_t math_add(int32_t left, int32_t right);
#endif
```

`math.c`:

```c
#include "math.h"
int32_t math_add(int32_t left, int32_t right) {
    return left + right;
}
```

## Macros

```text
ZLC_MODULE(name)
ZLC_HEADER("relative/header.h")
ZLC_SOURCE("relative/source.c")
ZLC_INCLUDE_DIR("relative/include")
ZLC_LIB_DIR("relative/lib")
ZLC_LIB("library")
ZLC_CFLAG("one-compiler-argument")
ZLC_LDFLAG("one-linker-argument")

ZLC_STRUCT(Name, ZLC_FIELD(field, Type), ...)
ZLC_FN(zy_name, c_symbol, ReturnType, ZLC_PARAM(name, Type), ...)
```

Path/library/flag macros can have `_WINDOWS`, `_LINUX`, `_MACOS`, or `_UNIX`
suffixes. For example:

```c
ZLC_LIB_WINDOWS("user32")
ZLC_LIB_LINUX("m")
ZLC_CFLAG_UNIX("-D_POSIX_C_SOURCE=200809L")
```

One `ZLC_CFLAG`/`ZLC_LDFLAG` contains exactly one argument. Output-changing or
response-file flags are rejected. Metadata is passed as an argument vector,
never concatenated into a shell command.

## Types

Supported template types:

- `i8/i16/i32/i64`, `u8/u16/u32/u64`, `usize`;
- `f32/f64`, `bool`, `str`, and return-only `void`;
- `fn(P...) R` or compatibility spelling `fn(P...)->R`;
- a type declared by `ZLC_STRUCT`.

Compatibility aliases `int -> i32`, `float -> f64`, and `ZL_String -> str`
are accepted. New templates should use fixed-width spelling.

`List<T>` and raw pointer values are deliberately not ABI types in v0.3.
Their layout and ownership operations are compiler internals. Wrap native
memory in functions or an opaque handle class rather than exchanging an
address that ZyenLang cannot validate.

## Struct values

```c
ZLC_STRUCT(NativePoint,
    ZLC_FIELD(x, f64),
    ZLC_FIELD(y, f64))

ZLC_FN(move_point, native_move_point, NativePoint,
    ZLC_PARAM(point, NativePoint),
    ZLC_PARAM(dx, f64),
    ZLC_PARAM(dy, f64))
```

The corresponding C declaration must have the same field order and ABI types.
Struct names and fields are validated C identifiers. Empty structs, duplicate
fields, and direct recursive by-value fields are rejected. Values returned by
C can be inferred and their public fields read in ZyenLang.

## Callback ABI

A template may expose closures directly:

```c
ZLC_FN(invoke, native_invoke, i32,
    ZLC_PARAM(callback, fn(i32)->i32),
    ZLC_PARAM(value, i32))
```

The C prototype receives `ZL_Function` from `zyenlang_c_abi.h`:

```c
#include "zyenlang_c_abi.h"
#include <stdint.h>

typedef int32_t (*callback_i32)(void* env, int32_t value);

int32_t native_invoke(ZL_Function callback, int32_t value) {
    if (!zl_fn_matches(callback, "fn(i32) i32")) return -1;
    return ZL_FN_CALL_AS(callback_i32, callback)(callback.env, value);
}
```

Callback arguments are borrowed for the call. C code that stores one must use
`zl_fn_assign`; replacement/unregister uses `zl_fn_clear`. A callback returned
to ZyenLang must carry an owned reference, usually produced with
`zl_fn_retain`. The environment itself may be a captured closure and is not
implicitly thread safe.

## Build outputs

Executable, static, and shared targets compile template sources directly with
the template include/library paths and flags. A `c-source` target copies
declared sources, declared headers, and recursively discovered quoted local
headers into its source bundle; metadata records compile flags, link flags,
libraries, and exports.

## Security model

The parser rejects unknown macros, malformed nesting, invalid identifiers,
duplicate declarations, unsupported types, non-literal load paths, package
escape, missing files/directories, unsafe output flags, and conflicting C
symbol signatures. These checks prevent template text from becoming arbitrary
compiler command structure.

The C source itself is trusted native code. Once `zy build` or `zy run`
compiles it, it has the same process privileges and memory access as any C
library. `zy check` validates the template but does not invoke the C compiler
or execute native code.
