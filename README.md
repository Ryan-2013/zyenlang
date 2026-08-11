# ZyenLang 0.2.1

[繁體中文](README.zh-TW.md) | **English**

ZyenLang is a compact, statically checked language that compiles to C. Its
design goal is to build structured programs from a small language surface:
values, structs, and functions.

Version 0.2 introduces a new lexer, parser, typed AST/IR, semantic checker,
module loader, and C backend. The compiler is invoked with `zy`.

## Download

The [GitHub Releases](https://github.com/Ryan-2013/zyenlang/releases) page
provides:

- `zyv201-windows-x64.msi`: per-user Windows installer with PATH setup;
- `zyv201-windows-x64.zip`: portable Windows package;
- `zyv201-linux-x64.tar.gz` and `zyv201-linux-arm64.tar.gz`;
- `zyv201-macos-x64.tar.gz` and `zyv201-macos-arm64.tar.gz`;
- `zyenlang-vscode-0.2.1.vsix`: VS Code completion, navigation, diagnostics,
  Run, Build, and the optional ZyenLang Ember theme;
- Python wheel and source archive.

Portable packages and the MSI include the compiler runtime and a pinned Zig
0.16.0 C toolchain. Python, `pip`, GCC, and MSYS2 are not required.

The MSI installs for the current user under
`%LOCALAPPDATA%\Programs\ZyenLang`, updates only the user PATH, and removes its
PATH entry on uninstall. Release assets include SHA-256 checksums and GitHub
artifact provenance. The v0.2.1 MSI is not yet Authenticode-signed.

## Hello world

```zy
import <std/io> as io

struct Counter {
    public value: i32 = 0
}

public fn (counter: Counter) add(amount: i32) i32 {
    return counter.value + amount
}

fn main() i32 {
    let counter: Counter = Counter{value: 40}
    let answer: i32 = counter.add(2)
    if answer == 42 {
        io.print("hello from ZyenLang 0.2")
    }
    return 0
}
```

```powershell
zy check main.zy
zy run main.zy
zy run main.zy -- first-argument second-argument
zy build main.zy -o main.exe --release
zy build main.zy -o main.c
```

## Language

ZyenLang 0.2 currently provides:

- fixed-width integers, floats, `bool`, `str`, and explicit types;
- typed f-strings such as `f"answer={value}"` with once-only interpolation;
- inferred or typed `let` declarations with newline-terminated statements;
- structs with defaults, public/private fields, and receiver methods;
- ARC-managed `List<T>` struct fields with recursive copy and cleanup;
- named top-level function values, checked indirect calls, and struct callback fields;
- tuple returns and typed destructuring;
- generic functions and strongly typed `List<T>` values;
- `T | null` optionals and `if let` unwrapping;
- `throws Error`, `stop`, `catch`, and `recover`;
- `while`, assignment, `break`, and `continue`;
- linear `Task<T>` values with `spawn` and exactly-once `await`;
- `TYPEOF__(value, Type)` compile-time checks and per-module `FILE__` paths;
- standard, package, and relative modules plus checked native C declarations.

See [the v0.2 architecture](docs/v2_architecture.md),
[standard library guide](docs/v2_stdlib.md), and
[language tour](examples/v2_language_tour.zy).

## Package manager

The first ZEP-0017 package workflow supports local path dependencies with a
deterministic lockfile and SHA-256 content cache:

```powershell
zy pkg init --name my-app
zy pkg add ../math-lib
zy pkg install --locked
zy pkg list
```

Packages are imported from their `src/` directory with
`import <math-lib/math> as math`. See the
[package manager guide](docs/package_manager.md) for manifests, cache safety,
and current local-path limits.

## Standard library

The v0.2 standard library includes typed list/error/option helpers, process
arguments, portable filesystem and path operations, OS threads, HTTP client and
server modules, and a cross-platform GUI backend with retained `Application`,
`Panel`, `Label`, `Button`, and `Column` widgets. Every module uses the same
import mechanism as third-party modules.

```zy
import <std/request> as request
import <std/io> as io

fn main() i32 {
    let response: request.Response = request.get("https://example.com")
    if response.ok() {
        io.print(response.body)
    }
    return 0
}
```

A complete filesystem CLI is included as `examples/v2_file_tree.zy`:

```powershell
zy run examples/v2_file_tree.zy -- . tree.txt
```

## VS Code

Install `zyenlang-vscode-0.2.1.vsix` with **Extensions: Install from
VSIX...**. The extension does not execute compiler commands in untrusted
workspaces. Live checks are debounced, cancellable, size-limited, output-limited,
and time-limited; Run and Build use VS Code process tasks instead of shell
command strings.

## Source install

```powershell
git clone https://github.com/Ryan-2013/zyenlang.git
cd zyenlang
python -m pip install -e .
zy --version
python -m pytest -q
```

## Security boundary

`zy check` parses and type-checks code but does not compile or execute native
C. `zy build` and `zy run` intentionally compile `native source` declarations,
so treat an unfamiliar ZyenLang project like an unfamiliar C project and
review it before building. Module depth, module count, source size, archive
extraction, native symbol names, and linker library names are validated.

ZyenLang remains an experimental language. ARC does not make borrowed native
resources or external C libraries memory-safe, and the project does not claim
Rust-equivalent safety.

The compiler implementation and standard library live in `zyenlang/v2`. The
repository and release packages contain only the ZyenLang 0.2 language line.

License: MIT.
