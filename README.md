# ZyenLang 0.3.0

[繁體中文](README.zh-TW.md) | **English**

ZyenLang is a small, statically typed language with a C11 backend. Version
0.3 is a deliberate hard break: modules and static members use `::`, structs
are pure data, classes provide ARC-managed identity and methods, and safe
references replace source-level raw pointers.

The compiler, project manager, package resolver, runtime, standard library,
and VS Code support all ship through the `zy` command.

## Status

The `v0.3.0` tag is a source release. This delivery does not publish a GitHub
Release, MSI, or portable archives; binary packaging follows only after those
artifacts are independently verified on each platform.

ZyenLang remains experimental. It is ready for testing the design and building
small native programs, but compatibility and Rust-equivalent safety are not
promised yet.

## Install from source

```powershell
git clone https://github.com/Ryan-2013/zyenlang.git
cd zyenlang
python -m pip install -e .
zy --version
zy doctor
```

Native builds need a C11 compiler. `zy` discovers a bundled Zig toolchain
first, then GCC, Clang, or `cc`; `ZY_CC` can override the command.

## First project

```powershell
zy new hello
cd hello
zy run
```

`src/main.zy`:

```zy
import std::io as io

public struct Point {
    public x: i32
    public y: i32
}

public class Counter {
    private value: i32

    public init(value: i32) {
        this.value = value
    }

    public mut fn add(amount: i32) void {
        this.value += amount
    }

    public fn get() i32 {
        return this.value
    }
}

fn main() i32 {
    let point = Point{x: 3, y: 4}
    let counter = Counter(point.x * point.x + point.y * point.y)
    counter.add(17)
    io::print(f"answer={counter.get()}")
    return 0
}
```

The separators are fixed:

- `io::print()` calls a module function.
- `Cache<i32>::create()` calls a class static function.
- `counter.get()` calls an instance method.
- `point.x` reads an instance field.

## Language overview

- Fixed-width numbers, `bool`, and ARC UTF-8 `str`.
- Pure-data value `struct`; ARC identity `class` with `init`, `deinit`, readonly
  `fn`, mutable `mut fn`, and `static fn`.
- Generic functions and classes using reachable monomorphization.
- Strong `List<T>` with an ARC backing buffer and copy-on-write mutation.
- Tuples, multiple returns, destructuring, and narrowed `T | null` values.
- Named functions, closures, callbacks, returned functions, and chained calls.
- Local `&T` and unique `&mut T` references with `CLONE_REF__()` and
  `REF_SET__()`.
- Automatic cleanup, `CLONE__()`, early `DROP__()`, and LIFO `defer`.
- `throws Error`, `stop`, `catch`, and typed `recover`, including source
  locations and runtime stack frames for uncaught errors.
- `spawn`/`await`, portable standard modules, native declarations, and
  compiler-native `.zlcm.h` compatibility templates.

Every intrinsic uses parentheses: `TYPEOF__()`, `FILE__()`, `GET_ARGS__()`,
`LIST_LEN__()`, `STR_SLICE__()`, `CLONE__()`, and `DROP__()` are examples.

See the [language guide](docs/v3_language_guide_zh_TW.md),
[architecture](docs/v3_architecture.md), [standard library](docs/v3_stdlib.md),
and [0.2 migration guide](docs/migration_v0_3.md).

## Project manager

```toml
[package]
name = "hello"
version = "0.3.0"
zyen = ">=0.3.0"

[build]
default-target = "app"
target-dir = "target"

[targets.app]
kind = "bin"
entry = "src/main.zy"
```

```text
zy new / zy init
zy install / zy uninstall / zy list / zy show
zy add / zy remove / zy fetch  (lower-level aliases)
zy check [target]
zy build [target] [--release] [--out-dir PATH]
zy run [target] -- [program arguments]
zy test
zy clean [target]
zy metadata
zy emit --file source.zy --kind c --out-dir PATH
```

Dependencies can come from registry names, local paths, or Git repositories
pinned to an exact revision. `zy install package`, `zy install package==0.1.0`,
`zy install ../package`, and `zy install git+URL@FULL_COMMIT` share one package
workflow. `zy.lock` records commits, digests, and the transitive graph. The
first registry protocol is an immutable Git index; it has no install hooks or
automatic code execution.

Targets can be `bin`, `c-source`, `staticlib`, or `sharedlib`. A `c-source`
target emits C11 source, a C/C++ compatible header, the runtime/native source
bundle, and build metadata. Only `export fn` enters the public C header.

## Native modules

```zy
import std::c_module as c_module

fn main() i32 {
    let math: c_module::Module = c_module::load("math.zlcm.h")
    return math.add(20, 22) - 42
}
```

`c_module::load()` is compile-time only. The validated template becomes a
path-hashed hidden type, and its C sources, headers, libraries, and flags join
the same native build. No Python subprocess or runtime dynamic loader is used.
Fixed-width scalars, strings, `ZLC_STRUCT` values, and `ZL_Function` callbacks
are supported. Raw pointers and `List<T>` are not stable ABI values in 0.3;
wrap them behind an opaque native handle. See [c_module](docs/c_module.md).

## VS Code

`ide/vscode/zyenlang` provides v0.3 highlighting, completion, outline, hover,
signature help, navigation, cancellable live checks, project Run/Build,
single-file C emission, and the optional Ember theme. It does not execute the
compiler in an untrusted workspace.

```powershell
cd ide/vscode/zyenlang
npm test
npm run package
```

Install or update the extension through VS Code's extension registry from the
repository root:

```powershell
python tools/install_vscode_extension.py
```

The installer packages the extension when needed, installs the VSIX with the
official `code` CLI, and verifies that VS Code registered the expected version.
Reload every open VS Code window after updating.

## Security boundary

`zy check` does not invoke the C compiler. `zy build` and `zy run` compile
native sources, so review an unfamiliar project like unfamiliar C code. The
compiler limits source/module-graph size, rejects import and native path
traversal, validates native names, requires exact Git revisions, verifies
dependency digests, and cleans only artifact files recorded in its state.

ARC prevents ordinary dangling owned values, but does not collect cycles or
make external resources thread safe. Safe-reference analysis is intentionally
smaller than Rust's borrow checker. Native code can violate the language's
memory model.

License: MIT.
