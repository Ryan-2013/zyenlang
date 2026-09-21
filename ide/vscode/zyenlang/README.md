# ZyenLang for Visual Studio Code

The official project-aware editor extension for ZyenLang 0.3.

## Features

- ZyenLang 0.3 highlighting for `::` paths, pure structs, classes, references, intrinsics, and native declarations
- project-aware completion for `std::`, `crate::`, locked path/Git dependencies, package entry imports, imported module symbols, static functions, class methods, and fields
- inferred-type completion for locals such as `let app = gui::application(...)`,
  `let item = &values[0]`, and `let field = &object.value`
- hover, parameter hints, Go to Definition, Go to Type Definition, safe local Rename, Find References, import/native-source links, outline, folding, and workspace symbols
- inferred variable types on hover without always-visible editor annotations
- live `zy check` diagnostics for the current unsaved buffer
- migration quick fixes for old imports, module `.` access, and `FREE__()`
- manifest target selection plus Run, Build, Test, Clean, Emit C, and Refresh Index commands
- optional **ZyenLang Ember** theme using `#20201F` with `#D97757` accents

The extension finds the nearest `zyproject.toml`, debounces checks while typing,
and cancels stale compiler processes.
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
- `ZyenLang: Test Project`
- `ZyenLang: Clean Project Target`
- `ZyenLang: Emit C Source`
- `ZyenLang: Refresh Project Index`

The editor title also shows Run and Check buttons for `.zy` files.

## Settings

- `zyenlang.compilerPath`: compiler executable, default `zy`
- `zyenlang.diagnostics.enable`: live checking, default `true`
- `zyenlang.diagnostics.delay`: typing debounce in milliseconds, default `300`
- `zyenlang.diagnostics.timeout`: check timeout in milliseconds, default `15000`
- `zyenlang.diagnostics.maxFileSizeKb`: live-check size limit, default `1024`
- `zyenlang.build.release`: use release builds for Run and Build, default `true`
- `zyenlang.project.target`: preferred target; empty uses the manifest default or opens a picker
- `zyenlang.run.arguments`: string array passed after `zy run --`

## Install a VSIX

In VS Code, choose **Extensions: Install from VSIX...** and select the packaged
`zyenlang-0.3.4.vsix`, or run:

```powershell
code --install-extension zyenlang-0.3.4.vsix --force
```

From a ZyenLang source checkout, the preferred command is:

```powershell
python tools/install_vscode_extension.py
```

It installs through VS Code's extension registry and verifies the registered
version. Run **Developer: Reload Window** in every open VS Code window after an
update; an existing extension host keeps the old JavaScript until reloaded.
