# ZyenLang for Visual Studio Code

The official editor extension for ZyenLang 0.3.

## Features

- ZyenLang 0.3 highlighting for `::` paths, pure structs, classes, references, intrinsics, and native declarations
- completion for keywords, types, imports, module symbols, class methods, struct/class fields, and standard modules
- hover signatures, parameter hints, Go to Definition, code-only Find References, document highlights, outline, and workspace symbols
- live `zy check` diagnostics for the current unsaved buffer
- Run Project Target, Build Project Target, and Emit C Source commands
- optional **ZyenLang Ember** theme using `#20201F` with `#D97757` accents

The extension debounces checks while typing and cancels stale compiler processes.
Compiler errors remain authoritative; the extension's lightweight index is only
used for responsive navigation and completion.

Compiler execution is disabled in untrusted workspaces. Run and Build use VS
Code process tasks rather than shell command strings, and live checks enforce
time, file-size, and output limits.

## Requirements

Install ZyenLang and make sure `zy --version` works in a new terminal. If the
compiler is elsewhere, set `zyenlang.compilerPath` to its executable path.

## Commands

Open the Command Palette with `Ctrl+Shift+P`:

- `ZyenLang: Check Current File`
- `ZyenLang: Run Project Target`
- `ZyenLang: Build Project Target`
- `ZyenLang: Emit C Source`

The editor title also shows Run and Check buttons for `.zy` files.

## Settings

- `zyenlang.compilerPath`: compiler executable, default `zy`
- `zyenlang.diagnostics.enable`: live checking, default `true`
- `zyenlang.diagnostics.delay`: typing debounce in milliseconds, default `300`
- `zyenlang.diagnostics.timeout`: check timeout in milliseconds, default `15000`
- `zyenlang.diagnostics.maxFileSizeKb`: live-check size limit, default `1024`
- `zyenlang.build.release`: use release builds for Run and Build, default `true`

## Install a VSIX

In VS Code, choose **Extensions: Install from VSIX...** and select the packaged
`zyenlang-0.3.0.vsix`, or run:

```powershell
code --install-extension zyenlang-0.3.0.vsix --force
```
