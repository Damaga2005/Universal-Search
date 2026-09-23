import json
import struct
import subprocess
from pathlib import Path

import universal_search.appconfig as appconfig
from universal_search.appconfig import AppConfig, AppPaths, default_home, setup_logging
from universal_search.gui import services
from universal_search.gui.services import SearchService
from universal_search.index.indexer import Indexer
from universal_search.providers.local import discover_local


# -- paths and locations -------------------------------------------------------

def test_app_paths_honour_environment_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(tmp_path / "home"))
    paths = AppPaths.discover()

    assert paths.home == tmp_path / "home"
    paths.ensure()
    assert paths.home.is_dir()
    names = {
        paths.database.name,
        paths.config_file.name,
        paths.log_file.name,
        paths.status_file.name,
        paths.pause_file.name,
        paths.lock_file.name,
    }
    assert len(names) == 6  # every runtime artifact has its own file


def test_default_home_uses_localappdata(monkeypatch) -> None:
    monkeypatch.delenv("UNIVERSAL_SEARCH_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
    assert default_home() == Path(r"C:\Users\u\AppData\Local") / "Universal Search"


# -- search service -------------------------------------------------------------

def test_service_starts_with_missing_database(tmp_path: Path) -> None:
    database = tmp_path / "fresh" / "never-created.db"
    assert not database.exists()

    service = SearchService(
        database_path=database, paths=AppPaths(tmp_path / "home")
    )

    assert service.search("whatever") == []  # app must start anyway
    assert database.exists()


def test_service_finds_indexed_documents(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "capacitor.md").write_text("capacitor discharge formula", encoding="utf-8")
    service = SearchService(
        database_path=tmp_path / "index.db", paths=AppPaths(tmp_path / "home")
    )
    for document in discover_local(files):
        Indexer(service.database).upsert(document)

    results = service.search("capacitor")

    assert [result.name for result in results] == ["capacitor.md"]


# -- open and reveal -------------------------------------------------------------

def test_open_path_uses_windows_shell(tmp_path: Path, monkeypatch) -> None:
    called: dict[str, str] = {}
    monkeypatch.setattr(
        services.os, "startfile", lambda target: called.setdefault("target", target)
    )

    services.open_path(tmp_path / "doc.md")

    assert called["target"] == str(tmp_path / "doc.md")


def test_reveal_invokes_explorer_with_select(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        services.subprocess, "Popen", lambda args, **kwargs: calls.append(args)
    )

    services.reveal_in_explorer(r"C:\docs\informe.pdf")

    assert calls == [["explorer", "/select,", r"C:\docs\informe.pdf"]]


# -- persistent configuration -----------------------------------------------------

def test_config_defaults_when_missing(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    assert AppConfig.load(paths) == AppConfig()
    assert AppConfig.load(paths).ignore_rules() is not None


def test_config_round_trip(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    config = AppConfig(
        roots=(r"D:\apuntes", r"D:\trabajo"),
        ignore_dirs=("privado",),
        ignore_patterns=("*.off",),
        start_with_windows=True,
        indexer_interval_seconds=60,
        indexer_file_delay=0.5,
        window_geometry="800x600",
    )

    config.save(paths)
    loaded = AppConfig.load(paths)

    assert loaded == config
    assert json.loads(paths.config_file.read_text(encoding="utf-8"))["roots"] == [
        r"D:\apuntes",
        r"D:\trabajo",
    ]


def test_config_corrupt_file_falls_back_to_defaults(tmp_path, caplog) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text("not { valid json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        loaded = AppConfig.load(paths)

    assert loaded == AppConfig()
    assert "using defaults" in caplog.text


def test_config_ignores_unknown_and_wrongly_typed_fields(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text(
        json.dumps(
            {"roots": "not-a-list", "indexer_interval_seconds": "abc", "future": 1}
        ),
        encoding="utf-8",
    )

    loaded = AppConfig.load(paths)

    assert loaded.roots == ()  # wrong type -> default, no crash (forward compatible)
    assert loaded.indexer_interval_seconds == 300


def test_config_drives_ignore_rules(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    AppConfig(
        ignore_dirs=("privado",), ignore_patterns=("*.off",)
    ).save(paths)
    rules = AppConfig.load(paths).ignore_rules()

    assert rules.ignores_directory("privado")
    assert rules.ignores_file("tema.off")


# -- logging -----------------------------------------------------------------------

def test_setup_logging_writes_and_is_idempotent(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    try:
        logger = setup_logging(paths)
        logger.info("mensaje de escritorio")
        handlers_after_first = list(logger.handlers)

        setup_logging(paths)  # second call must not duplicate handlers

        assert logger.handlers == handlers_after_first
        content = paths.log_file.read_text(encoding="utf-8")
        assert "mensaje de escritorio" in content
        assert "INFO" in content
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def test_default_home_env_override_takes_priority(monkeypatch) -> None:
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", "C:/temp/custom-home")
    assert default_home() == Path("C:/temp/custom-home")


# -- packaging artifacts -----------------------------------------------------------

def test_icon_file_is_a_valid_png_compressed_ico() -> None:
    icon = Path(__file__).resolve().parents[1] / "packaging" / "universal_search.ico"
    data = icon.read_bytes()

    reserved, image_type, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, image_type) == (0, 1)
    assert count >= 1
    for index in range(count):
        _w, _h, _c, _r, planes, bpp, size, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + index * 16
        )
        assert (planes, bpp) == (1, 32)
        assert offset + size <= len(data)
        assert data[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"


def test_shortcut_script_creates_lnk(tmp_path: Path) -> None:
    script = (
        Path(__file__).resolve().parents[1] / "packaging" / "make-shortcut.ps1"
    )
    executable = tmp_path / "UniversalSearch.exe"
    executable.write_bytes(b"MZ fake")
    menu = tmp_path / "menu"

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-TargetExe",
            str(executable),
            "-StartMenuPath",
            str(menu),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert result.returncode == 0, result.stderr
    assert (menu / "Universal Search.lnk").exists()
