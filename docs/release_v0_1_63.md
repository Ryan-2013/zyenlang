# ZyenLang v0.1.63 release notes

ZyenLang v0.1.63 adds managed pointers to function-value cells and ships as
path-ready portable bundles named `zyv163`.

## Downloads

Choose the archive for your platform:

- `zyv163-windows-x64.zip`
- `zyv163-linux-x64.tar.gz`
- `zyv163-linux-arm64.tar.gz`
- `zyv163-macos-x64.tar.gz`
- `zyv163-macos-arm64.tar.gz`

Every archive extracts to one `zyv163` folder with `zy` at its root. Run
`add-to-user-path.cmd` on Windows or `add-to-user-path.sh` on Linux and macOS
to add that folder to the current user's `PATH`.

## Function-cell pointers

`ptr<fn(P...)->R>` is now a managed pointer to a checked `ZL_Function` cell:

```zy
fn add(a: int, b: int) -> int {
    return a + b;
}

fn main() -> int {
    let pointer: ptr<fn(int,int)->int> = &add;
    print((*pointer)(20, 22));
    print(*pointer(20, 22));

    let opaque: ptr<void> = pointer;
    print(*(ptr<fn(int,int)->int>)opaque(12, 10));
    return 0;
}
```

- `&add` borrows the compiler-emitted static function cell.
- `let *pointer: ptr<fn(...)> = value;` creates an ARC-owned cell and retains
  a closure environment when needed.
- Calls check null/freed state, the runtime pointer tag, and the exact function
  signature before dispatch.
- A function pointer may be erased to `ptr<void>`, but restoring its concrete
  type requires an explicit cast.
- Different function signatures cannot be cast into one another.

The existing first-class function syntax remains available, including chained
calls such as `factory()(args)` and `(factory())(args)`.

## Compiler fixes

- Nested generic types correctly parse arrows inside `ptr<fn(...) -> T>`.
- Standalone function-pointer call statements are accepted.
- Factory diagnostics distinguish a function from the value returned by
  calling it.
- Shift expressions using `<<` and `>>` are no longer parsed as comparisons.

## Compatibility

The C ABI remains v2. Existing `ZL_Function` and c_module callback wrappers
remain source-compatible. Function-cell pointers use the existing pointer ARC
control blocks; borrowed cells are not automatically freed, while owned cells
release their retained function value when the final reference leaves scope.

ARC does not collect reference cycles. Raw C callback APIs still require a
small compatibility wrapper that stores and invokes `ZL_Function` values.
