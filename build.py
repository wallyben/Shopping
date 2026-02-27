"""
build.py — produce a Windows .exe with PyInstaller.

Run on Windows:
    python build.py

Run cross-platform (for CI):
    python build.py --platform win

Output:
    dist/PokemonCenterMonitor/          (--onedir, faster startup)
    dist/PokemonCenterMonitor.exe       (entry binary inside the above dir)

Folder layout after build:
    dist/
      PokemonCenterMonitor/
        PokemonCenterMonitor.exe    <- double-click this
        _internal/                  <- PyInstaller runtime files
        assets/                     <- icon and any bundled assets

For distribution: zip the entire dist/PokemonCenterMonitor/ folder.
The user extracts and double-clicks PokemonCenterMonitor.exe — no installer
required. App data (monitor_data.db, logs/) is created next to the .exe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ASSETS = ROOT / "assets"
APP_NAME = "PokemonCenterMonitor"


def run_pyinstaller() -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",           # one folder (faster startup than --onefile)
        "--windowed",         # no console window on Windows
        f"--name={APP_NAME}",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),

        # App icon (provide assets/icon.ico to enable)
        *(_icon_args()),

        # Bundle customtkinter's data files (themes, images)
        "--collect-all", "customtkinter",

        # Hidden imports: keyring platform backends
        "--hidden-import", "keyring.backends.Windows",
        "--hidden-import", "keyring.backends.macOS",
        "--hidden-import", "keyring.backends.SecretService",
        "--hidden-import", "keyring.backends.fail",

        # lxml parser used by beautifulsoup4
        "--hidden-import", "lxml.etree",
        "--hidden-import", "lxml._elementpath",

        # httpx / httpcore internals
        "--hidden-import", "httpcore._async.http2",
        "--hidden-import", "h2",
        "--hidden-import", "hpack",

        # tkinter / CTk
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "tkinter.messagebox",

        # Bundle assets directory if it exists and has files
        *(_asset_args()),

        str(ROOT / "main.py"),
    ]

    print("Running PyInstaller...")
    print(" ".join(cmd))
    print()

    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"\nPyInstaller failed (exit {result.returncode})")
        sys.exit(result.returncode)

    print(f"\nBuild complete: {DIST / APP_NAME}")


def _icon_args() -> list[str]:
    icon = ASSETS / "icon.ico"
    if icon.exists():
        return ["--icon", str(icon)]
    print("Note: assets/icon.ico not found — building without icon.")
    return []


def _asset_args() -> list[str]:
    if ASSETS.exists() and any(ASSETS.iterdir()):
        sep = ";" if sys.platform == "win32" else ":"
        return ["--add-data", f"{ASSETS}{sep}assets"]
    return []


if __name__ == "__main__":
    run_pyinstaller()
