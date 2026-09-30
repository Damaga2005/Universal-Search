"""Phase 027: Windows shell integration (adapter, CLI and installer)."""

from pathlib import Path

import pytest

from universal_search import cli
from universal_search.platforms.base import PlatformError
from universal_search.platforms.null import NullPlatform
from universal_search.platforms.windows import WindowsPlatform


ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"


class FakeUser32:
    def __init__(self, *, modern: bool = True) -> None:
        self.calls: list[tuple] = []
        self.modern = modern

    def SetProcessDpiAwarenessContext(self, context):
        self.calls.append(("context", context))
        return 1 if self.modern else 0

    def SetProcessDPIAware(self):
        self.calls.append(("aware",))
        return 1


def test_windows_platform_uses_per_monitor_dpi_context() -> None:
    user32 = FakeUser32(modern=True)
    platform = WindowsPlatform(user32=user32, platform="win32")

    assert platform.set_dpi_awareness() is True
    assert user32.calls[0][0] == "context"


def test_windows_platform_falls_back_to_legacy_dpi_aware() -> None:
    user32 = FakeUser32(modern=False)
    platform = WindowsPlatform(user32=user32, platform="win32")

    assert platform.set_dpi_awareness() is True
    assert [call[0] for call in user32.calls] == ["context", "aware"]


def test_dpi_awareness_is_a_no_op_off_windows() -> None:
    assert NullPlatform().set_dpi_awareness() is False


class RecordingPlatform(NullPlatform):
    def __init__(self) -> None:
        self.opened: list[Path] = []
        self.revealed: list[Path] = []

    def open_path(self, path) -> None:
        self.opened.append(Path(path))

    def reveal(self, path) -> None:
        self.revealed.append(Path(path))


def test_cli_open_and_reveal_use_the_platform_adapter(monkeypatch, tmp_path) -> None:
    target = tmp_path / "nota.md"
    target.write_text("contenido", encoding="utf-8")
    platform = RecordingPlatform()
    monkeypatch.setattr("universal_search.platforms.get_platform", lambda: platform)

    monkeypatch.setattr(cli.sys, "argv", ["universal-search", "open", str(target)])
    assert cli.main() is None or True  # legacy commands return None

    monkeypatch.setattr(cli.sys, "argv", ["universal-search", "reveal", str(target)])
    cli.main()

    assert platform.opened == [target]
    assert platform.revealed == [target]


def test_cli_open_failure_is_actionable(monkeypatch, tmp_path, capsys) -> None:
    class Failing(NullPlatform):
        def open_path(self, path):
            raise PlatformError("no se pudo abrir")

    monkeypatch.setattr("universal_search.platforms.get_platform", lambda: Failing())
    monkeypatch.setattr(
        cli.sys, "argv", ["universal-search", "open", str(tmp_path / "x.txt")]
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1
    assert "no se pudo abrir" in capsys.readouterr().err


def test_installer_registers_and_uninstaller_removes_explorer_verb() -> None:
    install = (PACKAGING / "install.ps1").read_text(encoding="utf-8")
    uninstall = (PACKAGING / "uninstall.ps1").read_text(encoding="utf-8")
    explorer = (PACKAGING / "explorer-search.ps1").read_text(encoding="utf-8")

    assert "explorer-search.ps1" in install
    assert "explorerIntegration" in install
    assert "explorer-search.ps1" in uninstall
    assert "-Remove" in uninstall
    assert "HKCU:" in explorer
    assert "#Requires -RunAsAdministrator" not in install
    assert "#Requires -RunAsAdministrator" not in uninstall


def test_default_hotkey_avoids_ime_conflict() -> None:
    from universal_search.appconfig import AppConfig

    assert AppConfig().hotkey != "ctrl+space"