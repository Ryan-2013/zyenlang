# Migrating from ZyenLang 0.2 to 0.3

0.3 is a hard break. The compiler does not accept both grammars.

## Imports and member paths

```zy
// 0.2
import <std/io> as io
io.print("hello")
let point: model.Point = model.create(1, 2)

// 0.3
import std::io as io
io::print("hello")
let point: model::Point = model::create(1, 2)
```

String imports, angle imports, relative traversal, wildcard/selective imports,
and re-export are removed. Move project code under `src/` and import it through
`crate::...`; declare external roots as dependencies.

## Struct methods

Struct is now pure data.

```zy
// 0.2 receiver method
public fn (point: Point) length_squared() i32 { ... }

// 0.3 module function
public fn length_squared(point: Point) i32 { ... }
```

Use a class when the value needs identity, encapsulated mutation, lifecycle,
or methods.

```zy
public class Counter {
    private value: i32
    public init(value: i32) { this.value = value }
    public mut fn increment() void { this.value += 1 }
    public fn get() i32 { return this.value }
}
```

## Pointer and lifetime syntax

General `ptr<T>`, unary `*`, owned `let *`, `SKIP__()`, and `FREE__()` are not
part of safe 0.3 source.

```zy
let read: &i32 = &value
let write: &mut i32 = &mut value
let copy = CLONE_REF__(read)
REF_SET__(write, 20)

let other = CLONE__(managed)
DROP__(managed)
```

Native raw addresses belong behind a checked C/native handle facade.

## Intrinsics

All intrinsics now use parentheses. `FILE__`, `GET_ARGS__`, and `GET_EXE__`
become `FILE__()`, `GET_ARGS__()`, and `GET_EXE__()`.

List operations are:

```text
LIST_LEN__ LIST_GET__ LIST_SET__ LIST_PUSH__ LIST_POP__ LIST_CLEAR__
```

## Package commands

```text
0.2                         0.3
zy pkg init                 zy init
zy pkg add                  zy add
zy pkg install              zy fetch
zy build file.zy -o out.c   zy emit --file file.zy --kind c --out-dir DIR
zy build file.zy -o app     project target + zy build
```

0.3 manifests use `[targets.<name>]` and explicit `kind`. The backend never
guesses an artifact type from `.c`, `.exe`, `.dll`, `.so`, or `.a`.

## Native code

Direct `native source`, `native link`, and `native fn` remain available to all
modules. c_module syntax changes with the module system:

```zy
import std::c_module as c_module
let math: c_module::Module = c_module::load("math.zlcm.h")
```

The old `import c_module.load(...) as name` form is removed. v0.3 uses fixed
width ABI type names. Untyped `ZL_List` and source-level pointer values are no
longer accepted by c_module; expose opaque native operations instead.

## Error values

The `(str)err` exception is removed. Use `err.message`, `err.file`, `err.line`,
or a standard formatting helper. `recover` must match the caught expression;
bare `recover` is valid only when the expression result is discarded/void.

## Mechanical checklist

1. Replace every import with a rooted `std::`, `crate::`, or dependency path.
2. Replace module/type/static `.` with `::`; keep instance `.`.
3. Move struct behavior to module functions or classes.
4. Replace raw pointers with references or native handle classes.
5. Replace `FREE__` with `DROP__`; remove `SKIP__` and use lexical scope.
6. Add parentheses to process/file intrinsics.
7. Create `zyproject.toml` targets and run `zy fetch`.
8. Run `zy check`, `zy test`, then native builds under sanitizers.
