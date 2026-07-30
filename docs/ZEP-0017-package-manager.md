# ZEP-0017: ZyenLang package manager

Status: Phase 1 implemented in ZyenLang 0.2.1

## Goal

Add a package workflow with the convenience of `pip install`, while keeping
ZyenLang builds reproducible and preserving the rule that native libraries use
the same public `c_module` mechanism as standard-library code.

The command family is `zy pkg`. `zyenv` remains the version manager for the
compiler itself; it does not install project dependencies.

## User experience

```text
zy pkg init
zy pkg add httpx
zy pkg add github:Ryan-2013/zy-raylib
zy pkg add ../local-library
zy pkg remove httpx
zy pkg install
zy pkg update httpx
zy pkg list
zy pkg publish
```

Packages are imported by package name:

```zy
import <httpx/client> as http;
```

`zy pkg add` updates both `zyproject.toml` and `zy.lock`. A fresh checkout only
needs `zy pkg install`; `zy check`, `zy run`, and `zy build` then resolve the
locked package graph automatically.

## Manifest

```toml
[package]
name = "my-game"
version = "0.1.0"
entry = "src/main.zy"
zyen = ">=0.1.84,<0.2"

[dependencies]
httpx = "^1.2.0"
raylib = { git = "https://github.com/Ryan-2013/zy-raylib", rev = "..." }
local-ui = { path = "../local-ui" }
```

A publishable package contains `zyproject.toml`, `src/`, an optional
`examples/` and `tests/`, and any native `.zlcm.h`, `.h`, and `.c` files below
its package directory. Native metadata is read from the template at build
time; installation never executes package code or build scripts.

## Resolver and lockfile

- Registry versions use semantic versioning.
- Resolution is global for one project: one selected version per package name.
- `zy.lock` records exact version or Git commit, source URL, dependency edges,
  and SHA-256 content digest.
- `zy pkg install --locked` fails if the manifest and lockfile disagree.
- `zy pkg update [name]` is the only command that intentionally changes locked
  versions.
- Path dependencies are development-only and cannot be published as-is.

The first resolver can use deterministic backtracking with highest compatible
versions first. Conflict diagnostics must show the shortest dependency chains
that introduced incompatible constraints.

## Storage and imports

Downloaded archives are immutable and content-addressed under:

```text
~/.zyen/packages/v1/<sha256>/
```

The project stores no copied dependency source. The compiler reads `zy.lock`,
maps `<package/path>` to the immutable cache, and keeps relative imports inside
that package root. A package cannot escape its root through `..`.

`zy pkg cache clean` removes only unreferenced digests after checking known
lockfiles. Offline mode succeeds when every locked digest is already cached.

## Registry and security

The initial registry API needs only package metadata and immutable archive
downloads over HTTPS. Every archive is verified against its lockfile digest.
Publishing requires an authenticated token, rejects an already published
name/version pair, and records package ownership.

Native packages are source distributions in the first version. Platform
metadata uses the existing `ZLC_SOURCE_*`, `ZLC_HEADER_*`, `ZLC_LIB_*`, and
`ZLC_CFLAG_*` rules. Prebuilt native binaries and install-time scripts are out
of scope because they weaken portability and auditability.

## Delivery phases

1. Local manifest, path dependencies, lockfile, cache, and import resolver.
2. Git dependencies pinned by commit and `--offline` / `--locked` modes.
3. Read-only registry with `add`, `install`, `update`, and dependency conflict
   diagnostics.
4. Authentication, ownership, publish/yank, and package search.

Each phase must test Windows, Linux, and macOS path handling and ensure that
the portable ZyenLang archive needs no system Python installation.

## Implemented phase 1

ZyenLang 0.2.1 provides `zy pkg` and the equivalent `zy2 pkg` command with
`init`, `add`, `remove`, `install`, `install --locked`, and `list`. It supports
local path dependencies, transitive dependency locking, SHA-256 addressed
cache entries, compiler resolution of `<package/module>`, and package-root
escape prevention. See [the package manager guide](package_manager.md).

Git dependencies, offline registry archives, version-constraint resolution,
cache garbage collection, authentication, and publishing remain planned for
the later phases above.
