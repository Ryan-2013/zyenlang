from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _find_command(name: str) -> str | None:
    return shutil.which(name) or shutil.which(f"{name}.cmd")


def _registered_version(output: str, extension_id: str) -> str | None:
    prefix = f"{extension_id.lower()}@"
    for raw_line in output.splitlines():
        line = raw_line.strip().lower()
        if line.startswith(prefix):
            return raw_line.strip()[len(prefix) :]
    return None


def _package_extension(extension_dir: Path, expected_vsix: Path) -> None:
    npm = _find_command("npm")
    if npm is None:
        raise RuntimeError(
            f"{expected_vsix.name} is missing and npm is not available; "
            "install Node.js or pass --vsix PATH"
        )

    if not (extension_dir / "node_modules" / "@vscode" / "vsce").exists():
        install = _run([npm, "ci"], cwd=extension_dir)
        if install.returncode != 0:
            raise RuntimeError(install.stderr.strip() or install.stdout.strip() or "npm ci failed")

    package = _run([npm, "run", "package"], cwd=extension_dir)
    if package.returncode != 0:
        raise RuntimeError(package.stderr.strip() or package.stdout.strip() or "VSIX packaging failed")
    if not expected_vsix.is_file():
        raise RuntimeError(f"VSIX packaging did not create {expected_vsix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the current ZyenLang VS Code extension.")
    parser.add_argument("--code", help="Path to the VS Code command-line executable")
    parser.add_argument("--vsix", type=Path, help="Install this VSIX instead of packaging the source tree")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    extension_dir = root / "ide" / "vscode" / "zyenlang"
    manifest_path = extension_dir / "package.json"
    if not manifest_path.is_file():
        print(f"missing extension manifest: {manifest_path}")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    extension_id = f"{manifest['publisher']}.{manifest['name']}"
    version = str(manifest["version"])
    vsix = args.vsix.resolve() if args.vsix else extension_dir / f"{manifest['name']}-{version}.vsix"

    try:
        if args.vsix is None:
            _package_extension(extension_dir, vsix)
        elif not vsix.is_file():
            raise RuntimeError(f"VSIX does not exist: {vsix}")
    except RuntimeError as exc:
        print(f"Cannot package the VS Code extension: {exc}")
        return 1

    code = args.code or _find_command("code")
    if code is None:
        print("Cannot find the VS Code 'code' command. Add it to PATH or pass --code PATH.")
        return 1

    install = _run([code, "--install-extension", str(vsix), "--force"])
    if install.returncode != 0:
        print(install.stderr.strip() or install.stdout.strip() or "VS Code extension installation failed")
        return install.returncode

    listed = _run([code, "--list-extensions", "--show-versions"])
    if listed.returncode != 0:
        print(listed.stderr.strip() or listed.stdout.strip() or "Cannot verify the installed extension")
        return listed.returncode

    actual = _registered_version(listed.stdout, extension_id)
    if actual != version:
        print(f"VS Code registered {extension_id}@{actual or 'missing'}, expected {version}.")
        return 1

    print(f"Installed and verified {extension_id}@{version} from:")
    print(vsix)
    print("Run 'Developer: Reload Window' in every open VS Code window before using the extension.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
