# ZyenLang 0.2 package manager

ZyenLang 0.2.1 implements phase 1 of ZEP-0017: local path dependencies,
deterministic lockfiles, a content-addressed package cache, and checked package
imports. The package manager is available as `zy pkg` or `zypkg`.

## Create a project

```powershell
mkdir my-app
cd my-app
zy pkg init --name my-app
```

This creates `zyproject.toml`, `zy.lock`, and `src/main.zy`:

```toml
[package]
name = "my-app"
version = "0.1.0"
entry = "src/main.zy"
zyen = ">=0.2.1"

[dependencies]
```

Package names use lowercase letters, digits, `-`, and `_`. Versions use
semantic versioning.

## Add and import a package

The dependency must have its own `zyproject.toml` and `src/` directory:

```powershell
zy pkg add ../math-lib
zy pkg list
```

`add` records the relative path in `zyproject.toml`, resolves transitive path
dependencies, copies verified source into the immutable cache, and updates
`zy.lock`. Commit both project files.

Given `math-lib/src/math.zy`, import it with:

```zy
import <math-lib/math> as math

fn main() i32 {
    return math.answer()
}
```

Hyphenated final module names require an explicit `as alias`. An import may
only name a direct dependency of the importing project or package; transitive
dependencies are not automatically public.

## Install and remove

```powershell
zy pkg install
zy pkg install --locked
zy pkg remove math-lib
```

`install` resolves the current path sources and intentionally refreshes
`zy.lock`. `install --locked` never changes resolution: it requires the
manifest hash and all package digests to match the existing lockfile. It can
repopulate a missing cache entry only when the path source still has the
locked content.

The cache defaults to `~/.zyen/packages/0.2/<sha256>/`. Set `ZYEN_HOME` to move
the ZyenLang home directory.

## Security and limits

- Package code and build scripts are never executed during installation.
- Symlinks and non-regular files are rejected.
- One package is limited to 10,000 files and 256 MiB of source.
- Cached content is SHA-256 verified during installation and compilation.
- Relative imports inside a package cannot escape its cached package root.
- Native C remains source-based and uses the same public native/c_module ABI
  as standard-library code.

This first version deliberately has no registry, Git source, search, publish,
or install-time scripts. Those are later ZEP-0017 phases; unsupported source
forms receive an explicit diagnostic instead of silently running another
tool.
