# Built-in `List<T, Rank>` and `std::list`

`List<T, Rank>` is a compiler/runtime generic container, not a user-declared
struct. `Rank` is an optional compile-time integer from 1 through 16;
`List<T>` means `List<T, 1>`. Shape lengths remain runtime values. Every
concrete element type is checked and specialized; `List<i32>` cannot be assigned
to `List<f64>` without an explicit element-by-element conversion.

```zy
let values: List<i32> = [10, 20]
LIST_PUSH__(values, 30)
LIST_SET__(values, 0, 11) catch err { recover }
let first: i32 = values[0] catch err { recover 0 }
let last: i32 = LIST_POP__(values) catch err { recover 0 }
let size: usize = LIST_LEN__(values)
let shape: List<usize> = LIST_SHAPE__(values) catch err { recover [] }
LIST_CLEAR__(values)
```

All checked element access can throw `Error`. `values[index]` is the canonical
read syntax. `LIST_GET__()` remains available for source compatibility. The
canonical mutation surface is:

```text
LIST_LEN__  LIST_SHAPE__  LIST_FILLED__ LIST_SET__
LIST_PUSH__ LIST_POP__    LIST_CLEAR__
```

Use a ranked type when the lengths come from runtime data:

```zy
fn make_plane<T>(shape: List<usize>, value: T) List<T, 2> throws Error {
    return LIST_FILLED__(shape, value)
}

let shape: List<usize> = [2, 3]
let matrix: List<i32, 2> = make_plane(shape, 0)
let value: i32 = matrix[1][2]
```

`LIST_FILLED__([2, 3], 0)` can infer `List<i32, 2>` directly from the shape
literal. When shape is a variable, the target or return type supplies the rank.
A runtime rank mismatch throws `Error`. The rank limit is 16 and allocation is
rejected when the shape exceeds 100,000,000 leaf elements.

`LIST_SHAPE__()` returns one dimension per statically nested `List` level:

```zy
let matrix: List<i32, 2> = [[1, 2, 3], [4, 5, 6]]
let shape: List<usize> = LIST_SHAPE__(matrix) // [2, 3]
```

A flat `List<T>` returns `[length]`. An empty `List<T, 2>` returns `[0, 0]`.
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
