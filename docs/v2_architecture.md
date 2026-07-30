# ZyenLang 0.2 compiler architecture

ZyenLang 0.2 is a parallel compiler. It does not extend the v0.1
line-oriented transpiler.

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
- standard and relative module loading;
- separate debug (`-O0`) and release (`-O2`) C builds;
- a backend protocol and explicit LLVM backend boundary.
- process special values `GET_ARGS` and `GET_EXE`;
- structured native C declarations with checked symbols, sources, and links;
- `while`, direct assignment, `break`, and `continue`;
- linear `Task<T>` values using `spawn call()` and exactly-once `await task`.
- compile-time `typeof value Type` boolean expressions;
- checked `List<T>.get(i32) T throws Error` access.

## Type tests

`typeof` is an expression whose result is a compile-time `bool`:

```zy
let value: i32 = 12
let is_integer: bool = typeof value i32
let is_text: bool = typeof value str
let is_argument_list: bool = typeof GET_ARGS List<str>
```

The operand is type-checked but never evaluated at runtime. Use parentheses
around compound operands, for example `typeof (left + right) i32`. This is a
static type test, not runtime reflection or a string-returning type query.

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
let args: List<str> = GET_ARGS
let executable: str = GET_EXE
```

`GET_ARGS` contains only user arguments, so the executable name is excluded.
`GET_EXE` contains `argv[0]`. Both values borrow process-owned memory and stay
valid for the complete program lifetime. They are reserved and cannot be
shadowed by local declarations.

The v2 standard library receives no private native-module privilege. Future
GUI and HTTP modules must use the same public v2 c_module ABI available to
third-party packages.

`List<T>.get(index)` performs a checked access and returns `T throws Error`.
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

After editable installation, the same interface is available as `zy2`.

## Editor tooling

The supported editor integration is the VS Code extension under
`ide/vscode/zyenlang`. It provides syntax highlighting, completion, navigation,
symbols, hover, signature help, diagnostics, and process-based Run and Build
tasks. The `std/editor` module remains available for applications that need a
mutable UTF-8 text buffer, and is tested independently of a specific IDE shell.

## Next implementation order

1. Complete generic struct instantiation and typed List mutation/iteration.
2. Add ownership IR, owned UTF-8 strings, `Box<T>`, and scope cleanup.
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
