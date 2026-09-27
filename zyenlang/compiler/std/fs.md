# std/fs

`std/fs` provides portable filesystem I/O for normal ZyenLang programs. It is
an ordinary native module and uses the same `native source` mechanism available
to application and package authors.

```zy
import std::fs as fs
import std::io as io

fn main() i32 {
    let listing: str = fs::tree(".") catch err {
        io::eprint(err.message)
        recover ""
    }
    io::print(listing)
    return 0
}
```

## API

- `read_text(path: str) str throws Error` reads a UTF-8/text file up to 64 MiB.
- `write_text(path: str, value: str) i32 throws Error` replaces a file.
- `append_text(path: str, value: str) i32 throws Error` appends to a file.
- `tree(path: str) str throws Error` creates a sorted ASCII directory tree.
- `list_dir(path: str) str throws Error` returns sorted names separated by
  newlines; directory names end in `/`.
- `create_dir(path: str) void throws Error` creates one directory.
- `create_dirs(path: str) void throws Error` creates missing parents.
- `copy_file(source: str, destination: str) void throws Error` copies bytes.
- `rename(source: str, destination: str) void throws Error` moves or renames.
- `remove_file(path: str) void throws Error` removes one file.
- `remove_dir(path: str) void throws Error` removes one empty directory.
- `file_size(path: str) i64 throws Error` returns the byte size of a file.

Relative paths are resolved from the running executable's directory, not the
shell working directory or source directory. Absolute paths remain unchanged.

`tree` does not follow symbolic links or Windows reparse-point directories, so
a link cycle cannot recursively trap the process. It limits traversal to 256
levels and output to 16 MiB. File paths use Unicode APIs on Windows.

Returned text is copied into an immutable ARC `str`; later filesystem calls do
not invalidate it.
