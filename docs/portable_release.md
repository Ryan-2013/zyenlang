# ZyenLang portable release

The platform archives contain everything needed to compile and run ZyenLang:

- a standalone `zy` CLI with its own Python runtime;
- Zig 0.16.0 as the bundled C compiler;
- raylib 6.0 for the cross-platform `std/tk` GUI backend;
- examples, applications, language documentation, and a prebuilt GUI demo.

No Python installation, `pip`, GCC, or MSYS2 setup is required.

## Windows

```powershell
.\zy.exe version
.\zy.exe run examples\hello.zy
.\zy.exe run examples\tk_portable_smoke.zy
.\zytk-demo.exe
```

## Linux and macOS

```bash
./zy version
./zy run examples/hello.zy
./zy run examples/tk_portable_smoke.zy
./zytk-demo
```

Linux desktops still need the normal system OpenGL/X11 libraries supplied by
the distribution. The portable archive does not install operating-system
drivers or display servers.

## Optional system compiler

The portable CLI uses its bundled Zig toolchain first. Set `ZY_CC` to override
it, for example `ZY_CC="clang"` or `ZY_CC="gcc"`.

Set `ZYENLANG_RAYLIB` only when testing a different raylib shared library. The
bundled runtime is selected automatically in normal use.
