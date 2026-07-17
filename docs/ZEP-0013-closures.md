# ZEP-0013: Nested-function closures

Status: Implemented with ARC in v0.1.50-rc.1 (ABI v2)

A nested named function creates a closure. Captures are read-only snapshots;
assigning the capture name inside the nested function changes only its local
copy. Anonymous lambda syntax is not part of this ZEP.

```zy
fn make_adder(delta: int) -> fn(int)->int {
    fn add_delta(value: int) -> int {
        return value + delta;
    }
    return add_delta;
}
```

The compiler lifts `add_delta` to a C function whose first parameter is
`void* env`. It allocates a typed environment, retains managed captures, and
stores the environment in `ZL_Function`. The environment's ARC destructor
recursively releases captured function values, owned pointers, and structs
that contain managed fields.

Closure environments use C11 atomic reference counts. This makes retaining and
releasing a callback control block safe across C threads; captured data is not
automatically synchronized. Cycles can leak but cannot cause early release.

Normal scope exit, `return`, `break`, and `continue` release owned closure
locals. Returned closures transfer ownership to the caller. Parameters remain
borrowed for the duration of a call.
