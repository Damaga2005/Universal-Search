# Phase 010 — Windows Release: Implementation Report

## What was implemented

- **Version single-sourced at `1.0.0`**:
  - `src/universal_search/__init__.py` holds `__version__` — the only place
    the number exists as code.
  - `pyproject.toml` switched from a static `version = "0.1.0"` to hatch
    dynamic versioning (`dynamic = ["version"]` + `[tool.hatch.version]
    path = "src/universal_search/__init__.py"`): there is now nothing left
    to forget when releasing.
  - `universal-search --version` → `universal-search 1.0.0` (argparse
    `version` action, works before the required subcommand).
  - `packaging/version_file.txt`: PyInstaller `VSVersionInfo` resource fed
    to **both** executables (`EXE(..., version=str(VERSION_FILE))`), so
    file properties show `ProductVersion/FileVersion 1.0.0` and
    `FileDescription: Local-first universal search for Windows`.
  - The desktop window title carries it too: `Universal Search 1.0.0`.
  - `packaging/installer.iss` `#define MyAppVersion` kept in sync by test.
- **Installer** (`packaging/install.ps1`, verified — see E2E below):
  - Copies a built `dist/UniversalSearch` to `%LOCALAPPDATA%\Programs\UniversalSearch`
    (per-user, no admin); creates the Start Menu entry by reusing the
    existing `make-shortcut.ps1`; never writes user data into the install
    directory (`%LOCALAPPDATA%\Universal Search` is created by the app on
    first run, not by the installer).
  - Writes **`install-manifest.json`**: product, version (read from the
    installed CLI when runnable), install/data dirs, every copied file
    (relative, self-excluding), created shortcuts, `backgroundWorker` flag
    and `upgraded` flag.
  - **Upgrade behavior**: a pre-existing manifest is detected, the old
    worker is stopped through the *old* executable (only when the manifest
    says a worker is registered) and any running GUI is closed before
    copying; re-running the installer over an install is the tested upgrade
    path and preserves user data.
  - `-Autostart` registers the background indexer through the application's
    own tested command (`universal-search.exe indexer autostart on`), so the
    Run key always points at the installed executable.
  - `-NoStartMenu` for silent/test installs.
- **Clean uninstall** (`packaging/uninstall.ps1`, verified):
  - Deletes exactly the manifest-listed files, prunes emptied directories,
    removes the recorded shortcuts, then removes the install directory only
    if nothing foreign remains (otherwise it warns and lists what it left).
  - Stops the worker and runs `indexer autostart off` **before** deleting
    binaries when the manifest says a worker is registered.
  - **User data is kept by default** and only removed with an explicit
    `-PurgeData` (plus an optional `-DataDir`), clearly distinguishing
    application files from index/config/logs — spec requirement.
- **Inno Setup alternative** (`packaging/installer.iss`): full script
  (per-user `PrivilegesRequired=lowest`, icon, Start Menu + optional desktop
  shortcut, optional autostart task routed through the app's own command,
  `[UninstallRun]` mirroring the PowerShell order). **Honest note: Inno
  Setup (`ISCC.exe`) is not installed on this machine, so the script is
  provided as source and has NOT been compiled here** — the tested
  installer of record is `install.ps1`.
- **Application icon**: `packaging/universal_search.ico` is attached to
  both executables (and the Inno script); in frozen builds Windows derives
  the taskbar/window icon from the executable.
- **Application data directory**: `%LOCALAPPDATA%\Universal Search`
  (overridable `UNIVERSAL_SEARCH_HOME`) holds `index.db`, `config.json`,
  logs and worker status — outside the installation directory, as required.
  Logs rotate via `RotatingFileHandler` (1 MB × 3) already in place.
