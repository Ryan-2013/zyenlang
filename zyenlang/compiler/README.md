# ZyenLang 0.3 compiler package

This is the canonical implementation package for ZyenLang 0.3. Its contents
implement the parser, module graph and `SymbolPath`
resolver, typed IR, generic specialization, ownership/cleanup analysis, C11
backend, runtime, project manager, artifact builder, and standard library.

The old internal `zyenlang.v2` package name was removed because it no longer
described the only compiler shipped by this repository. ZyenLang 0.3 accepts
only v0.3 syntax and emits migration diagnostics for removed v0.2 forms.

See `docs/language_guide_zh_TW.md`, `docs/architecture.md`,
`docs/standard_library.md`, and `docs/migration_v0_3.md` in the source repository.
