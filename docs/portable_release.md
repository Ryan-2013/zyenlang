# ZyenLang 0.2 release packages

Every platform archive contains:

- `zy`: the ZyenLang 0.2 typed compiler;
- a pinned Zig 0.16.0 C toolchain;
- standard libraries, examples, documentation, and GUI runtime.

No Python installation, `pip`, GCC, or MSYS2 is required. Archives extract to
one `zyv201` directory and require roughly 400 MB of free disk space.

## Windows MSI

`zyv201-windows-x64.msi` installs for the current user without administrator
privileges. Files go to `%LOCALAPPDATA%\Programs\ZyenLang`; the installer adds
that exact directory to the user PATH and removes it during uninstall.

Open a new terminal after installation:

```powershell
zy --version
zy run examples\v2_language_tour.zy
```

The MSI is reproducibly generated from the already-tested portable directory.
Release assets have SHA-256 checksums and GitHub provenance, but v0.2.1 is not
Authenticode-signed.

## Windows portable

```powershell
cd zyv201
.\add-to-user-path.cmd
.\zy.exe --version
.\zy.exe run examples\v2_language_tour.zy
```

The helper changes only the current user's PATH. It supports a dry run through
`add-to-user-path.ps1 -DryRun`.

## Linux and macOS

```bash
cd zyv201
./add-to-user-path.sh
./zy --version
./zy run examples/v2_language_tour.zy
```

Linux still needs its normal system OpenGL/X11 libraries. The archive does not
install display drivers or system packages.

## Compiler override

The portable compiler selects bundled Zig first. Set `ZY_CC` to override it,
for example `ZY_CC=clang`.
