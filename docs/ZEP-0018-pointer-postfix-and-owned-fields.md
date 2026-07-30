# ZEP-0018: Pointer postfix precedence and owned struct fields

Status: Implemented

## Precedence

Postfix operations bind before prefix operations. The postfix group contains
field access, indexing, and calls; the prefix group contains `*`, `&`, and
casts. Parentheses select a different receiver.

```zy
set *car.value = 10;              // *(car.value)
set *((*car_ptr).value) = 20;     // dereference car_ptr, read value, dereference value
let result: int = (*func_ptr)(1, 2);
```

`*func_ptr(1, 2)` means `*(func_ptr(1, 2))`. Since a `ptr<fn(...)>` is not
directly callable, the compiler rejects it and suggests `(*func_ptr)(1, 2)`.
The old short function-pointer call is removed.

Each `*` consumes exactly one `ptr` layer. Therefore a value of type
`ptr<fn()->fn(int,int)->int>` is called as:

```zy
let *factory_ptr: ptr<fn()->fn(int,int)->int> = add2;
let result: int = (*factory_ptr)()(20, 22);
```

`(**factory_ptr)` is a type error. It requires
`ptr<ptr<fn()->fn(int,int)->int>>` because the first dereference already
produces a function value.

## Owned struct fields

An owned pointer default can be declared directly in a struct:

```zy
struct Car {
    let *this.value: ptr<int> = 0;
}
```

Every `Car{}` allocates a distinct ARC-owned `int` cell initialized to zero.
Copying the struct retains the pointer owner. Assignment and scope cleanup
release it through the normal recursive struct ARC helpers.

Nested pointer targets allocate every missing layer:

```zy
struct Chain {
    let *this.value: ptr<ptr<int>> = 10;
}

let chain: Chain = Chain{};
print((str)(**chain.value));
```

`let *this.field` requires a concrete `ptr<T>` and a pointee initializer.
Plain `let this.field: ptr<T>;` remains a normal pointer field initialized to
`None` when omitted.
