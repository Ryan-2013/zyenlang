# ARC, pointers, and c_module callback ABI

Status: ABI v2

## Managed pointers

`ZL_ptr` contains its address, runtime type tag, compatibility fields, and an
optional `ZL_ArcControl* owner`. `owner == NULL` means borrowed. Use
`zl_ptr_borrow` for external or stack memory and `zl_ptr_adopt` to transfer a C
resource plus destructor to ZyenLang.

Zyen copies of an owned pointer retain the control block. The last reference
disposes the payload once. `mem.free` forces immediate disposal and preserves
the control block until all aliases leave scope:

- null pointer: `-1`
- borrowed pointer: `-2`
- already disposed pointer: `-3`
- successful forced disposal: `0`

Every alias observes `Freed` after forced disposal. The fixed 4096-entry
pointer registry is no longer used.

`ptr<T>` converts implicitly to `ptr<void>`. Converting back or between
different concrete pointer types requires an explicit cast. A cast preserves
the address, owner, ownership, and runtime type tag. `ptr<void>` cannot be
dereferenced or indexed. A `ptr<ptr<int>>` points to a `ZL_ptr` value; it is not
a raw C `int**`.

`let *name: ptr<T> = value;` allocates an owned cell. When `T` is itself a
pointer and the initializer is the final scalar value, the compiler allocates
the complete pointer chain:

```zy
let *value: ptr<int> = 10;
let *chain: ptr<ptr<int>> = 10;
let inner: ptr<int> = *chain;
print(*inner); // 10
```

An existing pointer is also accepted, for example
`let *chain: ptr<ptr<int>> = value;`. Prefer a complete nested type such as
`ptr<ptr<int>>`; `ptr<ptr>` is retained as an untyped-inner compatibility form
but loses the innermost static type.

Managed `ZL_ptr` values do not support `pointer + offset` or
`pointer - offset`. The owner describes the original payload and currently has
no array-bound metadata, so preserving ARC while manufacturing an unchecked
interior address would be misleading. Use typed indexing only when the pointer
actually refers to an array. String character access uses `string.char_at` or
`string.substring`; raw C pointer arithmetic belongs in a c_module
compatibility adapter.

Plain local values use C automatic storage rather than ARC allocation. Taking
`&local` creates a borrowed pointer with no owner; it is valid only while that
local's lexical scope is alive. The compiler rejects obvious returns of stack
pointers, but ABI v2 is not a complete borrow checker, so borrowed pointers
must not be saved in a closure, container, struct, or C library beyond that
scope.

List retains owned pointers stored in its `Any` cells. `set` and `clear`
release removed pointer values; `pop` transfers the cell's reference. Lists do
not accept function values in ABI v2.

## c_module callbacks

Templates use source-level function types directly:

```c
ZLC_FN(set_callback, zl_set_callback, void,
    ZLC_PARAM(callback, fn(int)->void))
```

The compatibility-layer C prototype uses `ZL_Function`. A callback parameter
is borrowed during the call. C code that saves it must retain it:

```c
static ZL_Function stored;

void zl_set_callback(ZL_Function callback) {
    zl_fn_assign(&stored, callback);
}

void zl_clear_callback(void) {
    zl_fn_clear(&stored);
}
```

Use `zl_fn_matches` before dispatching untrusted values and call through an
exact typedef with `ZL_FN_CALL_AS`. A c_module function that returns a
`ZL_Function` or adopted `ZL_ptr` returns an owned reference that the Zyen
caller takes over.

Raw callback APIs with `user_data` should retain a `ZL_Function` as their
context. APIs without `user_data` need a compatibility-layer callback slot or
another library-specific trampoline. The compiler does not synthesize raw C
trampolines for arbitrary libraries.

Precompiled wrappers that pass the old `ZL_ptr` layout by value must be rebuilt
for ABI v2. Source wrappers are rebuilt automatically by `zy run` and
`zy build`.
