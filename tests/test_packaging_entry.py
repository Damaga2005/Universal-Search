"""Regression tests for the shared CLI and PyInstaller entry points."""

import importlib.util
import runpy
import sys
from pathlib import Path

import pytest

from universal_search import cli


ROOT = Path(__file__).resolve().parents[1]
ENTRY_GUI = ROOT / "packaging" / "entry-gui.py"
CLI_MODULE = ROOT / "src" / "universal_search" / "cli.py"


def _load_entry_gui():
    spec = importlib.util.spec_from_file_location("entry_gui_test", ENTRY_GUI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_packaging_entry_preserves_nonzero_cli_exit_code(monkeypatch) -> None:
    module = _load_entry_gui()
    monkeypatch.setattr(sys, "argv", ["UniversalSearch.exe", "tray"])
    monkeypatch.setattr(cli, "main", lambda: 17)

    assert module.main() == 17


def test_packaging_entry_maps_legacy_none_to_zero(monkeypatch) -> None:
    module = _load_entry_gui()
    monkeypatch.setattr(sys, "argv", ["UniversalSearch.exe", "search", "query"])
    monkeypatch.setattr(cli, "main", lambda: None)

    assert module.main() == 0


def test_cli_module_guard_propagates_tray_exit_code(tmp_path, monkeypatch) -> None:
    from universal_search import appconfig, tray
    from universal_search.appconfig import AppConfig, AppPaths

    calls = []
    logged = []
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(tmp_path / "home"))
    # Phase 043 gave setup_logging a second argument, and this stub kept the
    # old one-argument shape -- so the test failed on the signature before it
    # ever reached the thing it is about. The stub now records both arguments
    # instead of ignoring them, because the level the tray hands over is now
    # part of what this path is supposed to get right.
    monkeypatch.setattr(
        appconfig, "setup_logging",
        lambda paths, level=None: logged.append((paths, level)),
    )
    monkeypatch.setattr(
        tray,
        "run_tray",
        lambda **kwargs: calls.append(kwargs) or 17,
    )
    monkeypatch.setattr(sys, "argv", ["universal-search", "tray"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(CLI_MODULE), run_name="__main__")

    assert exit_info.value.code == 17
    assert len(calls) == 1
    assert len(logged) == 1
    # The tray is configured from the resolved log level, not from a constant:
    # that was phase 043's whole point, and until this assertion existed a
    # regression here would have failed on a TypeError instead of on the value.
    assert logged[0][1] == AppConfig.load(AppPaths.discover()).log_level
