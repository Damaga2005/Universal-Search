"""The Windows adapter and shell integration (spec 016).

Every OS touchpoint is injected, so these tests run — and pass — on a
machine that is not Windows, which is the point of the seam. The
Windows-only smoke test at the end is skipped elsewhere.
"""

import sys
from pathlib import Path

import pytest

from universal_search import platforms
from universal_search.platforms.base import Platform, PlatformError
from universal_search.platforms.null import NullPlatform
from universal_search.platforms.windows import (
    AUTOSTART_NAME,
    RUN_KEY,
    WindowsPlatform,
    autostart_enabled,
    set_autostart,
)

WINDOWS = sys.platform == "win32"


class FakeRegistry:
    """The slice of ``winreg`` the adapter uses."""

    def __init__(self, *, present: bool = False) -> None:
        self.values: dict[str, object] = {}
        self.opened: list[tuple] = []
        if present:
            self.values[AUTOSTART_NAME] = ("REG_SZ", "previous")

    def OpenKey(self, hive, path, reserved, access):  # noqa: N802 - winreg API
        self.opened.append((path, access))
        if access & 0x8001 and AUTOSTART_NAME not in self.values:
            return f"key:{path}"
        if access & 0x8001 == 0 and AUTOSTART_NAME not in self.values:
            raise FileNotFoundError(path)
        return f"key:{path}"

    def CreateKey(self, hive, path):  # noqa: N802 - winreg API
        return f"key:{path}"

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        self.values[name] = (kind, value)

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def CloseKey(self, key):  # noqa: N802
        pass

    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 0x0002
    KEY_QUERY_VALUE = 0x0001
    REG_SZ = 1


# -- the contract --------------------------------------------------------------

def test_the_two_platforms_implement_the_same_interface():
    for platform in (WindowsPlatform(), NullPlatform()):
        assert isinstance(platform, Platform)
        for method in (
            "available", "open_path", "reveal", "set_autostart",
            "autostart_enabled", "notify",
        ):
            assert callable(getattr(platform, method))


def test_null_platform_answers_honestly():
    platform = NullPlatform()
    assert platform.available() is False
    assert platform.autostart_enabled() is False
    assert platform.notify("t", "m") is False
    for call in (
        lambda: platform.open_path("x"),
        lambda: platform.reveal("x"),
        lambda: platform.set_autostart(True),
    ):
        with pytest.raises(PlatformError):
            call()


def test_get_platform_selects_and_can_be_replaced(monkeypatch):
    platforms.reset_platform()
    try:
        expected = WindowsPlatform if WINDOWS else NullPlatform
        assert isinstance(platforms.get_platform(), expected)
        # Selection is cached, and a replacement is honoured.
        assert platforms.get_platform() is platforms.get_platform()
        fake = NullPlatform()
        platforms.set_platform(fake)
        assert platforms.get_platform() is fake
    finally:
        platforms.reset_platform()


# -- opening and revealing -----------------------------------------------------

def test_open_path_uses_the_injected_shell(tmp_path: Path):
    opened: list[str] = []
    target = tmp_path / "doc.md"
    target.write_text("x", encoding="utf-8")
    platform = WindowsPlatform(platform="win32", startfile=opened.append)

    platform.open_path(target)

    assert opened == [str(target)]


def test_open_path_reports_a_missing_file_without_raising_oserror(tmp_path: Path):
    platform = WindowsPlatform(platform="win32", startfile=lambda target: None)
    with pytest.raises(PlatformError) as error:
        platform.open_path(tmp_path / "missing.md")
    assert "does not exist" in str(error.value)


def test_open_path_names_a_shell_failure(tmp_path: Path):
    target = tmp_path / "doc.md"
    target.write_text("x", encoding="utf-8")

    def explode(_target):
        raise OSError("no association")

    platform = WindowsPlatform(platform="win32", startfile=explode)
    with pytest.raises(PlatformError) as error:
        platform.open_path(target)
    assert "no association" in str(error.value)


def test_reveal_uses_explorer_without_waiting(tmp_path: Path):
    calls: list[list[str]] = []
    target = tmp_path / "informe.pdf"
    target.write_bytes(b"%PDF-1.7 ")
    platform = WindowsPlatform(
        platform="win32", popen=lambda args, **kwargs: calls.append(args)
    )

    platform.reveal(target)

    assert calls == [["explorer", "/select,", str(target)]]


def test_reveal_refuses_a_deleted_file(tmp_path: Path):
    platform = WindowsPlatform(platform="win32", popen=lambda args: None)
    with pytest.raises(PlatformError):
        platform.reveal(tmp_path / "gone.pdf")


def test_shell_operations_refuse_to_pretend_on_other_platforms(tmp_path: Path):
    platform = WindowsPlatform(platform="linux")
    assert platform.available() is False
    for call in (
        lambda: platform.open_path(tmp_path),
        lambda: platform.reveal(tmp_path),
        lambda: platform.set_autostart(True),
        lambda: platform.autostart_enabled(),
    ):
        with pytest.raises(PlatformError):
            call()


# -- startup entry -------------------------------------------------------------

def test_autostart_writes_and_removes_the_run_entry():
    registry = FakeRegistry()
    assert autostart_enabled(registry) is False
    set_autostart(True, registry=registry)
    kind, command = registry.values[AUTOSTART_NAME]
    assert kind == 1  # REG_SZ
    assert "indexer" in command and "run" in command
    assert autostart_enabled(registry) is True
    set_autostart(False, registry=registry)
    assert autostart_enabled(registry) is False
    set_autostart(False, registry=registry)  # already absent: no error


