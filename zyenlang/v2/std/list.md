# std/list and built-in List<T>

`List<T>` is a built-in generic container type, not a struct declared by the
standard library and not an `Any` container. `List` is a type constructor and
`T` is its element type. The compiler specializes a distinct native ARC storage
type for every concrete `T`.

```zy
let values: List<i32> = []
LIST_PUSH__(values, 10)
LIST_PUSH__(values, 20)
LIST_SET__(values, 0, 11) catch err { recover }
let first = values[0] catch err { recover 0 }
let size: usize = LIST_LEN__(values)
let last = values.pop() catch err { recover 0 }
```

`LIST_LEN__(values)` and `values[index]` are compiler operations rather than struct
methods. Indexing performs a bounds check and has type `T throws Error`.
The canonical mutation intrinsics are `LIST_PUSH__(list, value)` and
`LIST_SET__(list, index, value)`; `pop`, `remove`, and `clear` remain intrinsic
operations on the built-in container. `capacity` and `is_empty` expose storage
state. Copies share ARC storage and
therefore observe each other's mutations. Nested Lists and Box elements are
retained and released recursively.

Lists may be stored directly in struct fields. A struct containing a List is
itself managed: copies retain every managed field, replacement releases the old
fields, and parameters, returns, `stop`, `break`, `continue`, and scope exit all
run recursive cleanup.

```zy
struct Inventory {
    public items: List<i32> = []
}

let inventory = Inventory{}
LIST_PUSH__(inventory.items, 10)
let alias = inventory
// Both values share the same ARC List storage.
```

Nested structs and `List<StructWithList>` are also supported. A recursive type
such as `Node { children: List<Node> }` is still rejected during `zy check` until
recursive managed type helpers are emitted in separate declaration/body phases.
Lists inside tuples or optionals remain disabled for the same reason.

The old `.len()`, `.get(index)`, `.push(value)`, and `.set(index, value)`
spellings remain compatibility aliases for existing source code. New source
should use the uppercase compiler forms and index syntax.

The `<std/list>` module contains generic helpers such as `length` and
`is_empty`; it does not define the `List<T>` type itself.