- **Database migration strategy formalized** (`index/database.py`):
  - `SCHEMA_VERSION = 3` + `PRAGMA user_version` stamped after every
    connect, so the applied level is observable and future *ordered*
    migrations have a version to step from.
  - The two existing mechanisms stay idempotent: `SCHEMA` (creates missing
    tables, e.g. FTS on legacy files) and `MIGRATIONS` (column
    introspection: adds `mtime_ns`, `last_seen_run`, `availability` only
    when absent). A legacy database keeps every row while gaining the new
    columns — verified by test.
  - Release rule documented in code: bump `SCHEMA_VERSION` whenever
    `SCHEMA` or `MIGRATIONS` change; tests fail on drift.
- **Real bug found and fixed during the E2E run** — the CLI's
  `--database` default was `Path("universal-search.db")`, i.e. **relative to
  the current directory**. `universal-search index C:\docs` therefore wrote
  the index into whatever folder the command was run from, while the GUI and
  the background worker read `%LOCALAPPDATA%\Universal Search\index.db`:
  a split-brain index (CLI-visible results, empty window). Fixed so the
  default is `AppPaths.discover().database` (one index, one location),
  documented in the parser help, covered by a regression test, and a stray
  `universal-search.db` produced during testing was removed and added to
  `.gitignore` (`/universal-search.db*`).

## Technical decisions

1. **Manifest-based uninstall instead of "delete the folder"**: recording
   the exact installed files lets uninstall distinguish application files
   from anything foreign, and lets upgrade stop only what it registered.
2. **Autostart always goes through the app's own command** (both installers
   and tests): one code path writes the Run key, so the recorded path can
   never disagree with the real one; uninstall reverses it the same way.
3. **`user_version` is a stamp, not the migration driver**: column
   additions remain introspection-driven (idempotent and safe on every
   historical database), while the stamp makes the applied level
   verifiable — chosen over a rewrite to versioned migration scripts
   because every shipped database must upgrade losslessly.
4. **Upgrade = re-run the installer**: tested and simple, no differential
   patching; user data is outside the install tree, so overwrite is safe.
5. **Version drift fails the suite**: tests parse `version_file.txt`,
   `pyproject.toml` and `installer.iss` against `__version__`, and count
   `version=str(VERSION_FILE)`/`icon=str(ICON)` occurrences in the spec, so
   a release cannot ship with mismatched numbers or an unversioned exe.

## Dependencies

None added. The installers are plain PowerShell using built-in
`WScript.Shell` COM (shared with the pre-existing shortcut helper);
`hatchling` dynamic versioning uses the build backend already declared;
PyInstaller was already the declared build extra.

## Limitations

- `installer.iss` is unverified by compilation (no Inno Setup on this
  machine); `install.ps1`/`uninstall.ps1` are the verified path and the
  tests exercise them for real.
- The E2E cycle could not click through an actual GUI session headlessly:
  acceptance steps 1-7 were exercised with the real installed binaries
  (install → index → search → worker start/status/stop → upgrade →
  uninstall); the window itself is covered by the Tk test suite.
- `version` resource read-back at install time is best effort: when the CLI
  cannot run, the manifest records `version = "unknown"` (never happens for
  real builds — the real build reported `1.0.0`).
- One-time Windows reboot/logon is needed for autostart to take effect
  (standard Run-key semantics).

## Tests

`tests/test_release.py` (**10**) + version title test in
`tests/test_gui.py` (17 total there). Full suite: **208 passed** (was 197
after phase 009).

- version single-sourcing: `__version__` semver-shaped, pyproject dynamic +
  path, static `version = "` line gone;
- Windows version resource matches `__version__` (filevers/prodvers/
  FileVersion/ProductVersion);
- spec pins both exe names, exactly two `version=` and two `icon=`
  entries; `installer.iss` carries the same version, an uninstall display
  name and the user-data distinction;
- `universal-search --version` prints `universal-search 1.0.0`, exit 0;
- fresh database stamps `PRAGMA user_version = SCHEMA_VERSION`, second
  connect is a no-op;
