# ZEP-0017: Project, dependency, and artifact manager

Status: Implemented by ZyenLang 0.3.0. This revision supersedes the 0.2
`zy pkg` design.

## Goal

One deterministic command should own project creation, exact dependency
resolution, target selection, artifact placement, and safe cleanup. Package
installation must never execute dependency hooks or infer an artifact kind
from a filename extension.

## Commands

```text
zy new PATH [--lib]
zy init [PATH] [--lib]
zy add ALIAS --path PATH
zy add ALIAS --git URL --rev COMMIT
zy remove ALIAS
zy fetch [--locked]
zy check [TARGET]
zy build [TARGET] [--release] [--out-dir PATH]
zy run [TARGET] -- [ARGS...]
zy test
zy clean [TARGET]
zy metadata
zy emit --file SOURCE --kind c --out-dir PATH
```

The old nested `zy pkg` command and `zy build file.zy -o output` form are
removed with migration diagnostics.

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
net = { git = "https://example.com/net.git", rev = "COMMIT_SHA" }
```

Supported target kinds are `bin`, `c-source`, `staticlib`, and `sharedlib`.
Default artifacts go to `target/debug/<target>/` or
`target/release/<target>/`. CLI `--out-dir` overrides target `out-dir`, which
overrides the default.

## Resolution and lockfile

0.3 supports local path dependencies and Git dependencies pinned to an exact
revision. It has no registry, floating version solver, build script, install
hook, or dependency re-export.

`zy.lock` records:

- the root manifest digest;
- each dependency alias and package name;
- source kind and canonical source;
- the resolved Git commit where applicable;
- a SHA-256 package-content digest;
- transitive dependency edges.

The resolver is deterministic and bounded. It rejects cycles, alias
collisions, mutable/unsafe Git revisions, source-root escape, symlinks,
oversized package trees, stale locks, and digest mismatches. Cached packages
are immutable and content addressed below `~/.zyen/packages/0.3/`; Git
checkouts are stored below `~/.zyen/git/0.3/`.

Imports use a declared alias as their root:

```zy
import utils::math as math
let answer = math::add(20, 22)
```

## Artifacts

`c-source` emits generated C, a C/C++ header, runtime/native source bundle,
and JSON metadata for sources, headers, include paths, defines/flags,
libraries, and exports. Only non-generic, non-throwing `export fn` declarations
using the supported C ABI enter the header.

`zy clean` reads an artifact manifest containing exact absolute file paths.
It deletes files only, rejects symlinks/directories/malformed records, and
never recursively deletes an external output directory.

## Security boundary

Fetching does not execute package code. Native `.c` files become trusted
build inputs only when the user builds or runs a target. Exact commits and
digests provide reproducibility and tamper detection, not a sandbox.

Registry publishing, semver range resolution, build hooks, prebuilt native
binaries, and cache garbage collection remain out of scope for 0.3.
