import io
import sys
from pathlib import Path

import pytest

from universal_search.cli import main


def run_cli(monkeypatch, *args) -> None:
    monkeypatch.setattr(sys, "argv", ["universal-search", *[str(a) for a in args]])


def test_cli_index_and_search(tmp_path: Path, monkeypatch, capsys) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "transistor_notes.md").write_text("biasing the class A amplifier stage", encoding="utf-8")
    database = tmp_path / "index" / "cli.db"

    monkeypatch.setattr(
        sys, "argv", ["universal-search", "index", str(files), "--database", str(database)]
    )
    main()
    assert "Indexed 1 files." in capsys.readouterr().out
    assert database.exists()

    monkeypatch.setattr(
        sys, "argv", ["universal-search", "search", "amplifier", "--database", str(database)]
    )
    main()
    output = capsys.readouterr().out
    assert "[local] transistor_notes.md" in output
    assert str(files.resolve()) in output
    assert "biasing" in output


def test_cli_search_before_any_index_creates_database(tmp_path: Path, monkeypatch, capsys) -> None:
    database = tmp_path / "fresh.db"
    monkeypatch.setattr(
        sys, "argv", ["universal-search", "search", "nothing here", "--database", str(database)]
    )

    main()

    assert capsys.readouterr().out == ""
    assert database.exists()


def test_cli_prints_results_the_console_encoding_cannot_represent(
    tmp_path: Path, monkeypatch
) -> None:
    """A legacy Windows console must degrade gracefully, not crash with a traceback."""
    files = tmp_path / "files"
    files.mkdir()
    (files / "flecha.md").write_text("el camino \u2192 el destino", encoding="utf-8")
    database = tmp_path / "index" / "cli.db"
    monkeypatch.setattr(
        sys, "argv", ["universal-search", "index", str(files), "--database", str(database)]
    )
    main()

    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(
        sys, "argv", ["universal-search", "search", "camino", "--database", str(database)]
    )
    main()
    stream.flush()

    printed = stream.buffer.getvalue().decode("ascii")
    assert "flecha.md" in printed
    assert "?" in printed


def test_cli_context_lifecycle(tmp_path: Path, monkeypatch, capsys) -> None:
    from universal_search.appconfig import AppConfig, AppPaths

    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(tmp_path / "home"))
    uni_root = tmp_path / "uni"

    run_cli(
        monkeypatch, "context", "add", "Universidad",
        "--root", uni_root, "--subject", "Álgebra",
        "--type", "pdf", "--recency-days", "30",
    )
    main()
    assert "Universidad" in capsys.readouterr().out

    config = AppConfig.load(AppPaths.discover())
    assert str(uni_root) in config.roots  # context roots are indexed too
    assert config.contexts[0]["subjects"] == ["álgebra"]
    assert config.contexts[0]["recency_days"] == 30

    run_cli(monkeypatch, "context", "list")
    main()
    listing = capsys.readouterr().out
    assert "Universidad" in listing and "1 raíz(es)" in listing

    run_cli(monkeypatch, "context", "use", "Universidad")
    main()
    assert "contexto activo: Universidad" in capsys.readouterr().out
    assert AppConfig.load(AppPaths.discover()).active_context == "Universidad"

    run_cli(
        monkeypatch, "context", "relate", "Universidad",
        "fourier", "transformada", "serie",
    )
    main()
    assert "transformada" in capsys.readouterr().out
    config = AppConfig.load(AppPaths.discover())
    related = config.contexts[0]["related_terms"]["fourier"]
    assert related == ["transformada", "serie"]

    run_cli(monkeypatch, "context", "use", "none")
    main()
    assert AppConfig.load(AppPaths.discover()).active_context == ""

    run_cli(monkeypatch, "context", "remove", "Universidad")
    main()
    assert "eliminado" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, "context", "use", "Fantasma")
        main()
    assert exit_info.value.code == 1
    assert "desconocido" in capsys.readouterr().err


def test_cli_usage_learning_commands(tmp_path: Path, monkeypatch, capsys) -> None:
    from universal_search.appconfig import AppConfig, AppPaths
    from universal_search.index.database import SearchDatabase
    from universal_search.index.search import SearchEngine

    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(tmp_path / "home"))
    database = tmp_path / "usage.db"

    run_cli(monkeypatch, "usage", "show", "--database", database)
    main()
    assert "sin eventos" in capsys.readouterr().out

    run_cli(monkeypatch, "usage", "on")
    main()
    assert "activado" in capsys.readouterr().out
    assert AppConfig.load(AppPaths.discover()).usage_tracking is True

    # record one real signal against an indexed document
    files = tmp_path / "files"
    files.mkdir()
    (files / "amplificador.md").write_text("etapa de amplificacion clase A", encoding="utf-8")
    run_cli(monkeypatch, "index", files, "--database", database)
    main()
    capsys.readouterr()
    handle = SearchDatabase(database)
    with handle.connect() as connection:
        document_id = connection.execute("SELECT id FROM documents").fetchone()[0]
    SearchEngine(handle).record_open(document_id, "amplificador")

    run_cli(monkeypatch, "usage", "show", "--database", database)
    main()
    shown = capsys.readouterr().out
    assert "amplificador" in shown and "«amplificador»" in shown

    run_cli(monkeypatch, "usage", "clear", "--database", database)
    main()
    assert "1 evento(s)" in capsys.readouterr().out

    run_cli(monkeypatch, "usage", "off")
    main()
    assert AppConfig.load(AppPaths.discover()).usage_tracking is False


