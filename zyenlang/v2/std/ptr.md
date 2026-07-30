# `std/ptr` v2 contract

`std/ptr` will be the only ordinary source-level memory API. ZyenLang 0.2 has
no `*` or `&` expression operators.

```zy
import <std/ptr> as ptr

let value: ptr.Box<i32> = ptr.box(12)
value.set(20)
let current: i32 = value.get()
```

The public types are:

- `Box<T>` owns one heap value and releases it when its final owner leaves.
- `Ref<T>` is a checked, non-owning borrow whose lifetime cannot escape the
  operation that created it.
- `Raw<T>` is an unsafe C address available only to native compatibility code.

The compiler retains private ownership primitives so it can insert moves and
cleanup at every return and scope exit. The public module will be implemented
through the v2 native ABI, with `get` and `set` emitted as intrinsics or
`static inline` operations. A normal external C call per access is forbidden
because it would block optimization.

This file is a contract, not a placeholder implementation. `std/ptr` becomes
importable only when the ownership pass, escape checks, and native ABI tests
all pass.
