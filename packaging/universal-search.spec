# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the Universal Search desktop application.

Build from the repository root:

    .venv\\Scripts\\python -m pip install ".[build]"
    .venv\\Scripts\\python -m PyInstaller packaging/universal-search.spec

Output (windowed, no console):

    dist/UniversalSearch/UniversalSearch.exe
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = ROOT / "packaging" / "universal_search.ico"

a = Analysis(
    [str(ROOT / "packaging" / "entry-gui.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["doctest", "pydoc_data", "test"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UniversalSearch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed application: no console, no tracebacks
    icon=str(ICON),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="UniversalSearch",
)
