# ZyenLang Repository Structure

```text
zyenlang/
  cli/                    Command-line entry points and doctor checks
  compiler/               Compiler, package manager, runtime, and standard library
  vendor/                 Pinned third-party runtime binaries and licenses
docs/
  history/v0.2/           Archived 0.2 documentation
  zeps/                   Language and tooling proposals
  *.md                    Current 0.3 documentation
examples/
  native/                 Native C interop examples
  *.zy                    Current language and standard-library examples
ide/vscode/zyenlang/      VS Code extension
registry/                 Default package registry index
scripts/windows/          Interactive Windows helper scripts
tests/
  fixtures/               Test-only projects and native fixtures
  test_*.py               Compiler, tooling, security, and packaging tests
tools/                    Release, installer, and repository maintenance tools
```

## Source Ownership

- `zyenlang/compiler/lexer.py`, `parser.py`, and `ast.py` form the frontend.
- `zyenlang/compiler/semantic.py`, `types.py`, and `ir.py` form typed analysis.
- `zyenlang/compiler/backends/` contains code generators.
- `zyenlang/compiler/runtime/` contains private and public C runtime headers.
- `zyenlang/compiler/std/` contains shipped ZyenLang modules and native adapters.
- `zyenlang/compiler/c_module.py` and `bindgen.py` implement C interop.
- `zyenlang/compiler/package_manager.py` and `artifacts.py` build project targets.

The old internal `zyenlang/v2` directory is intentionally absent. Version 0.3
is the only active compiler, so implementation paths do not carry a stale
language-version name.

## Generated Files

The following are local build products and must not be committed:

```text
build/ dist/ .portable/ .pytest_cache/ *.egg-info/ __pycache__/
examples/target/ examples/native/*/target/ ide/**/node_modules/
```

Run `python tools/check_layout.py` to validate required paths and reject retired
layout names. Run `zy clean` for project artifacts recorded by ZyenLang.
