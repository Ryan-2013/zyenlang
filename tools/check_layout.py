from pathlib import Path
root = Path(__file__).resolve().parents[1]
required = [
    "pyproject.toml",
    "zyen.py",
    "PROJECT_STRUCTURE.md",
    "docs/README.md",
    "docs/language_guide_zh_TW.md",
    "examples/README.md",
    "scripts/windows/smoke_test.bat",
    "tests/README.md",
    "zyenlang/compiler/compiler.py",
    "zyenlang/compiler/std/list.zy",
    "zyenlang/compiler/std/io.zy",
    "zyenlang/compiler/std/fs.zy",
    "tools/install_vscode_extension.py",
    "examples/language_tour.zy",
    "ide/vscode/zyenlang/package.json",
]
print("ZyenLang folder:", root)
missing = []
for item in required:
    p = root / item
    print(("OK      " if p.exists() else "MISSING ") + item)
    if not p.exists():
        missing.append(item)
if missing:
    raise SystemExit(1)
removed = [
    "zyenlang/v1",
    "zyenlang/v2",
    "std",
    "zy2.py",
    "examples/v2_language_tour.zy",
    "docs/v3_architecture.md",
]
unexpected = [item for item in removed if (root / item).exists()]
for item in removed:
    print(("UNEXPECTED " if item in unexpected else "REMOVED ") + item)
if unexpected:
    raise SystemExit(1)
print("Layout OK")
