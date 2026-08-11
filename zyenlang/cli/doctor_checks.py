"""Health checks used by ``zy doctor``."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


STATUS_PASS = "pass"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"
STATUS_INFO = "info"


@dataclass
class CheckResult:
    id: str
    category: str
    status: str
    detail: str
    fix: str = ""

    def to_dict(self) -> dict:
        result = asdict(self)
        if not result["fix"]:
            result.pop("fix")
        return result


def check_python_version() -> CheckResult:
    version = sys.version_info
    detail = f"{version.major}.{version.minor}.{version.micro}"
    if version[:2] < (3, 10):
        return CheckResult(
            "python_version",
            "environment",
            STATUS_ERROR,
            f"Python {detail} is below the minimum 3.10",
            "Install Python 3.10 or newer.",
        )
    return CheckResult("python_version", "environment", STATUS_PASS, detail)


def check_cc() -> CheckResult:
    try:
        from zyenlang.v2.toolchain import find_c_compiler

        command = find_c_compiler()
    except (FileNotFoundError, ValueError) as exc:
        return CheckResult(
            "cc",
            "environment",
            STATUS_ERROR,
            str(exc),
            "Use a portable release, install gcc/clang, or set ZY_CC.",
        )
    return CheckResult("cc", "environment", STATUS_PASS, " ".join(command))


def check_cc_compiles() -> CheckResult:
    from zyenlang.v2.toolchain import compile_c

    with tempfile.TemporaryDirectory(prefix="zyen_doctor_cc_") as temp:
        root = Path(temp)
        source = root / "hello.c"
        executable = root / ("hello.exe" if os.name == "nt" else "hello")
        source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
        try:
            compile_c(source, executable)
        except (FileNotFoundError, ValueError, OSError, subprocess.CalledProcessError) as exc:
            return CheckResult(
                "cc_compiles",
                "environment",
                STATUS_ERROR,
                f"C compiler failed: {exc}",
                "Fix the C compiler reported by the cc check.",
            )
        if not executable.is_file():
            return CheckResult(
                "cc_compiles",
                "environment",
                STATUS_ERROR,
                "C compiler returned without creating an executable",
            )
        return CheckResult("cc_compiles", "environment", STATUS_PASS, "compiled a trivial C program")


def check_zy_on_path() -> CheckResult:
    executable = shutil.which("zy")
    if executable:
        return CheckResult("zy_on_path", "environment", STATUS_PASS, executable)
    return CheckResult(
        "zy_on_path",
        "environment",
        STATUS_WARNING,
        "zy is not on PATH",
        "Run pip install -e . or use python -m zyenlang.",
    )


def check_package_installed() -> CheckResult:
    try:
        import zyenlang  # noqa: F401
    except Exception as exc:
        return CheckResult(
            "package_installed",
            "installation",
            STATUS_ERROR,
            f"import zyenlang failed: {exc}",
            "Run pip install -e . from the repository root.",
        )
    return CheckResult("package_installed", "installation", STATUS_PASS, "import zyenlang OK")


def check_package_version() -> CheckResult:
    try:
        import zyenlang

        version = getattr(zyenlang, "__version__", "unknown")
    except Exception as exc:
        version = f"unavailable: {exc}"
    return CheckResult("package_version", "installation", STATUS_INFO, version)


def check_compiler_importable() -> CheckResult:
    try:
        from zyenlang.v2.compiler import Compiler  # noqa: F401
    except Exception as exc:
        return CheckResult(
            "compiler_importable",
            "installation",
            STATUS_ERROR,
            f"compiler import failed: {exc}",
            "Reinstall the package and check recent compiler edits.",
        )
    return CheckResult("compiler_importable", "installation", STATUS_PASS, "v0.2 compiler import OK")


def _std_dir() -> Path | None:
    try:
        import zyenlang

        directory = Path(zyenlang.__file__).resolve().parent / "v2" / "std"
    except Exception:
        return None
    return directory if directory.is_dir() else None


def check_std_modules_count() -> CheckResult:
    directory = _std_dir()
    if directory is None:
        return CheckResult(
            "std_modules_count",
            "installation",
            STATUS_ERROR,
            "zyenlang/v2/std was not found",
            "Reinstall the package from a complete release.",
        )
    count = len(list(directory.glob("*.zy")))
    if count < 10:
        return CheckResult(
            "std_modules_count",
            "installation",
            STATUS_ERROR,
            f"only {count} standard-library modules were found in {directory}",
            "Reinstall the package from a complete release.",
        )
    return CheckResult("std_modules_count", "installation", STATUS_PASS, f"{count} standard-library modules")


def check_vscode_ext() -> CheckResult:
    candidates = [Path.home() / ".vscode" / "extensions"]
    if os.name != "nt":
        candidates.append(Path.home() / ".vscode-server" / "extensions")
    for directory in candidates:
        if directory.is_dir() and any("zyenlang" in child.name.lower() for child in directory.iterdir()):
            return CheckResult("vscode_ext", "configuration", STATUS_INFO, f"installed under {directory}")
    return CheckResult(
        "vscode_ext",
        "configuration",
        STATUS_INFO,
        "not installed (optional)",
        "Run python tools/install_vscode_extension.py from the repository root.",
    )


def check_tmpdir_writable() -> CheckResult:
    try:
        with tempfile.TemporaryDirectory(prefix="zyen_doctor_write_") as temp:
            probe = Path(temp) / "probe"
            probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        return CheckResult(
            "tmpdir_writable",
            "configuration",
            STATUS_ERROR,
            f"temporary directory is not writable: {exc}",
            "Set TEMP/TMP to a writable directory.",
        )
    return CheckResult("tmpdir_writable", "configuration", STATUS_PASS, "temporary directory is writable")


SMOKE_SOURCE = """import <std/io> as io

