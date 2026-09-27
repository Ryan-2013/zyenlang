# c_module: C Interop ABI v3

**English** | [繁體中文](c_interop.zh_TW.md)

`std::c_module` turns a reviewed `.zlcm.h` template into a typed native
namespace. It does not translate C, start a Python process, or load a template
at runtime. Declared adapters and C sources are compiled and linked with the
generated C11 program.

## ABI v3 native module

```zy
import std::c_module as c

native module graphics = c::load("native/graphics.zlcm.h")

fn main() i32 {
    let window: graphics::Window = graphics::open("Demo", 800, 480) catch err {
        return 1
    }
    let pixels: List<u8> = [0, 1, 2]
    graphics::upload(window, c::slice(&pixels))
    return 0
}
```

`native module` is a top-level namespace declaration. The load alias must be
the alias of `std::c_module`; the path must be a package-contained string
literal relative to the declaring `.zy` file. Native symbols use `::`, never
`.`. `zy check` parses and validates the template without invoking a C
compiler.

## ABI v3 template

```c
ZLC_ABI(3)
ZLC_MODULE(graphics)
ZLC_SOURCE("graphics_zy.c")

ZLC_HANDLE(Window, ZLC_DROP(zy_window_drop))
ZLC_ENUM(WindowMode, i32,
    ZLC_CASE(windowed, 0),
    ZLC_CASE(fullscreen, 1))
ZLC_FLAGS(WindowFlags, u32,
    ZLC_CASE(resizable, 4),
    ZLC_CASE(high_dpi, 8))
ZLC_CONST(DEFAULT_WIDTH, i32, 800)

ZLC_FN(open, zy_window_open, optional<owned<Window>>,
    ZLC_PARAM(title, str),
    ZLC_PARAM(width, i32),
    ZLC_PARAM(height, i32),
    ZLC_FAIL(null, zy_window_last_error))

ZLC_FN(upload, zy_window_upload, void,
    ZLC_PARAM(window, borrowed<Window>),
    ZLC_PARAM(data, Slice<u8>))
```

The public C adapter receives `ZL_Handle`, `ZL_Slice`, `ZL_MutSlice`,
`ZL_String`, and fixed-width scalar values from `zyenlang_c_abi.h`. It must
wrap the original library API rather than expose library struct layouts.

### Handle ownership

- `owned<T>` returns a new ARC-owned handle.
- `borrowed<T>` borrows a handle for the current call.
- `borrowed<T, parent>` returns a view that retains the named parent handle.
- `borrowed_static<T>` is valid for process lifetime.
- `consumed<T>` transfers the payload and invalidates every ZyenLang alias.
- `optional<T>` maps C `NULL` to `T | null`.

Use `zl_handle_adopt`, `zl_handle_borrow`, or `zl_handle_borrow_from` in the
adapter. A handle has an atomic control block and a path-derived type tag.
Copies retain the control block; the final release calls the destructor once.
`DROP__(handle)` releases only that binding. A consumed, null, or wrong-type
handle causes a clear runtime error before the original C function runs.

### Buffers and parameter direction

`Slice<T>` and `MutSlice<T>` are synchronous borrows created with
`c::slice(&list)` and `c::mut_slice(&mut list)`. They may appear only directly
as native-call arguments and cannot be stored, returned, captured, or kept by
C after the call. A mutable slice first detaches shared List storage using
copy-on-write.

`out<T>` is omitted from the ZyenLang argument list and appended to the return
value. `inout<T>` is called with `&mut T`. Multiple `out<T>` values become one
fixed-length multiple-result value that can be destructured in ZyenLang.
Only fixed-layout ABI element types are accepted.

### Native failures

`ZLC_FAIL` accepts `null`, `nonzero`, `negative`, or `equal(value)`. It makes
only that function `throws Error`. An optional message symbol has the C
signature `ZL_String fn(void)`; otherwise the runtime includes the native
function name in the default error.

## Legacy ABI v2

The dependent-value spelling remains temporarily available:

```zy
import std::c_module as c_module

fn main() i32 {
    let math: c_module::Module = c_module::load("native/math.zlcm.h")
    return math.add(20, 22) - 42
}
```

It emits one deprecation warning per compilation and should not be used for
new wrappers. The type and load alias must match. The declaration must
directly initialize:

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

## ABI v2 template

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
ZLC_ABI(3)
ZLC_HEADER("relative/header.h")
ZLC_SOURCE("relative/source.c")
ZLC_INCLUDE_DIR("relative/include")
ZLC_LIB_DIR("relative/lib")
ZLC_LIB("library")
ZLC_CFLAG("one-compiler-argument")
ZLC_LDFLAG("one-linker-argument")

ZLC_STRUCT(Name, ZLC_FIELD(field, Type), ...)
ZLC_HANDLE(Name, ZLC_DROP(drop_symbol))
ZLC_ENUM(Name, i32, ZLC_CASE(name, value), ...)
ZLC_FLAGS(Name, u32, ZLC_CASE(name, value), ...)
ZLC_CONST(NAME, Type, literal)
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

`ZLC_SOURCE` accepts `.c`, `.cc`, `.cpp`, and `.cxx`. C files compile as C11;
C++ files compile as C++17 and cause the final native link step to use the C++
linker. Export adapter symbols with `extern "C"`. Use `ZY_CC`/`ZY_CFLAGS` for C,
`ZY_CXX`/`ZY_CXXFLAGS` for C++, and `ZY_LDFLAGS` for final-link configuration.

## Types

Supported template types:

- `i8/i16/i32/i64`, `u8/u16/u32/u64`, `usize`;
- `f32/f64`, `bool`, `str`, and return-only `void`;
- `fn(P...) R` or compatibility spelling `fn(P...)->R`;
- a type declared by `ZLC_STRUCT`.
- ABI v3 handles and `owned/borrowed/borrowed_static/consumed/optional`;
- ABI v3 `Slice<T>`, `MutSlice<T>`, `out<T>`, and `inout<T>`.

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

## Bindgen

```text
zy bindgen vendor/library.h --module graphics --out-dir native \
    -I vendor/include --library raylib
```

The command asks Clang for a JSON AST; it does not infer declarations with
regular expressions. It writes a reviewable `.zybind.toml`, ABI template, and
C adapter pair. Scalar declarations can be enabled automatically. Ambiguous
pointers remain disabled with a TODO until the config states their Handle or
Slice ownership and any length relation. Existing generated files are not
overwritten after manual edits unless `--force` is present.

Parser selection is `ZY_BINDGEN_CLANG`, bundled Zig `zig cc`, then system
Clang. Commands use argument arrays with time and output limits. `zy doctor`
reports both the C compiler and whether the bindgen parser can actually run.

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
