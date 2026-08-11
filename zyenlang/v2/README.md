# ZyenLang v2

This directory contains the current compiler frontend, typed AST and IR,
semantic checker, C backend, runtime, package manager, and v2 standard library.

Use `zy`, `zy2`, or `python -m zyenlang.v2` for v2 programs. The shared
`zyenlang/cli` package only dispatches commands between this compiler and the
preserved implementation in `zyenlang/v1`.

See `../../docs/v2_architecture.md` and `../../docs/v2_language_guide_zh_TW.md` for the
architecture and current language surface.
