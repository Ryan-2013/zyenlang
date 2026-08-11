from __future__ import annotations

import json
import shutil
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / "ide" / "vscode" / "zyenlang"
    if not src.exists():
        print(f"missing extension folder: {src}")
        return 1

    manifest = json.loads((src / "package.json").read_text(encoding="utf-8"))
    extension_id = f"{manifest['publisher']}.{manifest['name']}"
    extensions = Path.home() / ".vscode" / "extensions"
    dst = extensions / f"{extension_id}-{manifest['version']}"
    extensions.mkdir(parents=True, exist_ok=True)
    for installed in extensions.glob(f"{extension_id}-*"):
        if installed.is_dir():
            shutil.rmtree(installed)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("node_modules", "test", "*.vsix"),
    )

    print(f"Installed {extension_id} {manifest['version']}:")
    print(dst)
    print("Restart VS Code, then open a .zy file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
