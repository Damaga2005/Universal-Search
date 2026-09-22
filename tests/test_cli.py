import io
import sys
from pathlib import Path

from universal_search.cli import main


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
