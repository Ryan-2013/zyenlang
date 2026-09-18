# ZyenLang 0.3 Architecture

## Pipeline

```text
Lexer / Parser
  -> Module Graph + SymbolPath Resolver
  -> Typed AST / IR
  -> Generic Validation + Reachable Monomorphization
  -> Borrow / Ownership Analysis
  -> Cleanup Planning
  -> C11 Backend
  -> Artifact Builder
```

The frontend and semantic pipeline live in `zyenlang/v2`. The directory name
is an implementation path retained from the compiler rewrite; the only public
language version shipped by this tree is 0.3.0.

## Frontend

`lexer.py` emits source-spanned tokens. `parser.py` builds immutable AST nodes
for structured paths, pure-data structs, generic classes, closures, safe
references, error handlers, defer statements, and native declarations.

The parser rejects removed syntax at its point of use. Examples include
string imports, module access through `.`, receiver methods, methods nested in
structs, nested references, old function arrows, `FREE__`, and `SKIP__`.

## Module graph

`modules.py` resolves only three path roots:

- `std::` into the bundled standard-library root;
- `crate::` into the current package `src/` root;
- a direct dependency alias into the immutable package root recorded by
  `zy.lock`.

Every imported definition receives a structured namespace. Visibility checks
compare the definition and use-site module. Graph depth, unique modules,
expansions, source bytes, package roots, and path containment are bounded.

The c_module expansion also runs here. A valid direct
`alias::Module = alias::load("literal.zlcm.h")` initializer becomes a hidden
path-hashed struct containing typed function values. Template metadata is
validated and represented as AST metadata, never shell text.

## Typed semantic IR

`semantic.py` performs symbol collection and visibility, recursive type
resolution, numeric promotion, generic template validation, concrete class
and function specialization, closure capture, optional narrowing, ownership
state, borrow conflicts, and typed expression lowering.

The checker and backend consume the same resolved expression types. This is
important for indirect calls, chained calls, class specialization, native
callbacks, List COW, and cleanup: code generation does not guess a type from
source text.

The IR in `ir.py` is backend-neutral. It describes typed operations rather
than C syntax, preserving a future LLVM backend boundary.

## Ownership model

Managed values are:

- `str`;
- `List<T>`;
- class handles;
- `Box<T>`;
- function values and closure environments;
- tuples, optionals, or structs containing managed values.

Class, Box, strings, Lists, and closure environments use C11 atomic reference
counts. Structs retain value semantics and receive generated recursive
retain/release helpers when necessary. Lists share backing storage until a
mutation triggers copy-on-write detachment.

The semantic phase tracks initialized, moved/dropped, and borrowed bindings.
References never own data. The first reference implementation deliberately
allows only local/parameter borrows and rejects escaping, nested, aggregate,
closure-captured, and cross-thread references.

The cleanup planner feeds explicit IR cleanup points for normal scope exits,
return, break, continue, error propagation, catch/recover, `DROP__`, and
defer. The C backend emits the same ownership operations on every path.

ARC cycles can leak. There is no weak reference or cycle collector in 0.3.

## Function ABI

Every first-class function has this logical layout:

```c
typedef struct ZL_Function {
    ZL_GenericCall call;
    void* env;
    ZL_ArcControl* owner;
    const char* signature;
} ZL_Function;
```

Named functions use an adapter with a null owner. Closures point at an ARC
environment. Signature-specific indirect-call helpers validate null and exact
canonical signatures before invoking `call(env, ...)`. Native callbacks use
the same `ZL_Function` layout through `zyenlang_c_abi.h`.

## Error ABI

Throwing calls lower to typed result carriers. `stop` records the original
source file, line, column, function, and stack frames. `catch` branches on the
result without losing ownership. `recover` creates a normal value of the
original expression type. An uncaught result reaches the generated main
wrapper, prints a red diagnostic, releases its error storage, and exits
nonzero.

Deinitializers and deferred calls are restricted to non-throwing operations,
so cleanup cannot replace an error already in flight.

## C backend and runtime

`backends/c.py` emits portable C11. It defines concrete specializations,
managed helpers, closure adapters, native prototypes, stack frames, and an
optional process entry point. `runtime/zy2_runtime.h` supplies atomic ARC,
strings, callbacks, errors, Unicode scalar operations, and platform runtime
support. `runtime/zyenlang_c_abi.h` is the public C/C++ ABI v2 surface.

Direct calls to ordinary named functions remain ordinary C calls. Only values
whose static type is `fn(...) R` use the indirect ABI.

## Project and dependency layers

`package_manager.py` parses `zyproject.toml`, resolves path and exact-revision
Git dependencies, verifies package limits and content digests, and writes a
deterministic `zy.lock`. Git dependencies are checked out into a content
cache; compilation imports only locked roots.

`artifacts.py` builds explicit target kinds. It never infers artifact kind
from a filename suffix. Default output is
`target/{debug|release}/<target>/`; CLI `--out-dir` takes precedence over the
manifest. The artifact state stores exact output files, and `zy clean` refuses
directories and symlinks rather than recursively deleting an output root.

The toolchain receives structured source, include, library, compile-flag, and
link-flag lists. No native module contributes a shell command.

## C/C++ consumption

`c-source`, `staticlib`, and `sharedlib` targets omit the process main wrapper.
The generated public header uses `extern "C"` under C++, and only includes
supported `export fn` declarations. `ZL_String` retain/release/data/byte-length
functions and callback helpers are exposed by ABI v2.

The C-source bundle includes:

- generated `<name>.c` and `<name>.h`;
- runtime and public ABI headers;
- declared native sources and local quoted headers;
- `<name>.metadata.json` with sources, headers, flags, libraries, and exports.

## Verification

The repository tests parser/type diagnostics, runtime execution, ARC and COW,
references, classes and generics, closures/callbacks, all cleanup exits,
Unicode strings, module visibility, path/Git locking, artifact kinds, C and
C++ consumers, c_module templates, CLI migration errors, and standard-library
integration.

CI runs the Python/Node suites and language examples on Windows, Linux, and
macOS. Linux additionally builds sanitizer fixtures with AddressSanitizer and
UndefinedBehaviorSanitizer when the host compiler supports them.
