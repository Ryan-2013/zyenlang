# ZyenLang v0.2.0 release notes

ZyenLang v0.2.0 is the first formal release of the new typed compiler pipeline.
It ships as `zy2` beside the legacy-compatible `zy` command.

## Downloads

- Windows x64 installer: `zyv200-windows-x64.msi`
- Windows x64 portable: `zyv200-windows-x64.zip`
- Linux x64: `zyv200-linux-x64.tar.gz`
- Linux arm64: `zyv200-linux-arm64.tar.gz`
- macOS x64: `zyv200-macos-x64.tar.gz`
- macOS arm64: `zyv200-macos-arm64.tar.gz`
- VS Code: `zyenlang-vscode-0.2.0.vsix`
- Python wheel and source distribution

All native packages include Zig 0.16.0. Verify downloads with
`SHA256SUMS.txt`; GitHub artifact attestations provide build provenance.

## Highlights

- Separate lexer, parser, typed AST/IR, semantic checker, C backend, and LLVM
  backend boundary.
- Fixed-width numbers, typed/inferred variables, structs, visibility,
  receiver methods, tuples, generics, typed Lists, optionals, and `typeof`.
- Structured errors through `throws Error`, `stop`, `catch`, and `recover`.
- OS-thread tasks through `spawn` and exactly-once `await`.
- Standard process, thread, HTTP client/server, GUI, and editor modules.
- Normal relative and standard imports with source-aware diagnostics.
- VS Code completion, navigation, symbols, hover, signature help, live
  diagnostics, Run, Build, C emission, snippets, and the Ember theme.

## Security hardening

- Untrusted VS Code workspaces cannot start compiler processes.
- VS Code Run and Build use process tasks rather than shell command strings.
- Live diagnostics enforce time, input-size, and output-size limits.
- Module loading limits source size, import depth, unique modules, and graph
  expansions to resist accidental or hostile resource exhaustion.
- Native C symbols and link names are validated before C emission.
- `zyenv` and Zig extraction reject path traversal, symlinks, and unsupported
  archive entries.
- Zig download URLs and SHA-256 values are pinned in the repository.
- Release actions are pinned to immutable commits and artifacts are attested.
- Windows MSI installation is per-user and restores PATH on uninstall.

## Compatibility and limitations

The v0.2 syntax intentionally differs from v0.1. Existing programs continue to
use `zy`; new programs should use `zy2`.

Native C is an explicit trust boundary: `check` does not compile it, while
`build` and `run` do. ARC does not prevent cycles and cannot guarantee the
lifetime rules of borrowed values supplied by external C libraries.

The v0.2.0 MSI is not Authenticode-signed, so Windows SmartScreen may warn.
Use the published SHA-256 file and GitHub attestation to verify the download.
