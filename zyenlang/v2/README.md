# ZyenLang 0.3 compiler package

This directory retains the historical Python import path `zyenlang.v2` so
existing installation tooling can locate the compiler implementation. Its
contents implement ZyenLang 0.3: parser, module graph and `SymbolPath`
resolver, typed IR, generic specialization, ownership/cleanup analysis, C11
backend, runtime, project manager, artifact builder, and standard library.

The directory name is not a source-language compatibility mode. ZyenLang 0.3
accepts only the v0.3 syntax and emits migration diagnostics for removed v0.2
forms.

See `docs/v3_language_guide_zh_TW.md`, `docs/v3_architecture.md`,
`docs/v3_stdlib.md`, and `docs/migration_v0_3.md` in the source repository.
