# ZyenLang v0.2.1 release notes

ZyenLang v0.2.1 introduces the first usable ZEP-0017 package workflow and a
consistent visual convention for compiler-provided special words.

## Downloads

- Windows x64 installer: `zyv201-windows-x64.msi`
- Windows x64 portable: `zyv201-windows-x64.zip`
- Linux x64: `zyv201-linux-x64.tar.gz`
- Linux arm64: `zyv201-linux-arm64.tar.gz`
- macOS x64: `zyv201-macos-x64.tar.gz`
- macOS arm64: `zyv201-macos-arm64.tar.gz`
- VS Code: `zyenlang-vscode-0.2.1.vsix`
- Python wheel and source archive

Portable packages and the MSI include the compiler runtime and pinned Zig
0.16.0 toolchain. They do not require a separate Python, GCC, or MSYS2 install.

## Package manager v1

`zy pkg` and `zy2 pkg` now provide:

```text
init
add <local-path>
remove <name>
install
install --locked
list
```

The first phase supports local path dependencies, transitive dependency
edges, deterministic `zy.lock` files, and SHA-256 content-addressed storage at
`~/.zyen/packages/v1`. The compiler resolves checked imports such as
`import <math-lib/math> as math` directly from locked cache entries.

Installation never runs package code or build scripts. Symlinks, special
files, oversized packages, cache tampering, undeclared transitive imports, and
relative imports that escape a package root are rejected.

Registry, Git, search, publish, and install-time scripts are not included in
this first phase.

## Special words

Compiler-provided expressions are now visually distinct from variables:

```zy
let args: List<str> = GET_ARGS__
let executable: str = GET_EXE__
let is_integer: bool = TYPEOF__ value i32
```

The previous `GET_ARGS`, `GET_EXE`, and `typeof` spellings produce migration
diagnostics that name their replacements.

## Verification

Release CI runs the compiler, package-manager integration and security tests,
VS Code language tests, portable package smoke tests, and native builds on
Windows, Linux x64/arm64, and macOS x64/arm64. Assets include SHA-256 checksums
and GitHub artifact provenance.

The Windows MSI is not Authenticode-signed, so SmartScreen may display a
publisher warning.
