# ZEP-0016: Structural method dispatch for List values

Status: Implemented in v0.1.83

## Summary

A heterogeneous `List` may store user-defined struct values. A method can be
called directly through `List.get()` or `List.pop()` when every possible
element struct provides that method with one exact shared signature:

```zy
struct Dog {
    fn bark() -> void {
        print("dog");
    }
}

struct Cat {
    fn bark() -> void {
        print("cat");
    }
}

fn main() -> int {
    let animals: List = [Dog {}, Cat {}];
    for (let i = 0; i < animals.len(); set i += 1) {
        animals.get(i).bark();
    }
    return 0;
}
```

The feature is structural: it adds no interface, trait, inheritance, public
union, or public `Any` syntax.

## Static shape

For a named local List, the compiler conservatively records the element types
seen in its literal, `append`, `append_ptr`, and `set` operations. A structural
call is accepted only when:

- every inferred element type is a user-defined struct;
- every struct defines the requested method;
- parameter types, return type, and default expressions are exact;
- the call uses positional arguments.

Method parameter names are not part of the shared shape. External c_module
structs cannot be boxed directly; a ZyenLang facade struct establishes the
language-side ownership and method rules.

## Erased Lists

A plain `List` parameter has no closed element set. Its structural call is
compiled only when the program has one unambiguous signature for that method
name. The generated dispatcher checks the boxed runtime type before invoking a
method. A scalar, pointer, or incompatible struct produces a runtime error
instead of an invalid C call.

Passing a locally inferred List to a plain `List` parameter erases its hidden
set because the callee may mutate it. This keeps the public `List` type simple
while preserving checked behavior across function boundaries.

## Representation and ownership

ABI v3 adds `ZL_VALUE_STRUCT` and three fields to `ZL_Value`: boxed address,
canonical struct name, and `ZL_ArcControl*` owner. Appending a struct copies its
value into a heap box. Managed fields are retained recursively; the box
destructor releases them before freeing storage.

- `List.get()` returns a borrowed `Any` view.
- Copying that view into another List retains the box.
- `List.set()` and `List.clear()` release removed boxes.
- `List.pop()` transfers its box reference to the caller.
- `(StructType)value` checks the exact runtime type before restoring a value.

The method receiver points at the boxed value, so mutating a struct method
updates the value stored in the List. A source struct used to initialize the
List remains a separate value copy.

## Limits

This proposal does not make List itself ARC-managed and does not add cycle
collection. Programs should still call `clear()` when deterministic release of
managed List elements matters. List continues to reject a bare `fn(...)`
value; a callback can be stored inside a user struct field instead.
