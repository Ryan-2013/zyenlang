# `std/ptr` v2 contract

ZyenLang 0.2 has no `*` or `&` expression operators. The first ownership
milestone exposes `Box<T>` directly as a compiler-managed type:

```zy
let value: Box<i32> = Box(12)
let alias = value
alias.value = 20
let current: i32 = value.value
let references: usize = value.__strong_count__
```

The public types are:

- `Box<T>` is implemented. It owns one heap cell and releases it when its
  final atomic ARC owner leaves.
- `Ref<T>` is a checked, non-owning borrow whose lifetime cannot escape the
  operation that created it. It is not implemented yet.
- `Raw<T>` is an unsafe C address available only to native compatibility code.
  It is not implemented yet.

Box construction, retain, release, payload access, and scope cleanup are
compiler/runtime intrinsics and compile to inline C operations. They do not
make an external C call per access.

Box payloads are currently limited to primitives, borrowed `str`, and concrete
structs without managed fields. Structs containing List fields now have managed
destructors, but Box still needs a Box-specific recursive payload destructor
before it can own those structs or appear in a struct field, tuple, optional,
or another Box. `List<Box<T>>` is supported for valid unmanaged `T` payloads.
`std/ptr` remains pending as the future facade for `Ref<T>` and `Raw<T>`.
