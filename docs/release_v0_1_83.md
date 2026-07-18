# ZyenLang v0.1.83 release notes

ZyenLang v0.1.83 is the `zyv183` portable release. It adds structural method
dispatch for heterogeneous Lists while keeping the language surface centered
on variables, functions, and structs.

## Downloads

- Windows x64: `zyv183-windows-x64.zip`
- Linux x64: `zyv183-linux-x64.tar.gz`
- Linux arm64: `zyv183-linux-arm64.tar.gz`
- macOS x64: `zyv183-macos-x64.tar.gz`
- macOS arm64: `zyv183-macos-arm64.tar.gz`

Every archive extracts into one `zyv183` directory and includes the ZyenLang
CLI, bundled Zig C toolchain, cross-platform raylib GUI runtime, examples,
docs, and a prebuilt GUI demo. Python, pip, and a separate C compiler are not
required.

On Windows, extract the archive and double-click `add-to-user-path.cmd`. On
Linux or macOS, run `./add-to-user-path.sh` once. Both helpers update only the
current user's PATH and are safe to run again.

## Language changes

- Lists accept user-defined struct values using ARC-owned runtime boxes.
- `list.get(i).method(...)` and `list.pop().method(...)` use structural
  dispatch when all possible structs provide the same method signature.
- Local List literals and direct mutations carry a hidden possible-type set;
  no interface, trait, union, or public `Any` syntax is added.
- Erased List calls use checked runtime dispatch and fail clearly for scalar or
  incompatible values.
- Dynamic List values can be restored with an exact struct cast such as
  `(Dog)animals.pop()`.
- Zero-field structs now emit portable ISO C storage instead of relying on an
  empty-struct compiler extension.

## Ownership and ABI

ABI v3 extends `ZL_Value` with a boxed struct address, canonical type name, and
ARC owner. `get()` borrows, List-to-List copies retain, `set()` and `clear()`
release removed boxes, and `pop()` transfers ownership. Managed struct fields
are retained and released recursively.

The ABI v2 `ZL_ptr` and `ZL_Function` layouts remain unchanged. Precompiled C
wrappers that pass `ZL_Value`, `Any`, or `ZL_List` by value must be rebuilt;
source c_module wrappers are rebuilt automatically.

## Source packages

The release also publishes a Python wheel, source distribution, and
`SHA256SUMS.txt` for integrity verification.
