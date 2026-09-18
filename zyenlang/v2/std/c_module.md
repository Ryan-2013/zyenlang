# `std::c_module`

`c_module::Module` and `c_module::load()` are compiler intrinsics with a
template-dependent type:

```zy
import std::c_module as c_module

let math: c_module::Module = c_module::load("native/math.zlcm.h")
let answer: i32 = math.add(20, 22)
```

The declaration must directly initialize a local or field, and the path must
be a package-contained string literal. The compiler validates the `.zlcm.h`
template, creates a path-hashed hidden wrapper type, and passes its C sources,
headers, include/library paths, and flags to the same C build. It performs no
runtime load and starts no Python process.

See `docs/c_module.md` in the source distribution for the complete template,
type, callback ABI, and security rules.