fn main() i32 {
    io.print("doctor_ok")
    return 0
}
"""


def run_smoke_trio() -> tuple[CheckResult, CheckResult, CheckResult]:
    from zyenlang.v2.compiler import Compiler

    with tempfile.TemporaryDirectory(prefix="zyen_doctor_runtime_") as temp:
        root = Path(temp)
        source = root / "doctor_smoke.zy"
        executable = root / ("doctor_smoke.exe" if os.name == "nt" else "doctor_smoke")
        source.write_text(SMOKE_SOURCE, encoding="utf-8")
        compiler = Compiler()

        try:
            compiler.check_file(source)
        except Exception as exc:
            failed = CheckResult("smoke_check", "runtime", STATUS_ERROR, f"check failed: {exc}")
            skipped_build = CheckResult("smoke_build", "runtime", STATUS_ERROR, "skipped because check failed")
            skipped_run = CheckResult("smoke_run", "runtime", STATUS_ERROR, "skipped because check failed")
            return failed, skipped_build, skipped_run
        checked = CheckResult("smoke_check", "runtime", STATUS_PASS, "minimal program passed semantic checking")

        try:
            compiler.build_file(source, executable)
        except Exception as exc:
            failed = CheckResult("smoke_build", "runtime", STATUS_ERROR, f"build failed: {exc}")
            skipped = CheckResult("smoke_run", "runtime", STATUS_ERROR, "skipped because build failed")
            return checked, failed, skipped
        built = CheckResult("smoke_build", "runtime", STATUS_PASS, "minimal program built successfully")

        try:
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            failed = CheckResult("smoke_run", "runtime", STATUS_ERROR, f"launch failed: {exc}")
            return checked, built, failed
        output = result.stdout.strip()
        if result.returncode != 0 or "doctor_ok" not in output:
            failed = CheckResult(
                "smoke_run",
                "runtime",
                STATUS_ERROR,
                f"exit={result.returncode}, stdout={output!r}, stderr={result.stderr.strip()!r}",
            )
            return checked, built, failed
        ran = CheckResult("smoke_run", "runtime", STATUS_PASS, "minimal program printed doctor_ok")
        return checked, built, ran


def run_all(with_runtime: bool = True) -> list[CheckResult]:
    results = [
        check_python_version(),
        check_cc(),
        check_cc_compiles(),
        check_zy_on_path(),
        check_package_installed(),
        check_package_version(),
        check_compiler_importable(),
        check_std_modules_count(),
        check_vscode_ext(),
        check_tmpdir_writable(),
    ]
    if with_runtime:
        results.extend(run_smoke_trio())
    return results
