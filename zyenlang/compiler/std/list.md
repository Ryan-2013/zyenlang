# Built-in `List<T>` and `std::list`

`List<T>` is a compiler/runtime generic container, not a user-declared struct.
Every concrete element type is checked and specialized; `List<i32>` cannot be
assigned to `List<f64>` without an explicit element-by-element conversion.

```zy
let values: List<i32> = [10, 20]
LIST_PUSH__(values, 30)
LIST_SET__(values, 0, 11) catch err { recover }
let first: i32 = LIST_GET__(values, 0) catch err { recover 0 }
let last: i32 = LIST_POP__(values) catch err { recover 0 }
let size: usize = LIST_LEN__(values)
let shape: List<usize> = LIST_SHAPE__(values) catch err { recover [] }
LIST_CLEAR__(values)
```

All checked element access can throw `Error`. `values[index]` is syntax sugar
for `LIST_GET__(values, index)`. The canonical mutation surface is:

```text
LIST_LEN__  LIST_SHAPE__  LIST_GET__  LIST_SET__
LIST_PUSH__ LIST_POP__    LIST_CLEAR__
```

`LIST_SHAPE__()` returns one dimension per statically nested `List` level:

```zy
let matrix: List<List<i32>> = [[1, 2, 3], [4, 5, 6]]
let shape: List<usize> = LIST_SHAPE__(matrix) // [2, 3]
```

A flat `List<T>` returns `[length]`. An empty `List<List<T>>` returns `[0, 0]`.
Every nested row must have the same dimensions; ragged input throws `Error`
instead of silently choosing the first row's shape.

List storage uses atomic ARC plus copy-on-write. Copying or `CLONE__()` initially
shares a backing buffer; the first mutation of either value detaches it. Popped
managed elements transfer ownership to the caller, while replacement and clear
release removed elements. Nested Lists and List fields in structs/classes are
cleaned recursively.

`std::list` adds two generic module helpers:

```zy
import std::list as list

let count = list::length(values)
let empty = list::is_empty(values)
```

`STR_TO_LIST__(text)` returns one `str` per Unicode scalar value.
