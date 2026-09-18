# `std::path`

`std::path` provides portable lexical path operations and filesystem queries.
It is an ordinary native module and receives no standard-library-only loading
privilege.

```zy
import std::path as path

let source: str = path::join("src", "main.zy")
let name: str = path::basename(source)
let parent: str = path::dirname(source)
let extension: str = path::extension(source)
```

Public functions are `separator`, `normalize`, `join`, `basename`, `dirname`,
`parent`, `extension`, `stem`, `with_extension`, `is_absolute`, `exists`,
`is_file`, and `is_dir`. Returned text is copied into immutable ARC `str`
values. Lexical output uses `/` as its canonical separator on every platform.
