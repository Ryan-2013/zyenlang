# ZyenLang 0.2 compiler architecture

ZyenLang 0.2 is a parallel compiler. It does not extend the v0.1
line-oriented transpiler.

The installed package keeps the two compiler generations in explicit version
namespaces:

```text
zyenlang/
  cli/       shared `zy` / `zy1` command dispatch
  v1/        preserved v0.1 transpiler, tools, and standard library
  v2/        current typed compiler, runtime, and standard library
```

Files such as `zyenlang/transpiler.py` are compatibility aliases only. Compiler
development belongs in the matching version directory so v1 maintenance cannot
silently change the v2 frontend or IR.

```text
source
  -> lexer
  -> parser / AST
  -> resolver and type checker
  -> typed IR
  -> lowering and optimization passes
  -> C backend (reference)
  -> LLVM backend (planned)
```

No frontend or IR node contains generated C text. The C backend consumes the
same checked IR that the future LLVM backend will consume.

## Implemented bootstrap

- newline-terminated syntax with semicolons rejected;
- traditional `let name: Type = value` declarations and inference;
- fixed-width integer types with literal range checking;
- structs, field defaults, receiver methods, and public/private access;
- tuple return values and typed destructuring;
- generic function monomorphization;
- strongly typed and nested `List<T>` literals;
- restricted `T | null` optionals and `if let` unwrapping;
- typed `throws Error`, `stop`, `catch`, and `recover`;
- compile-time propagation and a default unhandled-error process exit;
- standard, locked package, and relative module loading;
- separate debug (`-O0`) and release (`-O2`) C builds;
- a backend protocol and explicit LLVM backend boundary.
- process special values `GET_ARGS__` and `GET_EXE__`, plus per-module `FILE__`;
- structured native C declarations with checked symbols, sources, and links;
- `while`, direct assignment, `break`, and `continue`;
- linear `Task<T>` values using `spawn call()` and exactly-once `await task`.
- compile-time `TYPEOF__(value, Type)` boolean expressions;
- checked explicit casts written `(Type)value`;
- shared struct metadata exposed as `value.__attributes__` and `value.__methods__`;
- built-in `LIST_LEN__`, `LIST_PUSH__`, `LIST_SET__`, and checked `List<T>[i32]` operations.

## Type tests

`TYPEOF__` is an expression whose result is a compile-time `bool`:

```zy
let value: i32 = 12
let is_integer: bool = TYPEOF__(value, i32)
let is_text: bool = TYPEOF__(value, str)
```

The operand is type-checked but never evaluated at runtime. Compound operands
use the same form, for example `TYPEOF__(left + right, i32)`. This is a static
type test, not runtime reflection or a string-returning type query.

## Explicit casts

Explicit conversions use `(Type)value` and remain visible at the call site:

```zy
let wide: i64 = (i64)42
let enabled: bool = (bool)wide
let flag: str = (str)enabled

operation() catch err {
    io.print((str)err.message)
    recover
}
```

The first implementation supports numeric-to-numeric conversions, numeric and
`bool` conversions, and numeric/bool to `str`. Numeric formatting uses a
compiler-generated lexical buffer and does not allocate heap memory. An entire
`Error` cannot be cast to `str`; use `(str)err.message` so the selected data is
explicit. Text parsing, pointer casts, optional casts, and user-defined
conversions remain pending.

## Struct metadata

Every concrete struct value exposes two read-only metadata views:

```zy
let fields: List<str> = player.__attributes__
let methods: List<str> = player.__methods__
```

The attribute list contains declared fields in source order. The method list
contains declared receiver methods in source order. Both public and private
members are described, but normal visibility rules still control access.
`__attributes__` and `__methods__` are reserved member names.

The compiler emits one static name table per used struct/category. Instances
do not physically contain two Lists, so metadata does not enlarge every game
object or require ARC allocation. The returned `List<str>` is a read-only view
with program lifetime.

## Standard library reset

The v2 standard library lives in `zyenlang/v2/std`. It is not copied from
v0.1. Generic `list`, `error`, `option`, and `io` modules are the first checked
modules. `ptr`, `string`, `task`, `channel`, `tk`, and `request` are rebuilt
only after their required runtime layer exists.

Console output is a library API, not a bare language function:

```zy
import <std/io> as io

io.print("hello")
io.eprint("diagnostic")
```

Compiler diagnostics and runtime `Error` values retain their defining source
file, line, and column. Unhandled errors render as
`path/to/file.zy:line:column: message`.

## Process special values

Uppercase special values expose process context without pretending that they
were ordinary local variables:

```zy
let args: List<str> = GET_ARGS__
let executable: str = GET_EXE__
let source_file: str = FILE__
```

`GET_ARGS__` contains only user arguments, so the executable name is excluded.
`GET_EXE__` contains `argv[0]`. Both values borrow process-owned memory and stay
valid for the complete program lifetime. They are reserved and cannot be
shadowed by local declarations, and may appear only inside the root `main`.
Helper functions receive them through ordinary parameters. `FILE__` is a
compile-time borrowed string containing the absolute path of the module where
the expression appears; it remains valid in every function. Compiler special
words are uppercase and end in `__`, making them visually distinct from
variables and ordinary functions. `TYPEOF__` is compile-time-only and also
remains valid in every function.