def test_cli_search_with_context_and_explain(tmp_path: Path, monkeypatch, capsys) -> None:
    from universal_search.appconfig import AppConfig, AppPaths

    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(tmp_path / "home"))
    files = tmp_path / "uni"
    files.mkdir()
    (files / "fourier.md").write_text("series de fourier para señales", encoding="utf-8")
    database = tmp_path / "search.db"
    run_cli(monkeypatch, "index", files, "--database", database)
    main()
    capsys.readouterr()

    run_cli(monkeypatch, "context", "add", "Universidad", "--root", files)
    main()
    capsys.readouterr()

    run_cli(monkeypatch, "search", "fourier", "--database", database, "--context", "Universidad")
    main()
    assert "[local] fourier.md" in capsys.readouterr().out

    run_cli(monkeypatch, "search", "fourier", "--database", database, "--explain")
    main()
    explained = capsys.readouterr().out
    assert "score=" in explained and "filename_exact" in explained

    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, "search", "fourier", "--database", database, "--context", "Zombie")
        main()
    assert exit_info.value.code == 1
    assert "desconocido" in capsys.readouterr().err


def test_cli_hotkey_and_recent_commands(tmp_path: Path, monkeypatch, capsys) -> None:
    from universal_search.appconfig import AppConfig, AppPaths

    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(tmp_path / "home"))

    run_cli(monkeypatch, "hotkey", "show")
    main()
    shown = capsys.readouterr().out
    assert "ctrl+alt+s" in shown and "(activado)" in shown

    run_cli(monkeypatch, "hotkey", "set", "win+f2")
    main()
    assert "win+f2" in capsys.readouterr().out
    assert AppConfig.load(AppPaths.discover()).hotkey == "win+f2"

    # an unusable shortcut exits 1, explains itself and changes nothing
    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, "hotkey", "set", "banana+s")
        main()
    assert exit_info.value.code == 1
    assert "atajo inválido" in capsys.readouterr().err
    assert AppConfig.load(AppPaths.discover()).hotkey == "win+f2"

    run_cli(monkeypatch, "hotkey", "off")
    main()
    assert AppConfig.load(AppPaths.discover()).hotkey_enabled is False

    run_cli(monkeypatch, "recent", "show")
    main()
    assert "sin búsquedas recientes" in capsys.readouterr().out

    paths = AppPaths.discover()
    AppConfig(recent_queries=("fourier", "memoria final")).save(paths)
    run_cli(monkeypatch, "recent", "show")
    main()
    listing = capsys.readouterr().out
    assert "(recientes activadas)" in listing
    assert "fourier" in listing and "memoria final" in listing

    run_cli(monkeypatch, "recent", "clear")
    main()
    assert "borradas" in capsys.readouterr().out
    assert AppConfig.load(paths).recent_queries == ()

    run_cli(monkeypatch, "recent", "off")
    main()
    assert AppConfig.load(paths).recent_queries_enabled is False
    run_cli(monkeypatch, "recent", "on")
    main()
    assert AppConfig.load(paths).recent_queries_enabled is True


def test_cli_search_filters(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(tmp_path / "home"))
    files = tmp_path / "files"
    files.mkdir()
    (files / "termo.md").write_text(
        "practica de termo aplicada", encoding="utf-8"
    )
    (files / "amplificador.txt").write_text(
        "practica de amplificador clase A", encoding="utf-8"
    )
    database = tmp_path / "filters.db"
    run_cli(monkeypatch, "index", files, "--database", database)
    main()
    capsys.readouterr()

    run_cli(monkeypatch, "search", "practica", "--database", database)
    main()
    plain = capsys.readouterr().out
    assert "termo.md" in plain and "amplificador.txt" in plain

    run_cli(
        monkeypatch, "search", "practica", "--database", database,
        "--type", "txt",
    )
    main()
    only_txt = capsys.readouterr().out
    assert "amplificador.txt" in only_txt
    assert "termo.md" not in only_txt

    run_cli(
        monkeypatch, "search", "practica", "--database", database,
        "--source", "local",
    )
    main()
    assert "termo.md" in capsys.readouterr().out

    run_cli(
        monkeypatch, "search", "practica", "--database", database,
        "--source", "onedrive",
    )
    main()
    assert capsys.readouterr().out.strip() == ""  # no such source in the index

    # an unknown source is rejected by the argument parser
    with pytest.raises(SystemExit) as exit_info:
        run_cli(
            monkeypatch, "search", "practica", "--database", database,
            "--source", "s3",
        )
        main()
    assert exit_info.value.code == 2
