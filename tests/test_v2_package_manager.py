from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from zyenlang.v2.compiler import Compiler
from zyenlang.v2.diagnostics import CompileError
from zyenlang.v2.__main__ import main as cli_main
from zyenlang.v2.package_manager import (
    LOCK_NAME,
    MANIFEST_NAME,
    PackageError,
    add_dependency,
    add_git_dependency,
    cache_path,
    init_project,
    install_project,
    load_manifest,
    read_lock,
    remove_dependency,
)


def write_package(
    root: Path,
    name: str,
    modules: dict[str, str],
    *,
    dependencies: dict[str, Path] | None = None,
) -> None:
    root.mkdir(parents=True)
    dependency_lines = []
    for dependency_name, dependency_path in sorted((dependencies or {}).items()):
        relative = dependency_path.relative_to(root.parent).as_posix()
        dependency_lines.append(f'{dependency_name} = {{ path = "../{relative}" }}')
    (root / MANIFEST_NAME).write_text(
        "\n".join(
            [
                "[package]",
                f'name = "{name}"',
                'version = "0.1.0"',
                'entry = "src/lib.zy"',
                'zyen = ">=0.3.0"',
                "",
                "[build]",
                'default-target = "lib"',
                'target-dir = "target"',
                "",
                "[targets.lib]",
                'kind = "c-source"',
                'entry = "src/lib.zy"',
                'output-name = "package"',
                "",
                "[dependencies]",
                *dependency_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )
    for relative, source in modules.items():
        path = root / "src" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def test_package_manager_path_dependency_compiles_from_locked_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    library = tmp_path / "math-lib"
    write_package(library, "math-lib", {"math.zy": "public fn answer() i32 {\n    return 42\n}\n"})
    project = tmp_path / "app"
    init_project(project, "app")

    lock = add_dependency(project, library)
    source = project / "src" / "main.zy"
    source.write_text(
        "import math_lib::math as math\n\nfn main() i32 {\n    return math::answer() - 42\n}\n",
        encoding="utf-8",
    )
    executable = tmp_path / ("compiled-app.exe" if sys.platform.startswith("win") else "compiled-app")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert executable.is_file()
    assert result.returncode == 0, result.stdout + result.stderr
    assert cache_path(lock.packages[0].digest).is_dir()
    assert read_lock(project).root_dependencies == ("math_lib",)

    (library / "src" / "math.zy").write_text("public fn answer() i32 { return 7 }\n", encoding="utf-8")
    assert Compiler().check_file(source)


def test_package_manager_remove_updates_manifest_and_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    library = tmp_path / "math"
    write_package(library, "math", {"lib.zy": "public fn answer() i32 { return 42 }\n"})
    project = tmp_path / "app"
    init_project(project, "app")
    add_dependency(project, library)

    lock = remove_dependency(project, "math")

    assert load_manifest(project).dependencies == {}
    assert lock.packages == ()


def test_package_manager_locked_install_rejects_manifest_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "app"
    init_project(project, "app")
    manifest = project / MANIFEST_NAME
    manifest.write_text(manifest.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")

    with pytest.raises(PackageError, match="out of date"):
        install_project(project, locked=True)


def test_compiler_rejects_tampered_package_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    library = tmp_path / "math"
    write_package(library, "math", {"value.zy": "public fn answer() i32 { return 42 }\n"})
    project = tmp_path / "app"
    init_project(project, "app")
    lock = add_dependency(project, library)
    source = project / "src" / "main.zy"
    source.write_text("import math::value as value\nfn main() i32 { return value::answer() }\n", encoding="utf-8")
    cached_module = cache_path(lock.packages[0].digest) / "src" / "value.zy"
    cached_module.write_text("public fn answer() i32 { return 0 }\n", encoding="utf-8")

    with pytest.raises(CompileError, match="integrity verification"):
        Compiler().check_file(source)


def test_compiler_rejects_lock_metadata_that_disagrees_with_cached_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    library = tmp_path / "math"
    write_package(library, "math", {"value.zy": "public fn answer() i32 { return 42 }\n"})
    project = tmp_path / "app"
    init_project(project, "app")
    add_dependency(project, library)
    source = project / "src" / "main.zy"
    source.write_text("import math::value as value\nfn main() i32 { return value::answer() }\n", encoding="utf-8")
    lock_path = project / LOCK_NAME
    lock_path.write_text(
        lock_path.read_text(encoding="utf-8").replace('version = "0.1.0"', 'version = "9.9.9"'),
        encoding="utf-8",
    )

    with pytest.raises(CompileError, match="expected version `9.9.9`, got `0.1.0`"):
        Compiler().check_file(source)


def test_locked_install_rejects_changed_source_when_cache_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    library = tmp_path / "math"
    write_package(library, "math", {"value.zy": "public fn answer() i32 { return 42 }\n"})
    project = tmp_path / "app"
    init_project(project, "app")
    lock = add_dependency(project, library)
    shutil.rmtree(cache_path(lock.packages[0].digest))
    (library / "src" / "value.zy").write_text("public fn answer() i32 { return 7 }\n", encoding="utf-8")

    with pytest.raises(PackageError, match="changed"):
        install_project(project, locked=True)


def test_package_imports_enforce_direct_dependency_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    utility = tmp_path / "utility"
    write_package(utility, "utility", {"value.zy": "public fn value() i32 { return 42 }\n"})
    facade = tmp_path / "facade"
    write_package(
        facade,
        "facade",
        {"api.zy": "import utility::value as value\npublic fn answer() i32 { return value::value() }\n"},
        dependencies={"utility": utility},
    )
    project = tmp_path / "app"
    init_project(project, "app")
    add_dependency(project, facade)
    source = project / "src" / "main.zy"
    source.write_text("import facade::api as api\nfn main() i32 { return api::answer() - 42 }\n", encoding="utf-8")

    assert Compiler().check_file(source)

    source.write_text("import utility::value as value\nfn main() i32 { return value::value() }\n", encoding="utf-8")
    with pytest.raises(CompileError, match="not a declared dependency"):
        Compiler().check_file(source)


def test_package_string_import_reports_v03_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    library = tmp_path / "unsafe"
    write_package(
        library,
        "unsafe",
        {"api.zy": 'import "../../outside.zy" as outside\npublic fn answer() i32 { return outside.value() }\n'},
    )
    project = tmp_path / "app"
    init_project(project, "app")
    add_dependency(project, library)
    source = project / "src" / "main.zy"
    source.write_text("import unsafe::api as api\nfn main() i32 { return api::answer() }\n", encoding="utf-8")

    with pytest.raises(CompileError, match="ZyenLang 0.3 imports use"):
        Compiler().check_file(source)


def test_project_cli_initializes_and_reports_metadata(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "sample"

    assert cli_main(["init", str(project), "--name", "sample"]) == 0
    assert cli_main(["metadata", "--project", str(project)]) == 0

    output = capsys.readouterr().out
    assert "initialized sample" in output
    assert '"name": "sample"' in output


def test_failed_add_restores_the_original_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    project = tmp_path / "app"
    init_project(project, "app")
    manifest_path = project / MANIFEST_NAME
    original = manifest_path.read_text(encoding="utf-8")
    bad_package = tmp_path / "bad"
    write_package(
        bad_package,
        "bad",
        {"lib.zy": "public fn value() i32 { return 1 }\n"},
        dependencies={"missing": tmp_path / "missing"},
    )

    with pytest.raises(PackageError, match="missing"):
        add_dependency(project, bad_package)

    assert manifest_path.read_text(encoding="utf-8") == original


def test_package_cache_rejects_symlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    library = tmp_path / "linked"
    write_package(library, "linked", {"lib.zy": "public fn value() i32 { return 1 }\n"})
    target = library / "target.txt"
    target.write_text("target", encoding="utf-8")
    try:
        (library / "link.txt").symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    project = tmp_path / "app"
    init_project(project, "app")

    with pytest.raises(PackageError, match="symlink"):
        add_dependency(project, library)


def test_exact_revision_git_dependency_is_locked_and_reproducible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    monkeypatch.setenv("ZYEN_HOME", str(tmp_path / "home"))
    repository = tmp_path / "remote-math"
    write_package(repository, "remote-math", {"lib.zy": "public fn answer() i32 {\n    return 42\n}\n"})
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "ZyenLang Tests"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repository, check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    project = tmp_path / "app"
    init_project(project, "git-app")
    lock = add_git_dependency(project, "remote_math", str(repository), commit)
    source = project / "src" / "main.zy"
    source.write_text(
        "import remote_math::lib as math\nfn main() i32 {\n    return math::answer() - 42\n}\n",
        encoding="utf-8",
    )
    executable = tmp_path / ("git-app.exe" if sys.platform.startswith("win") else "git-app")
    Compiler().build_executable(source, executable)

    assert subprocess.run([str(executable)], check=False).returncode == 0
    assert len(lock.packages) == 1
    assert lock.packages[0].source_type == "git"
    assert lock.packages[0].revision == commit

    shutil.rmtree(cache_path(lock.packages[0].digest))
    reproduced = install_project(project, locked=True)
    assert reproduced.packages[0].revision == commit


@pytest.mark.parametrize("revision", ["main", "v1.0.0", "deadbeef", "a" * 41])
def test_git_dependency_rejects_non_commit_revisions(tmp_path: Path, revision: str) -> None:
    project = tmp_path / "app"
    init_project(project, "git-app")

    with pytest.raises(PackageError, match="full 40- or 64-hex commit"):
        add_git_dependency(project, "remote", "https://example.invalid/repo.git", revision)


@pytest.mark.parametrize(
    "source",
    ["http://example.invalid/repo.git", "file:///tmp/repo", "ext::command", "--upload-pack=bad"],
)
def test_git_dependency_rejects_unsafe_source_forms(tmp_path: Path, source: str) -> None:
    project = tmp_path / "app"
    init_project(project, "git-app")

    with pytest.raises(PackageError, match="invalid Git URL"):
        add_git_dependency(project, "remote", source, "a" * 40)