`std/process` receives no exception to that rule. Its explicit facade packages
values supplied by `main`:

```zy
let args = process.args(GET_ARGS__)
let executable = process.executable(GET_EXE__)
```

The v2 standard library receives no private native-module privilege. GUI and
HTTP modules use the same checked `native source`, `native link`, and `native
fn` declarations available to third-party packages.

## Ownership status

The first v2 ARC milestone manages heap cells through `Box<T>`:

```zy
let value = Box(42)
let alias = value
alias.value = 43

let references: usize = value.__strong_count__
```

`Box(value)` creates an atomic ARC control block. Copying a Box retains it;
assignment releases the replaced value; function parameters receive a scoped
retained reference; function returns transfer an owned reference. Normal scope
exit, `return`, `stop`, `break`, `continue`, discarded temporaries, and
temporary call arguments release automatically. The executable wrapper checks
that no ARC control block remains after `main` returns.

`List<T>` uses the same atomic ARC controls around a type-specialized dynamic
storage containing items, length, and capacity. Owned aliases share mutations.
Process arguments and struct metadata are borrowed views; their first mutation
creates owned storage. Nested Lists and Box elements recursively retain and
release. List values may now appear in struct fields through compiler-generated
struct retain/release helpers. Managed structs can be copied, assigned, passed,
returned, nested in another struct, or stored as List elements. Lists inside
tuples and optionals remain rejected.
Self-referential managed types such as `Node { children: List<Node> }` receive a
check-time diagnostic until helper declarations and bodies are emitted in two
phases.

This is deliberately not described as complete general memory management yet.
`Box<T>` may currently contain a primitive, borrowed `str`, or an unmanaged
concrete struct. Strings remain borrowed `const char*`, and native modules
remain responsible for resources they allocate. Awaiting `Task<T>` still joins
the worker and destroys its control block exactly once.

Blocks use lexical scope. Inner declarations are unavailable after their
closing brace, while outer declarations remain visible and mutable. Casting a
`str | null` to `str` evaluates the optional once and produces the contained
string or the literal `"null"`.

Project package imports use `<package/module>`. The module loader accepts only
dependencies declared by the importing project or package, resolves them from
`zy.lock`, verifies their content-addressed cache entry, and prevents relative
imports from escaping a package root. Package installation and lockfile rules
are documented in [the package manager guide](package_manager.md).

`List<T>` is a compiler-recognized generic type constructor, not a standard
library struct. `LIST_LEN__(list)` lowers to the specialized storage length helper.
`list[index]` performs checked access and returns `T throws Error`.
`LIST_PUSH__(list, value)` and `LIST_SET__(list, index, value)` lower directly to the
specialized mutation helpers; `LIST_SET__` returns `void throws Error`.
Out-of-range errors carry the `.zy` call site's file, line, and column.

## Commands

During bootstrap the compiler can run directly from the source tree:

```powershell
python -m zyenlang.v2 check examples\v2_language_tour.zy
python -m zyenlang.v2 run examples\v2_language_tour.zy
python -m zyenlang.v2 run examples\v2_language_tour.zy --release
python -m zyenlang.v2 build examples\v2_language_tour.zy -o build\tour.c
python -m zyenlang.v2 build examples\v2_language_tour.zy -o build\tour.exe --release
```

After editable installation, the current compiler is available as `zy`; `zy2`
is an equivalent compatibility name. Legacy v0.1 source uses `zy1` or
`zy legacy`.

## Editor tooling

The supported editor integration is the VS Code extension under
`ide/vscode/zyenlang`. It provides syntax highlighting, completion, navigation,
symbols, hover, signature help, diagnostics, and process-based Run and Build
tasks. The `std/editor` module remains available for applications that need a
mutable UTF-8 text buffer, and is tested independently of a specific IDE shell.

## Next implementation order

1. Complete generic struct instantiation and recursive managed type cycles.
2. Add owned UTF-8 strings and checked iteration syntax.
3. Extend native declarations into the template-driven v2 c_module ABI.
4. Add closure task arguments, `Channel<T>`, and a coroutine scheduler.
5. Add direct LLVM IR and object emission without changing frontend semantics.

## Native modules

Native modules use ordinary source declarations available to std and third
parties alike:

```zy
native source "bridge.c"
native link windows "winhttp"
private native fn perform(url: str) i32 = "my_perform"
```

Paths are resolved relative to the declaring `.zy` file. C symbols and library
names are validated before invocation, and compiler flags cannot be smuggled
through a link declaration. Importing native code is still a trust decision:
compiled C has the same process permissions as the application.

## Tasks and threads

```zy
fn calculate() i32 {
    return 42
}

let task: Task<i32> = spawn calculate()
let answer: i32 = await task
```

The current C backend runs each spawned call on an OS thread. To keep ownership
deterministic before ARC lands, the call takes no arguments, returns only a
scalar/bool/void value, and its Task must be awaited exactly once in the same
lexical scope. The typed task IR is scheduler-neutral; lightweight coroutine
lowering will reuse the syntax rather than changing user code.
