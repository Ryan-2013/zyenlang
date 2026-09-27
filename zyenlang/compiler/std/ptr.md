# Safe references and owned heap values

Normal ZyenLang 0.3 code does not expose source-level raw pointers. Use local
borrows when a function should observe or mutate an existing binding:

```zy
let value: i32 = 10
let read: &i32 = &value
let copy: i32 = CLONE_REF__(read)

let write: &mut i32 = &mut value
REF_SET__(write, 20)
```

`&T` permits multiple readers. `&mut T` is unique and excludes all other uses
of its owner for the borrow lifetime. References cannot be null, nested,
returned, captured, stored in List/struct/class fields, or sent to another
thread. They do not own or release the referred value.

`Box<T>` remains an ARC-owned heap cell for explicit shared identity:

```zy
let box = Box(12)
let alias = CLONE__(box)
alias.value = 20
let count: usize = box.__strong_count__
DROP__(alias)
```

Classes, closures, and Box values increment atomic ARC on clone. `DROP__()`
releases one local owner early and makes that binding uninitialized until it is
assigned again. Raw addresses exist only inside native/C compatibility code;
they are not a safe-language type.
