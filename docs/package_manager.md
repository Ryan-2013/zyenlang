# ZyenLang 0.3 Project and Package Manager

The project manager is part of `zy`; the old `zy pkg` command is removed.

## Create a project

```powershell
zy new my-app
zy new my-library --lib
zy init .
```

`zy new` creates a directory, `zyproject.toml`, `src/`, and an entry file.
`--lib` creates a default `c-source` target instead of `bin`.

## Manifest

```toml
[package]
name = "zy-math"
version = "0.3.0"
zyen = ">=0.3.0"

[build]
default-target = "ffi"
target-dir = "target"

[targets.app]
kind = "bin"
entry = "src/main.zy"

[targets.ffi]
kind = "c-source"
entry = "src/lib.zy"
out-dir = "../consumer/generated"
output-name = "zy_math"

[dependencies]
utils = { path = "../utils" }
net = { git = "https://example.com/net.git", rev = "0123456789abcdef0123456789abcdef01234567" }
```

Target kinds:

- `bin`: executable with `fn main() i32`.
- `c-source`: generated C/header/runtime/native bundle and metadata.
- `staticlib`: platform static library and public header.
- `sharedlib`: platform shared library, public header, and Windows import
  library where supported.

Default output is `target/debug/<target>/` or
`target/release/<target>/`. Target `out-dir` is relative to the project root;
CLI `--out-dir` has highest precedence. `output-name` defaults to the target
key.

## Dependencies

```powershell
zy add ../utils
zy add utils --path ../utils
zy add net --git https://example.com/net.git --rev COMMIT_SHA
zy remove net
zy fetch
zy fetch --locked
```

Path dependencies are resolved to canonical package roots. Git dependencies
require a full 40- or 64-hex commit in `rev`; branches, abbreviated hashes, and
floating tags are rejected. `zy.lock` records source, resolved commit,
content digest, and transitive edges.

`zy fetch` resolves and writes the lock. `--locked` verifies without changing
it. Build/check refuse missing, stale, or digest-mismatched locked packages.

Imports use the dependency alias:

```zy
import utils::math as math
let result = math::add(20, 22)
```

A package can import only its own direct dependencies. Import paths are kept
inside the locked package's `src/` root.

## Build commands

```powershell
zy check
zy check app
zy build ffi
zy build app --release
zy build ffi --out-dir D:\generated
zy run app -- first second
zy test
zy metadata
```

For editor/one-file checking:

```powershell
zy check --file scratch.zy
zy check --file library.zy --library
```

For C emission outside a project target:

```powershell
zy emit --file source.zy --kind c --out-dir generated --output-name module
```

Single-file `zy build file.zy -o output` is intentionally removed. Artifact
kind is explicit and cannot be inferred from an extension.

## Tests

`zy test` first runs targets named `test` or prefixed `test-`. If none exist,
it runs every `tests/**/*.zy` file as an executable test program and stops at
the first nonzero exit status.

## Clean

```powershell
zy clean
zy clean ffi
```

Each successful build records exact absolute artifact file paths. Clean
removes only those files. It refuses symlinks, directories, relative state
paths, and malformed state rather than recursively deleting an output root.
This also protects an external `out-dir` containing unrelated files.

## C and C++ consumers

Only `export fn` enters generated headers:

```zy
export fn add(left: i32, right: i32) i32 {
    return left + right
}
```

The header is valid C11 and C++, with `extern "C"` in C++ mode. v0.3 direct
exports support fixed-width numbers, bool, void, and `ZL_String`. Generic,
class, List, reference, closure, and throwing functions need a non-throwing
scalar/string facade.

The C-source metadata JSON includes source/header names, include paths,
compile flags, link flags, libraries, and exported symbols.

## Limits and trust

The resolver limits dependency count, archive/repository size, source files,
path length, and graph depth. It rejects symlink/package-root escapes, unsafe
names, unsupported URL schemes, mutable Git specifications, and inconsistent
lock records.

Dependencies and their native C files are executable build inputs. A digest
makes a build reproducible; it does not make third-party code trustworthy.
