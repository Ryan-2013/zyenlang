# ZyenLang v0.1.83

**English** | [繁體中文](README.zh-TW.md)

> **Related repositories**
> - **Specs & conventions (ZEPs)**: [zyenlang-zeps](https://github.com/Ryan-2013/zyenlang-zeps)
> - **IDE (dogfood)**: [zyenlang-ide](https://github.com/Ryan-2013/zyenlang-ide)

ZyenLang is an experimental C-like programming language with Python-like
tooling. `.zy` source is transpiled to C, then compiled with Zig, gcc, or
clang.

Current language syntax, ARC pointer rules, function values, closures, and the
native C bridge are collected in the
[Traditional Chinese syntax reference](docs/current_syntax_zh_TW.md).

Aimed at robotics, computer vision, control systems, embedded-style
experiments, and small engine prototyping.

## Portable download

The GitHub release provides ready-to-run archives for Windows, Linux, and
macOS. They contain the standalone `zy` CLI, Zig C toolchain, cross-platform
raylib GUI runtime, examples, docs, and a prebuilt GUI demo. Python, `pip`, and
a separate C compiler are not required.

For v0.1.83, download the `zyv183` archive for your platform. The extracted
folder is also named `zyv183`, and the `zy` executable is directly in that
folder. On Windows, double-click `add-to-user-path.cmd` for one-click setup;
Linux and macOS users can run `add-to-user-path.sh` once.

```powershell
# Windows, after extracting the archive
cd zyv183
.\add-to-user-path.cmd
.\zy.exe run examples\hello.zy
.\zy.exe run examples\tk_portable_smoke.zy
```

```bash
# Linux / macOS, after extracting the archive
cd zyv183
./add-to-user-path.sh
./zy run examples/hello.zy
./zy run examples/tk_portable_smoke.zy
```

See [the portable release guide](docs/portable_release.md).

## Source install

```powershell
cd <repo-root>
python tools\check_layout.py
python -m pip uninstall zyenlang -y
python -m pip install -e .
python tools\install_vscode_extension.py
```

## CLI

```powershell
zy check main.zy             # parse + type-check only
zy run main.zy               # transpile, compile, execute
zy build main.zy -o main.c   # emit C
zy build main.zy -o main.exe # emit .exe
zy c-module gen native.zlcm.h # optional offline wrapper export
```

## Hello world

```zy
fn add(a: int, b: int) -> int {
    return a + b;
}

fn main() -> int {
    let x: int = 10;
    let y: int = 20;
    print((str)add(x, y));
    return 0;
}
```

## Language surface (v0.1.83)

- **Variables**: `let a = v;`, `let a: T = v;`, `const a = v;`
- **Mutation requires `set`**: `set a = v;`, `set a += v;`, `set *p = v;`
- **Control flow**: `if (...) {}`, `else { ... }`, `for (init; cond; step) {}`, infinite loop `for (;;) {}`. There is no `while`.
- **Functions**: `fn name(args) -> T { ... }`. Calls support positional args, named args such as `add(b: 10, a: 5)`, trailing default params, first-class `fn(...) -> T` values, and managed `ptr<fn(...)>` function-cell pointers. Any fn-typed expression can be called, including `pick("sub")(10, 3)`.
- **Structs**: `struct S { let this.field: T; fn method() -> T { ... } }`
- **Structural List calls**: structs stored in one local List can share a method by matching its name and exact signature; no interface declaration is required.
- **Types**: `int`, `float`, `bool`, `str`, `List`, `ptr<T>`, `ptr<void>`, `fn(...) -> T`, `ptr<fn(...)>`, struct types, `void`, `None`. (`Any` is internal to `List`.)
- **Printing**: `print(expr)` accepts only `str`. Convert explicitly with `print((str)value)` or use an f-string.
- **f-strings**: `f"i={i}"` always has type `str`; interpolation formats supported values automatically.
- **Imports**: `import <std/math>;`, `import <std/math> as m;`, `import "lib.zy" as lib;`. Relative paths are resolved against the importing file's folder.
- **Qualified types**: `let car: test.Car = test.Car { model: "Honda" };` — imported user structs live in the global namespace, the alias is just stripped.
- **Multi-line statements**: function signatures, calls, `if` conditions, `for` headers, and list literals can span lines inside their `(...)`, `[...]`, `{...}` delimiters.

## File-scope rules

Only `import`, `struct`, and `fn` are allowed at file scope. There is no
top-level `let` or `const`. If you need a constant, expose it as a zero-arg
function:

```zy
fn max_pwm() -> int {
    return 1000;
}
```

## Mutation

```zy
let x: int = 1;
set x += 1;     // OK
set x = x + 1;  // OK

x += 1;         // ERROR — every mutation needs `set`
```

## Function values and closures

Function values have one representation for named functions, nested-function
closures, struct fields, and native callbacks. They may be passed, returned,
stored, set to `None`, and called through any fn-typed expression:

```zy
let result: int = pick("sub")(10, 3);
let callback: fn(int)->int = make_adder(5);
set callback = None;
```

The chained part of a function-value call uses positional arguments because a
function type contains types, not parameter names. Calling `None` reports a
runtime error instead of jumping through a null C pointer. Closures are created
by nested named functions and capture a read-only snapshot; there is no
anonymous lambda syntax.

## Function-cell pointers

`ptr<fn(P...)->R>` points to a checked `ZL_Function` memory cell. Taking the
address of a top-level function borrows its compiler-emitted static cell;
`let *` creates an ARC-owned cell that can retain a closure:

```zy
let pointer: ptr<fn(int,int)->int> = &add;
print(f"{(*pointer)(20, 22)}");
print(f"{*pointer(20, 22)}");

let opaque: ptr<void> = pointer;
print(f"{*(ptr<fn(int,int)->int>)opaque(12, 10)}");
```

Calls validate pointer state, the runtime cell tag, and the exact function
signature before dispatch. Different function-pointer signatures cannot be
cast into one another.

## Managed pointers

Owned `ptr<T>` and closure environments use compiler-inserted atomic ARC.
Copies retain their owner, assignments release the previous value, and normal,
`return`, `break`, and `continue` exits release managed locals. Parameters are
borrowed and managed return values transfer ownership. ARC does not collect
cycles.

`ptr<T>` converts implicitly to `ptr<void>`. Converting back, or converting
between concrete pointer types, requires an explicit cast. `ptr<void>` cannot
be dereferenced until cast. `mem.free` remains available for forced disposal;
all aliases then observe `Freed`.

`let *name: ptr<T> = value;` allocates an owned cell. Nested pointer types may
be initialized from their final scalar value; the compiler allocates every
layer and ARC releases the complete chain:

```zy
let *value: ptr<int> = 10;
let *chain: ptr<ptr<int>> = 10;
let inner = *chain;
print((str)(*inner)); // 10
```

An existing pointer can initialize the nested cell as well. Prefer fully typed
forms such as `ptr<ptr<int>>`; `ptr<ptr>` loses the innermost static type.

## Structural List dispatch

User-defined structs can be stored directly in a heterogeneous `List`. A call
through `get()` or `pop()` is valid when every inferred element struct provides
the same method with the same parameter, return, and default declarations:

```zy
struct Dog {
    fn bark() -> void {
        print("dog");
    }
}

struct Cat {
    fn bark() -> void {
        print("cat");
    }
}

fn main() -> int {
    let animals: List = [Dog {}, Cat {}];
    for (let i = 0; i < animals.len(); set i += 1) {
        animals.get(i).bark();
    }
    return 0;
}
```

The compiler keeps the possible element types as hidden metadata; there is no
public interface, trait, union, or `Any` syntax. Structural calls use positional
arguments because parameter names are not part of the shared shape. If a List
crosses an erased `List` boundary, dispatch checks the boxed struct type at
runtime and reports a clear error for an incompatible value.

List struct values are value copies stored in ARC boxes. `get()` borrows the
box, `set()` and `clear()` release removed boxes, and `pop()` transfers the
box reference. Cast a dynamic value back to its exact struct type when needed,
for example `let dog: Dog = (Dog)animals.pop();`.

## Standard library

### Round 3: collections
`std/list`, `std/stack`, `std/queue`, `std/map`, `std/set`

### Round 2: tooling
`std/path`, `std/text`, `std/log`, `std/test`, `std/config`, `std/csv`

### Core / runtime
`std/string`, `std/char`, `std/fs`, `std/cmd`, `std/term`, `std/time`,
`std/math`, `std/mem`, `std/ptr`, `std/random`, `std/stats`, `std/c_module`

### Domain modules
`std/tk` (Tk-like GUI bridge), `std/cv`, `std/gpu`, `std/units`,
`std/filter`, `std/trajectory`, `std/robot`, `std/pid`, `std/motor`,
`std/control`, `std/thread`, `std/coroutine`, `std/geometry`,
`std/buffer`, `std/bit`, `std/range`, `std/ease`, `std/check`

## Native C modules

Native C modules are compiler intrinsics. Import `std/c_module`, then directly
initialize a dependent field or local value from a small `.zlcm.h` template:

```zy
import <std/c_module> as c_module;

fn main() -> int {
    let math: c_module.Module = c_module.load("native_math.zlcm.h");
    print((str)(math.add(20, 22)));
    return 0;
}
```

The compiler creates a hidden typed wrapper in memory and automatically links
the template's C headers, sources, libraries, and flags. Normal `zy check`,
`zy run`, and `zy build` do not generate a `.zy` wrapper or invoke a Python
helper. Templates may expose `ZL_List`/`ZL_list`, `ZL_ptr`/`ZL_ptr<T>`,
`fn(...) -> T`, and value structs declared with `ZLC_STRUCT` plus `ZLC_FIELD`.
Their stable ABI v3 layouts come from `zyenlang_c_abi.h`, which native builds
include automatically. C code receives callbacks as `ZL_Function`; use
`zl_fn_assign` when saving one and `zl_fn_clear` when replacing or unregistering
it. `zy c-module gen` remains only for optional offline inspection/export.

```zy
import <std/stack>;
import <std/map>;

fn main() -> int {
    let s: Stack = stack.new();
    s.push_str("first");
    print((str)(s.pop_str()));

    let m: StringMap = map.new();
    m.put("name", "ZyenLang");
    print((str)(m.get("name", "none")));
    return 0;
}
```

## Examples and tests

```powershell
zy run examples\add.zy
zy run examples\pointer_function_tutorial.zy
zy run tests\text_test.zy
```

The Traditional Chinese [pointer and function-value video tutorial](docs/tutorial_pointer_function_video_zh_TW.md)
is both a runnable lesson and a ready-to-record script.

- `examples/` — 92 single-file demos
- `tests/` — 43 test suites
- `apps/` — full programs (`apps/zyide.zy`, `apps/zyide_gui.zy`, `apps/zytk_demo.zy`)

## Layout

```
zyenlang/             # Python transpiler + installed std
zyenlang/std/         # canonical stdlib (loaded by `import <std/...>`)
std/                  # source-tree mirror of stdlib (browse-friendly)
examples/             # 92 .zy demos
tests/                # 43 test suites
docs/                 # specs and notes
apps/                 # full programs
tools/                # install / repair scripts
ide/                  # VSCode + Zed editor configs
```

## Status

v0.1.50 is the first cross-platform portable release. The surface (syntax +
stdlib API) is intentionally small and **locked** — there are no plans to add lambdas,
spread / destructuring, `**kwargs`, comprehensions, or other JS / Python-
style sugar. Effort goes into fixing rough edges, not adding surface area.

## License

MIT. See [`LICENSE`](LICENSE).
