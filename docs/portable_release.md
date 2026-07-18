# ZyenLang portable release

The platform archives contain everything needed to compile and run ZyenLang:

- a standalone `zy` CLI with its own Python runtime;
- Zig 0.16.0 as the bundled C compiler;
- raylib 6.0 for the cross-platform `std/tk` GUI backend;
- examples, applications, language documentation, and a prebuilt GUI demo.

No Python installation, `pip`, GCC, or MSYS2 setup is required.

The v0.1.73 downloads are named `zyv173-<platform>`. Every archive extracts
to one `zyv173` folder. The `zy` executable lives directly in that folder, so
you can either invoke it in place or add that folder to `PATH`. Allow roughly
400 MB of free space while extracting the bundled toolchain.

## Windows

```powershell
cd zyv173
.\add-to-user-path.cmd
.\zy.exe version
.\zy.exe run examples\hello.zy
.\zy.exe run examples\tk_portable_smoke.zy
.\zytk-demo.exe
```

## Linux and macOS

```bash
cd zyv173
./add-to-user-path.sh
./zy version
./zy run examples/hello.zy
./zy run examples/tk_portable_smoke.zy
./zytk-demo
```

Linux desktops still need the normal system OpenGL/X11 libraries supplied by
the distribution. The portable archive does not install operating-system
drivers or display servers.

The helper scripts only add the extracted folder to the current user's PATH.
They do not install Python, a compiler, or files into system directories. You
can also configure PATH manually and skip the helper.

## Optional system compiler

The portable CLI uses its bundled Zig toolchain first. Set `ZY_CC` to override
it, for example `ZY_CC="clang"` or `ZY_CC="gcc"`.

Set `ZYENLANG_RAYLIB` only when testing a different raylib shared library. The
bundled runtime is selected automatically in normal use.
