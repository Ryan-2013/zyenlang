from pathlib import Path
root = Path(__file__).resolve().parents[1]
required = [
    "pyproject.toml",
    "zyen.py",
    "zyenlang/v2/compiler.py",
    "zyenlang/v2/std/list.zy",
    "zyenlang/v2/std/io.zy",
    "zyenlang/v2/std/fs.zy",
    "tools/install_vscode_extension.py",
    "examples/v2_language_tour.zy",
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
removed = ["zyenlang/v1", "std", "zy2.py"]
unexpected = [item for item in removed if (root / item).exists()]
for item in removed:
    print(("UNEXPECTED " if item in unexpected else "REMOVED ") + item)
if unexpected:
    raise SystemExit(1)
print("Layout OK")
