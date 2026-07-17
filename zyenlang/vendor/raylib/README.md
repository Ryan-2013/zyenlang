# Bundled raylib runtime

ZyenLang ships the raylib 6.0 shared runtime for its cross-platform `std/tk`
backend. The binaries are unmodified files from the official raylib 6.0
release:

- `windows-x64/raylib.dll`
- `linux-x64/libraylib.so`
- `linux-arm64/libraylib.so`
- `macos-universal/libraylib.dylib`

Source and release artifacts: https://github.com/raysan5/raylib/releases/tag/6.0

raylib is distributed under the zlib/libpng license. See `LICENSE` in this
directory.
