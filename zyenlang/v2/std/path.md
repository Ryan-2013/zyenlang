# std/path

`std/path` is the portable v2 path module. It is an ordinary native module and
uses the same public `native source` mechanism available to third-party code.
Paths returned by lexical operations use `/` as their canonical separator on
every platform.

```zy
import <std/path> as path

let source: str = path.join("src", "main.zy")
let name: str = path.basename(source)
let parent: str = path.dirname(source)
let ext: str = path.extension(source)
```

Public operations are `separator`, `normalize`, `join`, `basename`, `dirname`,
`parent`, `extension`, `stem`, `with_extension`, `is_absolute`, `exists`,
`is_file`, and `is_dir`.

The current v2 `str` type is borrowed. Text-returning path functions therefore
use 16 rotating thread-local 4096-byte buffers. A returned path remains valid
until that buffer slot is reused by later path calls. Inputs that cannot fit
produce an empty string. Owned UTF-8 strings will remove this bootstrap limit.
