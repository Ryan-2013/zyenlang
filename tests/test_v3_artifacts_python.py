from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from zyenlang.v2.artifacts import ArtifactBuilder, emit_single_file
from zyenlang.v2.package_manager import PackageError, init_project
from zyenlang.v2.toolchain import compile_objects, find_c_compiler


def write_manifest(root: Path, target_blocks: str, *, default_target: str = "ffi") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "zyproject.toml").write_text(
        f"""[package]
name = "artifact-test"
version = "0.3.0"
zyen = ">=0.3.0"

[build]
default-target = "{default_target}"
target-dir = "target"

{target_blocks}

[dependencies]
""",
        encoding="utf-8",
    )


def library_source() -> str:
    return """export fn add(left: i32, right: i32) i32 {
    return left + right
}

export fn answer_text() str {
    return "42"
}
"""


def executable_name(name: str) -> str:
    return name + (".exe" if sys.platform.startswith("win") else "")


def run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def test_c_source_target_builds_for_c_and_cpp_consumers(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_manifest(
        project,
        """[targets.ffi]
kind = "c-source"
entry = "src/lib.zy"
output-name = "zy_math"
""",
    )
    (project / "src" / "lib.zy").write_text(library_source(), encoding="utf-8")

    result = ArtifactBuilder(project).build("ffi")
    output = result.output_dir
    generated = output / "zy_math.c"
    header = output / "zy_math.h"
    metadata = json.loads((output / "zy_math.metadata.json").read_text(encoding="utf-8"))

    assert generated.is_file()
    assert header.is_file()
    assert (output / "zy2_runtime.h").is_file()
    assert (output / "zyenlang_c_abi.h").is_file()
    assert 'extern "C"' in header.read_text(encoding="utf-8")
    assert metadata["kind"] == "c-source"
    assert metadata["exports"] == ["add", "answer_text"]

    c_consumer = output / "consumer.c"
    c_consumer.write_text(
        '#include "zy_math.h"\nint main(void) { return add(20, 22) == 42 ? 0 : 1; }\n',
        encoding="utf-8",
    )
    c_executable = output / executable_name("consumer-c")
    compiled = run_command(
        [
            *find_c_compiler(),
            "-std=c11",
            str(c_consumer),
            str(generated),
            "-I",
            str(output),
            "-o",
            str(c_executable),
        ]
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert run_command([str(c_executable)]).returncode == 0

    cpp = shutil.which("g++") or shutil.which("clang++")
    if cpp is None:
        pytest.skip("no C++ compiler is available")
    generated_object = compile_objects((generated,), output / "objects")[0]
    cpp_consumer = output / "consumer.cpp"
    cpp_consumer.write_text(
        '#include "zy_math.h"\nint main() { return add(40, 2) == 42 ? 0 : 1; }\n',
        encoding="utf-8",
    )
    cpp_executable = output / executable_name("consumer-cpp")
    compiled_cpp = run_command(
        [
            cpp,
            "-std=c++17",
            str(cpp_consumer),
            str(generated_object),
            "-I",
            str(output),
            "-o",
            str(cpp_executable),
        ]
    )
    assert compiled_cpp.returncode == 0, compiled_cpp.stdout + compiled_cpp.stderr
    assert run_command([str(cpp_executable)]).returncode == 0


@pytest.mark.parametrize("kind", ["staticlib", "sharedlib"])
def test_compiled_library_targets_link_and_run(tmp_path: Path, kind: str) -> None:
    project = tmp_path / kind
    write_manifest(
        project,
        f"""[targets.ffi]
kind = "{kind}"
entry = "src/lib.zy"
output-name = "zy_math"
""",
    )
    (project / "src" / "lib.zy").write_text(library_source(), encoding="utf-8")
    result = ArtifactBuilder(project).build("ffi")
    output = result.output_dir
    consumer = output / "consumer.c"
    consumer.write_text(
        '#include "zy_math.h"\nint main(void) { return add(21, 21) == 42 ? 0 : 1; }\n',
        encoding="utf-8",
    )
    executable = output / executable_name(f"consume-{kind}")
    command = [*find_c_compiler(), "-std=c11", str(consumer), "-I", str(output)]
    if kind == "staticlib":
        command.append(str(result.primary))
    elif sys.platform.startswith("win"):
        command.extend([str(output / "libzy_math.dll.a"), "-DZYENLANG_USE_SHARED=1"])
    else:
        command.extend([str(result.primary), f"-Wl,-rpath,{output}"])
    command.extend(["-o", str(executable)])
    compiled = run_command(command)

    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert run_command([str(executable)], cwd=output).returncode == 0


def test_output_precedence_metadata_and_safe_clean(tmp_path: Path) -> None:
    project = tmp_path / "project"
    configured = tmp_path / "configured-output"
    override = tmp_path / "override-output"
    write_manifest(
        project,
        f"""[targets.ffi]
kind = "c-source"
entry = "src/lib.zy"
out-dir = "{configured.as_posix()}"
output-name = "stable_name"
""",
    )
    (project / "src" / "lib.zy").write_text(library_source(), encoding="utf-8")
    sentinel = override / "keep.txt"
    override.mkdir(parents=True)
    sentinel.write_text("do not remove", encoding="utf-8")

    result = ArtifactBuilder(project, release=True).build("ffi", out_dir=override)
    metadata = ArtifactBuilder(project).metadata()

    assert result.output_dir == override.resolve()
    assert not configured.exists()
    assert metadata["package"]["name"] == "artifact-test"
    assert metadata["targets"]["ffi"]["kind"] == "c-source"

    removed = ArtifactBuilder(project).clean("ffi")
    assert result.primary in removed
    assert sentinel.read_text(encoding="utf-8") == "do not remove"
    assert override.is_dir()


def test_clean_refuses_a_recorded_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project, "clean-safety")
    state = project / "target" / ".zyen-artifacts.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "targets": {
                    "debug:app": {
                        "kind": "bin",
                        "files": [str((tmp_path / "must-not-delete").resolve())],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    protected = tmp_path / "must-not-delete"
    protected.mkdir()
    (protected / "keep.txt").write_text("safe", encoding="utf-8")

    with pytest.raises(PackageError, match="refusing to recursively clean"):
        ArtifactBuilder(project).clean("app")

    assert (protected / "keep.txt").is_file()


def test_single_file_emit_uses_explicit_kind_and_never_guesses_from_suffix(tmp_path: Path) -> None:
    source = tmp_path / "library.zy"
    source.write_text("export fn answer() i32 {\n    return 42\n}\n", encoding="utf-8")
    output = tmp_path / "generated"

    result = emit_single_file(source, output, output_name="api")

    assert result.kind == "c"
    assert result.primary == (output / "api.c").resolve()
    assert (output / "zy2_runtime.h").is_file()
    assert (output / "zyenlang_c_abi.h").is_file()
