# ZyenLang v0.1.53 release notes

ZyenLang v0.1.53 is distributed as a path-ready `zyv153` folder. Download the
archive for your platform, extract it, and either run `zy` from that folder or
add the folder itself to `PATH`. Python and a separate C compiler are not
required.

## Which file to download

- Windows x64: `zyv153-windows-x64.zip`
- Linux x64: `zyv153-linux-x64.tar.gz`
- Linux ARM64: `zyv153-linux-arm64.tar.gz`
- macOS Intel: `zyv153-macos-x64.tar.gz`
- macOS Apple Silicon: `zyv153-macos-arm64.tar.gz`

Every archive extracts to a folder named `zyv153`. It includes the standalone
`zy` CLI, Zig 0.16.0, raylib 6.0, examples, documentation, and a prebuilt GUI
demo. Allow roughly 400 MB of free space while extracting; Windows Explorer or
PowerShell may take a few minutes because the Zig toolchain contains many small
files.

## Add to PATH

On Windows, open the extracted folder and run:

```powershell
.\add-to-user-path.cmd
```

On Linux or macOS:

```bash
./add-to-user-path.sh
```

Open a new terminal and verify the installation with `zy version` and
`zy doctor`.

## Language changes

- Recursive managed pointer dereference expressions: `**p`, `***p`, and
  deeper chains.
- ARC-preserving managed aliases such as `let *leaf: ptr<ptr> = &**p;`.
- Returned pointers, nested pointers, and structs containing managed pointers
  retain all managed values reachable from the returned value.
- New pointer and function-value tutorial with runnable examples.

## Compatibility

- Native interoperability remains ABI v2.
- Existing ZyenLang source remains compatible.
- Linux still requires the normal desktop OpenGL/X11 runtime libraries.
- ARC does not collect reference cycles, and borrowed pointers still follow
  their source lifetime.
