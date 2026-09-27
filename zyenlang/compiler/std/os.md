# `std::os`

Portable process and operating-system information:

```text
name() str
architecture() str
process_id() i64
current_dir() str throws Error
home_dir() str throws Error
temp_dir() str throws Error
hostname() str throws Error
env(name: str) str
has_env(name: str) bool
set_env(name: str, value: str) void throws Error
unset_env(name: str) void throws Error
```

`name` returns `windows`, `linux`, `macos`, or `unix`. Environment changes
affect the current process and child processes started afterwards. Use
`has_env` when an empty value must be distinguished from a missing variable.
