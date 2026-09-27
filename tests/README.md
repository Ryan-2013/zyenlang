# Test Layout

- `test_compiler.py`: core language, ownership, runtime, and standard modules.
- `test_language.py`: 0.3 classes, references, functions, and legacy ABI checks.
- `test_c_interop_abi3.py`: Handle, Slice, bindgen, callbacks, and ABI v3.
- `test_package_manager.py`: dependency resolution and lockfile security.
- `test_artifacts.py`: C source, static library, and shared library outputs.
- `test_std_*.py`: focused standard-library integration.
- `test_cli.py`, `test_doctor.py`: command-line behavior and diagnostics.
- `test_portable.py`, `test_msi.py`: release packaging.
- `fixtures/`: isolated project and native-process fixtures.
- `*.zy`: language programs executed by compiler integration tests.

Run all tests with `python -m pytest -q`. The CI workflow also checks every
public example, runs ownership tests under ASan/UBSan on Linux, and validates
the VS Code extension independently.
