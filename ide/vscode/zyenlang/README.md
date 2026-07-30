# ZyenLang for Visual Studio Code

The official editor extension for ZyenLang 0.2.

## Features

- ZyenLang 0.2 syntax highlighting and bracket-aware editing
- completion for keywords, types, imports, symbols, struct members, and standard modules
- hover signatures, parameter hints, Go to Definition, Find References, document outline, and workspace symbols
- live `zy2 check` diagnostics for the current unsaved buffer
- Run, Build Executable, and Emit C Source commands
- optional **ZyenLang Ember** theme using `#20201F` with `#D97757` accents

The extension debounces checks while typing and cancels stale compiler processes.
Compiler errors remain authoritative; the extension's lightweight index is only
used for responsive navigation and completion.

Compiler execution is disabled in untrusted workspaces. Run and Build use VS
Code process tasks rather than shell command strings, and live checks enforce
time, file-size, and output limits.

## Requirements

Install ZyenLang and make sure `zy2 --version` works in a new terminal. If the
compiler is elsewhere, set `zyenlang.compilerPath` to its executable path.

## Commands

Open the Command Palette with `Ctrl+Shift+P`:

- `ZyenLang: Check Current File`
- `ZyenLang: Run Current File`
- `ZyenLang: Build Executable`
- `ZyenLang: Emit C Source`

The editor title also shows Run and Check buttons for `.zy` files.

## Settings

- `zyenlang.compilerPath`: compiler executable, default `zy2`
- `zyenlang.diagnostics.enable`: live checking, default `true`
- `zyenlang.diagnostics.delay`: typing debounce in milliseconds, default `300`
- `zyenlang.diagnostics.timeout`: check timeout in milliseconds, default `15000`
- `zyenlang.diagnostics.maxFileSizeKb`: live-check size limit, default `1024`
- `zyenlang.build.release`: use release builds for Run and Build, default `true`

## Install a VSIX

In VS Code, choose **Extensions: Install from VSIX...** and select the packaged
`zyenlang-vscode-0.2.0.vsix`, or run:

```powershell
code --install-extension zyenlang-vscode-0.2.0.vsix --force
```
