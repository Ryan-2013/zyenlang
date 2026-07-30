from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from zyenlang.v2.compiler import Compiler
from zyenlang.v2.diagnostics import CompileError
from zyenlang.v2.package_manager import (
    LOCK_NAME,
    MANIFEST_NAME,
    PackageError,
    add_dependency,
    cache_path,
    init_project,
    install_project,
    load_manifest,
    main as package_main,
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
                'zyen = ">=0.2.1"',
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
        "import <math-lib/math> as math\n\nfn main() i32 {\n    return math.answer() - 42\n}\n",
        encoding="utf-8",
    )
    executable = tmp_path / ("compiled-app.exe" if sys.platform.startswith("win") else "compiled-app")
    Compiler().build_file(source, executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=False)

    assert executable.is_file()
    assert result.returncode == 0, result.stdout + result.stderr
    assert cache_path(lock.packages[0].digest).is_dir()
    assert read_lock(project).root_dependencies == ("math-lib",)

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
    source.write_text("import <math/value> as value\nfn main() i32 { return value.answer() }\n", encoding="utf-8")
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
    source.write_text("import <math/value> as value\nfn main() i32 { return value.answer() }\n", encoding="utf-8")
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
        {"api.zy": "import <utility/value> as value\npublic fn answer() i32 { return value.value() }\n"},
        dependencies={"utility": utility},
    )
    project = tmp_path / "app"
    init_project(project, "app")
    add_dependency(project, facade)
    source = project / "src" / "main.zy"
    source.write_text("import <facade/api> as api\nfn main() i32 { return api.answer() - 42 }\n", encoding="utf-8")

    assert Compiler().check_file(source)

    source.write_text("import <utility/value> as value\nfn main() i32 { return value.value() }\n", encoding="utf-8")
    with pytest.raises(CompileError, match="not a declared dependency"):
        Compiler().check_file(source)


def test_package_relative_import_cannot_escape_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    source.write_text("import <unsafe/api> as api\nfn main() i32 { return api.answer() }\n", encoding="utf-8")

    with pytest.raises(CompileError, match="escapes the package root"):
        Compiler().check_file(source)


def test_package_cli_initializes_and_lists_empty_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "sample"

    assert package_main(["--project", str(project), "init", "--name", "sample"]) == 0
    assert package_main(["--project", str(project), "list"]) == 0

    output = capsys.readouterr().out
    assert "initialized sample" in output
    assert "No dependencies." in output


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