- **legacy database upgrade preserves every row** while adding the newer
  columns and creating the FTS table;
- `default_home()` equals `%LOCALAPPDATA%\Universal Search` and is never the
  repository/install directory;
- **CLI default database is the user data directory** (index+search with no
  `--database` from a temp CWD: `home/index.db` created, no
  `universal-search.db` in the CWD);
- real PowerShell roundtrip: fresh install (files, shortcut, manifest with
  self-excluding file list, `backgroundWorker=false`, distinct `dataDir`) →
  upgrade (`Existing installation detected`, `upgraded=true`, data kept) →
  uninstall (install dir and shortcut gone, **user data kept**);
- `-PurgeData` deletes the data directory only when explicitly requested.

## Acceptance criteria (fresh Windows installation)

1. **install Universal Search** — `powershell -ExecutionPolicy Bypass -File
   packaging\install.ps1` (real E2E: files + Start Menu shortcut +
   manifest, no admin rights) ✅
2. **configure indexed folders** — `universal-search.exe index <folder>`
   writes into the shared data directory (**after the fixed default**;
   previously required `--database`) ✅
3. **build an index** — `Indexed 2 files. created=2 …` from the installed
   console exe ✅
4. **close the GUI** — window close never touches the worker (existing
   test) ✅
5. **keep indexing in the background** — installed exe `indexer start` →
   `idle (pid 13980)` → `indexer stop` verified in the E2E run; Run key
   registered via `-Autostart` ✅
6. **reopen search through Windows/shortcut** — `.lnk` created by the
   installer (target: installed `UniversalSearch.exe`) ✅
7. **search local and OneDrive content** — engine tests for both sources
   (phases 007-009) and `search practica` returning both files ✅
8. **uninstall cleanly** — E2E: install dir removed, shortcut removed,
   `Universal Search` Run key removed, no leftover processes, **index and
   config preserved** (`User data KEPT: … Delete it later with:
   uninstall.ps1 -PurgeData`) ✅

## How to run / manual verification performed

```bash
# build
.venv\Scripts\python -m pip install -e ".[build]"
.venv\Scripts\python -m PyInstaller packaging/universal-search.spec --noconfirm --clean

# version evidence
(Get-Item dist\UniversalSearch\UniversalSearch.exe).VersionInfo.ProductVersion  # 1.0.0
dist\UniversalSearch\universal-search.exe --version                              # universal-search 1.0.0

# install / upgrade / uninstall (real cycle executed for this report)
powershell -ExecutionPolicy Bypass -File packaging\install.ps1   -SourceDir dist\UniversalSearch -InstallDir "$env:TEMP\us010-app" -StartMenuPath "$env:TEMP\us010-menu" -Autostart
powershell -ExecutionPolicy Bypass -File packaging\install.ps1   # 2nd run: "Existing installation detected - upgrading in place..."
powershell -ExecutionPolicy Bypass -File packaging\uninstall.ps1 -InstallDir "$env:TEMP\us010-app" -StartMenuPath "$env:TEMP\us010-menu"
powershell -ExecutionPolicy Bypass -File packaging\uninstall.ps1 -PurgeData   # optional full cleanup

# release gates
.venv\Scripts\python -m pytest tests/test_release.py tests/test_gui.py -v
```

E2E transcript (abridged, this machine): install → Run key
`"…\us010-app\universal-search.exe" indexer run`, shortcut `True`,
manifest `version 1.0.0 / backgroundWorker True`; `index`+`search` from the
installed exe → `Indexed 2 files` / both results; worker
`indexer start (pid 13980)` → `idle` → `detenido`; second install →
`Existing installation detected` + `Upgrade complete … preserved`;
fixed default → `index.db in data dir: True`, `stray db in CWD: False`;
uninstall → `install dir exists: False`, `shortcut exists: False`,
`user data KEPT: True`, `Run key removed: True`, no leftover processes.
