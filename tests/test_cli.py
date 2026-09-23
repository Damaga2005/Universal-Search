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
