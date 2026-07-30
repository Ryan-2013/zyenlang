# ZyenLang 0.2 release packages

Every platform archive contains both compiler commands:

- `zy2`: the ZyenLang 0.2 typed compiler;
- `zy`: the legacy v0.1 compatibility compiler;
- a pinned Zig 0.16.0 C toolchain;
- standard libraries, examples, documentation, and GUI runtime.

No Python installation, `pip`, GCC, or MSYS2 is required. Archives extract to
one `zyv200` directory and require roughly 400 MB of free disk space.

## Windows MSI

`zyv200-windows-x64.msi` installs for the current user without administrator
privileges. Files go to `%LOCALAPPDATA%\Programs\ZyenLang`; the installer adds
that exact directory to the user PATH and removes it during uninstall.

Open a new terminal after installation:

```powershell
zy2 --version
zy2 run examples\v2_language_tour.zy
```

The MSI is reproducibly generated from the already-tested portable directory.
Release assets have SHA-256 checksums and GitHub provenance, but v0.2.0 is not
Authenticode-signed.

## Windows portable

```powershell
cd zyv200
.\add-to-user-path.cmd
.\zy2.exe --version
.\zy2.exe run examples\v2_language_tour.zy
```

The helper changes only the current user's PATH. It supports a dry run through
`add-to-user-path.ps1 -DryRun`.

## Linux and macOS

```bash
cd zyv200
./add-to-user-path.sh
./zy2 --version
./zy2 run examples/v2_language_tour.zy
```

Linux still needs its normal system OpenGL/X11 libraries. The archive does not
install display drivers or system packages.

## Compiler override

The portable v2 compiler selects bundled Zig first. Set `ZY2_CC` to override
it, for example `ZY2_CC=clang`. The legacy compiler uses `ZY_CC`.
