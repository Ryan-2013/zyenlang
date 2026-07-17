# ZEP-0010: First-class function values

Status: Implemented in v0.1.50-rc.1 (ABI v2)

## Semantics

`fn(P1,P2)->R` is a structural type. Parameter names are not part of the
type, signatures must match exactly, and function values cannot be cast to a
different function type or to `ptr<T>`.

Named functions, nested-function closures, struct fields, returned functions,
and c_module callbacks use the same value representation. Any expression with
a static fn type is callable:

```zy
let operation: fn(int,int)->int = pick("sub");
let first: int = operation(10, 3);
let second: int = pick("sub")(10, 3);
let third: int = make_picker()("sub")(10, 3);
```

Each callee and argument expression is evaluated once. Function-value calls
use positional arguments because fn types do not preserve parameter names.
Direct calls to a declared Zyen function may still use named arguments.

A function value may be `None` and compared to `None` with `==` or `!=`.
Calling it produces `None function call: <signature>` and exits; generated C
never calls a null pointer.

## C representation

All function values use `ZL_Function`:

```c
typedef struct ZL_Function {
    ZL_GenericFn call;
    void* env;
    ZL_ArcControl* owner;
    const char* signature;
} ZL_Function;
```

Named functions have no environment or owner. A closure has an environment
owned by atomic ARC. Generated signature-specific helpers validate `signature`
before casting `call` to the exact C function-pointer type.

## Ownership

Function parameters are borrowed. Returning a function transfers one owned
reference. Locals, fields, struct copies, assignments, and closure captures are
retained and released by generated code. An immediately called owned temporary
is released after the call completes.

ARC does not collect cycles. The language has no public retain/release,
lifetime, borrow, or weak-reference syntax.
