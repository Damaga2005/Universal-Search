# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the single-file Universal Search executable.

    python -m PyInstaller packaging/universal-search-onefile.spec --noconfirm \
        --distpath dist/UniversalSearch-onefile

Output — **one file, no folder**:

    dist/UniversalSearch-onefile/UniversalSearch.exe

A one-file spec writes the executable straight into ``--distpath`` with no
folder of its own, so it needs its own distpath: pointed at ``dist`` it would
drop ``UniversalSearch.exe`` right next to the one-dir ``dist/UniversalSearch``
folder. ``packaging/build.ps1`` and the CI workflow both pass one.

Why this build exists (phase 037). The one-dir build in
``universal-search.spec`` is the one to install: it starts fast and is easy to
inspect. But it is *not* one artefact. The 2.0.0 audit learned that the hard
way: the release attached the two bare ``.exe`` files, and on download they
died with ``PYI-8: Failed to load Python DLL`` because they need the sibling
``_internal/`` folder. Publishing a one-dir build is a zip; publishing an
executable is an executable.

The cost is stated rather than hidden, because it is real:

* **Startup is slower.** The archive is unpacked into ``%TEMP%`` on every run.
  With roughly 30 MB of runtime that is on the order of a second, on every
  launch, including every CLI command. It is the wrong choice for the
  background worker, which starts often and does little.
* **It is a console build** (``console=True``), and that is a decision with a
  consequence on both sides. The first version of this spec was windowed, and
  the build script's smoke caught the result: ``--version`` and the exit codes
  worked, but ``search`` printed nothing at all, because a windowed
  PyInstaller build has no ``sys.stdout`` to print to and Python's flush raises
  ``OSError [Errno 22]`` on exit. An application whose search results cannot be
  read is not a portable copy of the application.

  So the console is kept, and the cost is paid on the other side: launching the
  window by double-clicking leaves a console window behind it. That is the
  trade a portable copy makes — someone who wants a clean window installs
  ``universal-search.spec`` instead, which ships both executables separately.

Both builds carry the same icon and the same Windows version resource, so
``tests/test_release.py`` pins them together: a version that drifts in one and
not the other fails the suite.
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = ROOT / "packaging" / "universal_search.ico"
# Same source of truth as the one-dir spec and the installer:
# universal_search.__version__ (tests/test_release.py enforces the match).
VERSION_FILE = ROOT / "packaging" / "version_file.txt"

a = Analysis(
    [str(ROOT / "packaging" / "entry-gui.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Imported lazily at runtime; listed explicitly so a frozen worker
        # keeps filesystem watchers and lifecycle control even in one file.
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

# exclude_binaries=False is the whole point: the binaries and the archive go
# *inside* the executable, which PyInstaller unpacks at run time.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="UniversalSearch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=True, deliberately: a windowed build has no stdout, so search
    # results would be lost. Measured by the build script's smoke, not assumed.
    console=True,
    icon=str(ICON),
    version=str(VERSION_FILE),
    # A USB stick may be removed while a search is open. Without this, removing
    # it leaves the child processes with no code to read and Windows reports a
    # crash dialog; with it they exit cleanly.
    disable_windowed_traceback=False,
)