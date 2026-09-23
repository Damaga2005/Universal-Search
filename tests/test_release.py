"""Phase 010: version single-sourcing, migrations, installer semantics.

These are release gates: version drift, a lost index during upgrade, or an
uninstaller that touches user data must all fail the suite before shipping.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from universal_search import __version__
from universal_search.appconfig import default_home
from universal_search.cli import main
from universal_search.index.database import SCHEMA_VERSION, SearchDatabase

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"


# -- version single-sourcing ---------------------------------------------------

def test_version_is_single_sourced() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert "[tool.hatch.version]" in pyproject
    assert 'path = "src/universal_search/__init__.py"' in pyproject
    # the old static version line is gone: there is nothing left to forget
    assert not re.search(r'^version\s*=\s*"', pyproject, re.MULTILINE)


def test_windows_version_resource_matches_package_version() -> None:
    version_file = (PACKAGING / "version_file.txt").read_text(encoding="utf-8")
    major, minor, patch = (int(part) for part in __version__.split("."))
    assert f"filevers=({major}, {minor}, {patch}, 0)" in version_file
    assert f"prodvers=({major}, {minor}, {patch}, 0)" in version_file
    assert f"StringStruct('FileVersion', '{__version__}')" in version_file
    assert f"StringStruct('ProductVersion', '{__version__}')" in version_file


def test_spec_and_installer_pin_version_icon_and_exes() -> None:
    spec = (PACKAGING / "universal-search.spec").read_text(encoding="utf-8")
    # both executables carry the version resource and the product icon
    assert spec.count("version=str(VERSION_FILE)") == 2
    assert spec.count("icon=str(ICON)") == 2
    assert 'name="UniversalSearch"' in spec
    assert 'name="universal-search"' in spec

    iss = (PACKAGING / "installer.iss").read_text(encoding="utf-8")
    assert f'AppVersion "{__version__}"' in iss
    assert "UninstallDisplayName" in iss
    assert "user data" in iss.lower() or "user index" in iss.lower()


def test_cli_reports_version(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["universal-search", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert f"universal-search {__version__}" in out


# -- database migration strategy ----------------------------------------------

def test_fresh_database_stamps_the_schema_version(tmp_path) -> None:
    handle = SearchDatabase(tmp_path / "fresh.db")
    with handle.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    # connecting again is a no-op (idempotent migrations)
    with handle.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_upgrade_of_a_legacy_database_preserves_the_index(tmp_path) -> None:
    """Upgrades must preserve the search index where possible (spec 010)."""
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE documents ("
        "id TEXT PRIMARY KEY, source TEXT NOT NULL, path TEXT NOT NULL UNIQUE, "
        "name TEXT NOT NULL, extension TEXT NOT NULL, size INTEGER NOT NULL, "
        "created_at TEXT, modified_at TEXT, content_hash TEXT, "
        "indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    connection.execute(
        "INSERT INTO documents (id, source, path, name, extension, size) "
        "VALUES ('doc-1', 'local', 'C:/uni/apuntes.md', 'apuntes.md', '.md', 10)"
    )
    connection.commit()
    connection.close()

    handle = SearchDatabase(path)
    with handle.connect() as connection:
        rows = connection.execute("SELECT id, name FROM documents").fetchall()
        assert [row["id"] for row in rows] == ["doc-1"]  # index preserved
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(documents)")
        }
        assert {"mtime_ns", "last_seen_run", "availability"} <= columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        # the FTS table is created alongside on legacy files
        assert connection.execute("SELECT count(*) FROM documents_fts").fetchone()[0] == 0


# -- data directory separation -------------------------------------------------

def test_user_data_lives_outside_the_installation(monkeypatch) -> None:
    monkeypatch.delenv("UNIVERSAL_SEARCH_HOME", raising=False)
    home = default_home()
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        assert home == Path(os.environ["LOCALAPPDATA"]) / "Universal Search"
    # never the repository/install directory
    assert ROOT not in home.parents and home != ROOT


def test_cli_defaults_the_index_to_the_user_data_directory(
    tmp_path, monkeypatch, capsys
) -> None:
    """CLI, GUI and worker must share ONE index location (spec 010).

    The historical default (``universal-search.db`` relative to the current
    directory) silently split the index from the window and the worker.
    """
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)  # the CWD must never receive the database
    files = tmp_path / "files"
    files.mkdir()
    (files / "hola.md").write_text(
        "contenido de prueba de la fase 010", encoding="utf-8"
    )

    monkeypatch.setattr(sys, "argv", ["universal-search", "index", str(files)])
    main()
    monkeypatch.setattr(sys, "argv", ["universal-search", "search", "contenido"])
    main()
    assert "hola.md" in capsys.readouterr().out

    assert (tmp_path / "home" / "index.db").exists()
    assert not (tmp_path / "universal-search.db").exists()  # CWD stays clean


# -- installer semantics (real PowerShell scripts) -----------------------------

def run_powershell(script: Path, **parameters) -> subprocess.CompletedProcess:
    command = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(script),
    ]
    for name, value in parameters.items():
        command += [f"-{name}", str(value)]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"{script.name} failed ({completed.returncode})\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return completed


@pytest.fixture
def fake_dist(tmp_path) -> Path:
    """A minimal built dist folder (the scripts only copy and record files)."""
    source = tmp_path / "dist"
    source.mkdir()
    (source / "UniversalSearch.exe").write_bytes(b"MZ-fake-gui")
    (source / "universal-search.exe").write_bytes(b"MZ-fake-cli")
    internal = source / "_internal"
    internal.mkdir()
    (internal / "runtime.bin").write_bytes(b"runtime")
    return source


def test_install_reinstall_uninstall_roundtrip(tmp_path, fake_dist) -> None:
    install_dir = tmp_path / "Programs" / "UniversalSearch"
    menu_dir = tmp_path / "menu"
    data_dir = tmp_path / "user-data"
    data_dir.mkdir()
    (data_dir / "index.db").write_bytes(b"index")  # pre-existing user data

    install = PACKAGING / "install.ps1"
    uninstall = PACKAGING / "uninstall.ps1"

    # 1) fresh install: files, shortcut and manifest in place
    run_powershell(
        install, SourceDir=fake_dist, InstallDir=install_dir, StartMenuPath=menu_dir
    )
    assert (install_dir / "UniversalSearch.exe").exists()
    assert (install_dir / "universal-search.exe").exists()
    assert (install_dir / "_internal" / "runtime.bin").exists()
    shortcut = menu_dir / "Universal Search.lnk"
    assert shortcut.exists()

    manifest_path = install_dir / "install-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    assert manifest["product"] == "Universal Search"
    assert "UniversalSearch.exe" in manifest["files"]
    assert "install-manifest.json" not in manifest["files"]  # self-excluding
    assert any(name.endswith("runtime.bin") for name in manifest["files"])
    assert manifest["shortcuts"] == [str(shortcut)]
    assert manifest["backgroundWorker"] is False  # no -Autostart in this test
    assert manifest["upgraded"] is False
    # application files and user data are distinct locations
    assert Path(manifest["dataDir"]) != install_dir
    assert manifest["dataDir"].endswith("Universal Search")

    # 2) upgrade: re-running over an existing install is detected and safe
    upgraded = run_powershell(
        install, SourceDir=fake_dist, InstallDir=install_dir, StartMenuPath=menu_dir
    )
    assert "Existing installation detected" in upgraded.stdout
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    assert manifest["upgraded"] is True
    assert (install_dir / "UniversalSearch.exe").exists()
    assert (data_dir / "index.db").exists()  # upgrade preserved user data

    # 3) uninstall: application gone, user data kept
    run_powershell(
        uninstall, InstallDir=install_dir, StartMenuPath=menu_dir, DataDir=data_dir
    )
    assert not install_dir.exists()  # only our files were there -> removed
    assert not shortcut.exists()
    assert (data_dir / "index.db").exists()  # KEPT by default (spec 010)


def test_uninstall_purges_data_only_with_the_explicit_flag(tmp_path, fake_dist) -> None:
    install_dir = tmp_path / "Programs" / "UniversalSearch"
    menu_dir = tmp_path / "menu"
    data_dir = tmp_path / "user-data"
    data_dir.mkdir()
    (data_dir / "index.db").write_bytes(b"index")

    run_powershell(
        PACKAGING / "install.ps1",
        SourceDir=fake_dist,
        InstallDir=install_dir,
        StartMenuPath=menu_dir,
    )
    run_powershell(
        PACKAGING / "uninstall.ps1",
        InstallDir=install_dir,
        StartMenuPath=menu_dir,
        DataDir=data_dir,
        PurgeData="true",  # switch flags accept any value when present
    )
    assert not install_dir.exists()
    assert not data_dir.exists()  # explicitly purged this time
