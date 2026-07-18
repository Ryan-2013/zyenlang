# ZyenLang v0.1.73 release notes

ZyenLang v0.1.73 is the `zyv173` portable release. It tightens the public
printing API and keeps the same ready-to-run, path-friendly packaging on all
supported desktop platforms.

## Downloads

- Windows x64: `zyv173-windows-x64.zip`
- Linux x64: `zyv173-linux-x64.tar.gz`
- Linux arm64: `zyv173-linux-arm64.tar.gz`
- macOS x64: `zyv173-macos-x64.tar.gz`
- macOS arm64: `zyv173-macos-arm64.tar.gz`

Every archive extracts into one `zyv173` directory and includes the ZyenLang
CLI, bundled C toolchain, cross-platform raylib GUI runtime, examples, docs,
and a prebuilt GUI demo. Python, pip, and a separate C compiler are not
required.

For one-click PATH setup on Windows, extract the archive and double-click
`add-to-user-path.cmd`. On Linux or macOS, run `./add-to-user-path.sh` once.
Both helpers update only the current user's PATH and are safe to run again.

## Language changes

- `print` now accepts exactly one `str` argument.
- Use `(str)value` for explicit formatting or an f-string such as
  `f"value={value}"`.
- Pointer-to-string casts preserve ARC full-expression cleanup, including
  owned pointer temporaries.
- Official examples, syntax documentation, and diagnostics now demonstrate
  the strict printing rule.

This is a source compatibility change: existing `print` calls that pass an
`int`, `float`, `bool`, `List`, pointer, or internal dynamic List value must be
converted to a string explicitly.

## Source packages

The release also publishes a Python wheel, source distribution, and
`SHA256SUMS.txt` for integrity verification.
