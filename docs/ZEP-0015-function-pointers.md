# ZEP-0015: Managed pointers to function cells

Status: Implemented in v0.1.63

## Summary

`ptr<fn(P...)->R>` is a managed Zyen pointer to a `ZL_Function` cell. It is
distinct from the first-class `fn(P...)->R` value itself:

```zy
let value: fn(int,int)->int = add;
let pointer: ptr<fn(int,int)->int> = &add;

print((str)(value(20, 22)));
print(f"{(*pointer)(20, 22)}");
print(f"{*pointer(20, 22)}");
```

The last two calls are equivalent. The short form is intentional ZyenLang
syntax; it does not use C's postfix-before-unary precedence.

## Storage and ownership

Taking the address of a top-level named function points at the compiler-emitted
static `ZL_Function` cell:

```zy
let borrowed: ptr<fn(int,int)->int> = &add;
```

An owned cell is created with the normal owned-pointer declaration. Storing a
closure retains its environment:

```zy
let operation: fn(int,int)->int = make_operation();
let *owned: ptr<fn(int,int)->int> = operation;
```

The cell is released by pointer ARC. Its `ZL_Function` payload is released by
the cell destructor. `&local_function_value` remains a borrowed pointer with
the same lifetime restrictions as any other address of local storage.

## Type erasure

A function pointer implicitly erases to `ptr<void>`. Restoring it requires an
explicit cast:

```zy
let pointer: ptr<fn(int,int)->int> = &add;
let opaque: ptr<void> = pointer;
let restored: ptr<fn(int,int)->int> = (ptr<fn(int,int)->int>)opaque;
print(f"{*restored(12, 10)}");

// Equivalent immediate form.
print(f"{*(ptr<fn(int,int)->int>)opaque(12, 10)}");
```

The cast preserves the address, owner, ownership state, and runtime type tag.
Calling through the restored pointer validates both the pointer tag and the
`ZL_Function.signature` before dispatch. A mismatched tag, `None`, or `Freed`
pointer produces a runtime error instead of invoking an invalid C address.

Direct casts between different function-pointer signatures are rejected.

## Function factories

A named function expression includes the function's own call layer. Calling a
factory removes one layer:

```zy
fn add1() -> fn()->fn(int,int)->int {
    return add2;
}

let stage2: fn()->fn(int,int)->int = add1();
let stage3: fn(int,int)->int = stage2();
print((str)(stage3(10, 10)));
```

Assigning `add1` without `()` would have type
`fn()->fn()->fn(int,int)->int` and is therefore rejected with a diagnostic
that suggests `add1()`.

## C ABI

`ptr<fn(...)>` points to data storage containing `ZL_Function`; it is not a C
function pointer converted to `void*`. This representation is portable and
can retain closure environments. Native compatibility layers continue to use
`ZL_Function` for callbacks and must adapt raw library callback pointers at the
C boundary.
