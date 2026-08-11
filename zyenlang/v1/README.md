# ZyenLang v1

This directory contains the preserved v0.1 compiler implementation, native
module loader, Python-backed legacy tools, and bundled v1 standard library.

Use `zy1`, `zy legacy`, or `python -m zyenlang.v1` for v1 programs. Historical
imports such as `zyenlang.transpiler` remain compatibility aliases, but new
tooling should import `zyenlang.v1.transpiler` explicitly.

The v1 compiler remains supported for existing source. New language work is
implemented in `zyenlang/v2`.