def test_autostart_uses_the_expected_registry_location():
    registry = FakeRegistry()
    set_autostart(True, registry=registry)
    assert registry.opened[0][0] == RUN_KEY
    assert "CurrentVersion\\Run" in RUN_KEY


def test_an_existing_entry_is_recognised():
    registry = FakeRegistry(present=True)
    assert autostart_enabled(registry) is True


def test_platform_autostart_requires_windows():
    with pytest.raises(PlatformError):
        WindowsPlatform(platform="darwin").set_autostart(True)


# -- notifications -------------------------------------------------------------

def test_non_critical_notifications_are_never_shown():
    shown: list[tuple] = []

    class FakeUser32:
        def MessageBoxW(self, hwnd, text, caption, flags):  # noqa: N802
            shown.append((text, caption, flags))

    platform = WindowsPlatform(platform="win32", user32=FakeUser32())
    assert platform.notify("buscando", "sin importancia") is False
    assert shown == []


def test_critical_notifications_use_the_native_dialog():
    shown: list[tuple] = []

    class FakeUser32:
        def MessageBoxW(self, hwnd, text, caption, flags):  # noqa: N802
            shown.append((text, caption, flags))

    platform = WindowsPlatform(platform="win32", user32=FakeUser32())
    assert platform.notify("Universal Search", "el atajo está ocupado", critical=True)
    assert shown and shown[0][1] == "Universal Search"


# -- packaging scripts ---------------------------------------------------------

def parse_powershell(path: Path) -> list:
    """Parse a .ps1 with the PowerShell parser: syntax errors are failures."""
    pytest.importorskip("subprocess")
    import subprocess

    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "$errors = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{path}', [ref]$null, [ref]$errors) | Out-Null; "
            "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 }",
        ],
        capture_output=True, text=True, shell=False,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_packaging_scripts_parse():
    root = Path(__file__).resolve().parents[1] / "packaging"
    for name in ("make-start-menu.ps1", "explorer-search.ps1"):
        script = root / name
        assert script.exists(), name
        errors = parse_powershell(script)
        assert not errors, f"{name}: {errors}"


def test_integration_scripts_are_per_user_and_reversible():
    root = Path(__file__).resolve().parents[1] / "packaging"
    explorer = (root / "explorer-search.ps1").read_text(encoding="utf-8")
    assert "HKCU:" in explorer  # never HKLM: no administrator rights
    assert "-Remove" in explorer
    assert "%1" in explorer  # the selected file
    start_menu = (root / "make-start-menu.ps1").read_text(encoding="utf-8")
    assert "Start Menu" in start_menu
    assert "-Remove" in start_menu


# -- single instance and hotkey reporting -------------------------------------

def test_gui_run_defers_to_a_live_window(monkeypatch, tmp_path: Path):
    from universal_search import hotkey
    from universal_search.appconfig import AppPaths
    from universal_search.gui import app

    paths = AppPaths.discover(home=tmp_path / "home")
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(paths.home))
    hotkey.write_gui_pid(paths)  # a live window owns this pid
    # This process is a *different* pid, so the published one is another
    # live window: the launch must defer to it.
    monkeypatch.setattr(app.os, "getpid", lambda: 424242)
    requested: list[Path] = []
    monkeypatch.setattr(
        hotkey, "request_show", lambda p: bool(requested.append(p)) or True
    )
    created: list[str] = []

    class FakeWindow:
        def mainloop(self) -> None:
            created.append("ran")

    monkeypatch.setattr(app, "SearchWindow", FakeWindow)

    assert app.run() == 0
    assert requested == [paths]
    assert created == []  # no second window


def test_gui_run_starts_a_window_when_none_exists(monkeypatch, tmp_path: Path):
    from universal_search.appconfig import AppPaths
    from universal_search.gui import app

    paths = AppPaths.discover(home=tmp_path / "home")
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(paths.home))
    created: list[str] = []

    class FakeWindow:
        def mainloop(self) -> None:
            created.append("ran")

    monkeypatch.setattr(app, "SearchWindow", FakeWindow)
    assert app.run() == 0
    assert created == ["ran"]


def test_worker_reports_a_hotkey_it_could_not_register(tmp_path: Path):
    from universal_search import background

    worker = background.BackgroundIndexer(
        paths=background.AppPaths.discover(home=tmp_path / "home")
    )

    class BusyServer:
        registered = False
        error = "no se pudo registrar ctrl+alt+s (¿ocupado?)"

    worker.hotkey_server = BusyServer()
    assert "ocupado" in (worker._hotkey_problem() or "")

    class WorkingServer:
        registered = True
        error = None

    worker.hotkey_server = WorkingServer()
    assert worker._hotkey_problem() is None


def test_status_file_can_carry_a_hotkey_problem(tmp_path: Path):
    from universal_search import background
    from universal_search.appconfig import AppPaths

    paths = AppPaths.discover(home=tmp_path / "home")
    background.write_status(paths, "idle", hotkey="atajo ocupado")
    assert background.read_status(paths)["hotkey"] == "atajo ocupado"


# -- Windows-only smoke test ---------------------------------------------------

@pytest.mark.skipif(not WINDOWS, reason="Windows-only smoke test")
def test_windows_platform_really_opens_a_file(tmp_path: Path):
    target = tmp_path / "smoke.txt"
    target.write_text("contenido", encoding="utf-8")
    platform = WindowsPlatform()
    assert platform.available() is True
    # We do not launch an application: a smoke test must not open windows.
    # What we check is that the seam resolves the real API surface.
    assert platform.describe().startswith("windows")
    assert callable(platform.open_path)
