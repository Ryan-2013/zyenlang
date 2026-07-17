# ZyenLang v0.1.50 release notes

ZyenLang v0.1.50 is the first download-and-run, cross-platform release.

## Which file to download

- Windows x64: `zyenlang-v0.1.50-windows-x64.zip`
- Linux x64: `zyenlang-v0.1.50-linux-x64.tar.gz`
- Linux ARM64: `zyenlang-v0.1.50-linux-arm64.tar.gz`
- macOS Intel: `zyenlang-v0.1.50-macos-x64.tar.gz`
- macOS Apple Silicon: `zyenlang-v0.1.50-macos-arm64.tar.gz`

Each archive includes the standalone `zy` CLI, Zig 0.16.0, raylib 6.0,
examples, documentation, and a prebuilt `zytk-demo`. It does not require a
Python installation or a separately installed C compiler.

## Cross-platform GUI

`import <std/tk>;` now uses the same raylib-backed C renderer on Windows,
Linux, and macOS. Existing source code keeps the same API. Drawing, frame
buffers, event queues, and session redraws stay in the generated native
process; Python/Tk is not part of the rendering path.

The release covers shapes, UTF-8 text, images, mouse/keyboard/wheel events,
folder-pick requests, one-shot scenes, and long-lived session windows.

## Toolchain selection

Portable builds prefer their embedded Zig toolchain. Source and wheel installs
continue to use `gcc`, `clang`, or `cc` from `PATH`. Set `ZY_CC` to override
either choice.

## Compatibility

- Native interoperability remains ABI v2.
- Existing `std/tk` ZyenLang source does not need changes.
- Linux requires the normal desktop OpenGL/X11 runtime libraries provided by
  the distribution.
- ARC still does not collect reference cycles.
