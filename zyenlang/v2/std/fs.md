# std/fs

`std/fs` provides portable filesystem I/O for normal ZyenLang programs. It is
an ordinary native module and uses the same `native source` mechanism available
to application and package authors.

```zy
import <std/fs> as fs
import <std/io> as io

fn main() i32 {
    let listing: str = fs.tree(".") catch err {
        io.eprint(err.message)
        recover ""
    }
    io.print(listing)
    return 0
}
```

## API

- `read_text(path: str) str throws Error` reads a UTF-8/text file up to 64 MiB.
- `write_text(path: str, value: str) i32 throws Error` replaces a file.
- `append_text(path: str, value: str) i32 throws Error` appends to a file.
- `tree(path: str) str throws Error` creates a sorted ASCII directory tree.

`tree` does not follow symbolic links or Windows reparse-point directories, so
a link cycle cannot recursively trap the process. It limits traversal to 256
levels and output to 16 MiB. File paths use Unicode APIs on Windows.

The current v2 `str` type is borrowed. A `read_text` result remains valid until
the next `read_text` call on the same thread; a `tree` result remains valid until
the next `tree` call. Writing does not invalidate either result. Owned strings
will remove this bootstrap lifetime rule later.
