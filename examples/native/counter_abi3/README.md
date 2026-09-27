# Native ABI v3 example

Run from the repository root:

```powershell
zy run app --project examples/native/counter_abi3
```

The example prints `42`. It demonstrates a namespaced native module, an ARC
handle, a nullable native failure, and a synchronous `Slice<u8>` borrow.
