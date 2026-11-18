#!/usr/bin/env python3
"""Build the ShortClip desktop bundle: dist/ShortClip/ (ShortClip.exe on Windows) and dist/ShortClip-<os>.zip.

    pip install -r requirements.txt pyinstaller
    python packaging/build_exe.py

PyInstaller does not cross-compile: run this ON Windows to get ShortClip.exe (the GitHub Actions workflow
`.github/workflows/windows-exe.yml` does exactly that). On Linux/macOS it builds a native `ShortClip` binary.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP = DIST / "ShortClip"
# project files that run from source next to the executable
COPY = ["orchestrator.py", "shortclip", "tools", "dashboard", "agents", "assets", "config", "docs", "examples",
        "db/schema.sql", "CLAUDE.md", "AGENTS.md", "README.md", "LICENSE", ".env.example", "requirements.txt"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "*.db", "*.db-*", ".locks", "node_modules", "renders", "check")


def main() -> int:
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
                    str(ROOT / "packaging" / "shortclip.spec")], check=True, cwd=ROOT)
    for rel in COPY:
        src, dst = ROOT / rel, APP / rel
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=SKIP, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    shutil.copy2(ROOT / "packaging" / "README-windows.txt", APP / "README-windows.txt")
    tag = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")
    archive = shutil.make_archive(str(DIST / f"ShortClip-{tag}-{platform.machine().lower()}"), "zip", DIST, "ShortClip")
    print(f"built {APP}\nzip   {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
