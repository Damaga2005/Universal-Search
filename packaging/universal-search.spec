# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the Universal Search desktop application.

Build from the repository root:

    .venv\\Scripts\\python -m pip install ".[build]"
    .venv\\Scripts\\python -m PyInstaller packaging/universal-search.spec

Output (one folder, two executables sharing one runtime):

    dist/UniversalSearch/UniversalSearch.exe   windowed GUI (product)
    dist/UniversalSearch/universal-search.exe  console CLI + background indexer

Both executables use the same entry script (packaging/entry-gui.py): with
arguments they run the CLI — so ``indexer start``/autostart work inside a
frozen deployment — and without arguments they open the search window.
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = ROOT / "packaging" / "universal_search.ico"
# Windows version resource for both executables; the numbers must match
# universal_search.__version__ (tests/test_release.py enforces this).
VERSION_FILE = ROOT / "packaging" / "version_file.txt"

a = Analysis(
    [str(ROOT / "packaging" / "entry-gui.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[
        # imported lazily at runtime; listed explicitly so the frozen
        # worker keeps filesystem watchers and lifecycle control
        "universal_search.background",
        "universal_search.cli",
        "watchdog.observers",
        "watchdog.observers.read_directory_changes",
        "watchdog.observers.winapi",
        "watchdog.events",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["doctest", "pydoc_data", "test"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe_gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UniversalSearch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed application: no console, no tracebacks
    icon=str(ICON),
    version=str(VERSION_FILE),  # "1.0.0" version resource in file properties
)

exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="universal-search",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # CLI surface: search/index/indexer with visible output
    icon=str(ICON),
    version=str(VERSION_FILE),
)

coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="UniversalSearch",
)
