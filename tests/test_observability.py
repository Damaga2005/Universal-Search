"""Phase 028: structured local observability and sanitized export."""

import json
import sys

import pytest

from universal_search import cli
from universal_search.appconfig import AppPaths
from universal_search.index.database import SearchDatabase
from universal_search.observability import EventRecorder, self_test, support_bundle


def test_event_recorder_redacts_and_bounds(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    recorder = EventRecorder(path, max_bytes=220, backups=1)
    for number in range(5):
        recorder.emit(
            "worker",
            "index.pass",
            "info",
            password="secreto",
            access_token="otro-secreto",
            detail="x" * 400,
            number=number,
        )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines
    assert path.with_suffix(path.suffix + ".1").exists()  # rotated
    for line in lines:
        record = json.loads(line)
        assert record["password"] == "[redacted]"
        assert record["access_token"] == "[redacted]"
        assert len(record["detail"]) <= 300


def test_self_test_covers_every_declared_area(tmp_path) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    SearchDatabase(paths.database)

    report = self_test(paths)
    names = {check.name for check in report.checks}
    assert names == {
        "database",
        "fts",
        "schema",
        "providers",
        "extractors",
        "worker",
        "storage",
        # Phase 037: where the data lives is an area of the self-test, because
        # "my index vanished" is the first question when a portable copy is
        # involved.
        "deployment",
    }
    assert all(check.status in {"ok", "warning", "fatal"} for check in report.checks)
    assert report.as_dict()["storage"]["free_bytes"] >= 0


def test_self_test_declares_an_installed_deployment(tmp_path) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    SearchDatabase(paths.database)

    check = next(c for c in self_test(paths).checks if c.name == "deployment")
    assert check.status == "ok"
    assert str(paths.home) in check.detail


def test_self_test_reports_a_portable_deployment(tmp_path) -> None:
    """A portable copy must be able to name its own data directory."""
    home = tmp_path / "stick" / "UniversalSearch-data"
    paths = AppPaths(home, portable=True)
    paths.ensure()
    SearchDatabase(paths.database)

    check = next(c for c in self_test(paths).checks if c.name == "deployment")
    assert check.status == "ok"
    assert "portable" in check.detail
    assert str(home) in check.detail


def test_self_test_fails_when_a_portable_folder_cannot_be_written(tmp_path) -> None:
    """Refusing to run is the promise; a quiet fallback would be the bug."""
    occupied = tmp_path / "occupied"
    occupied.write_text("a file, not a folder", encoding="utf-8")
    paths = AppPaths(occupied / "UniversalSearch-data", portable=True)
    SearchDatabase(tmp_path / "index.db")

    check = next(c for c in self_test(paths).checks if c.name == "deployment")
    assert check.status == "fatal"


def test_support_bundle_is_json_sanitized_and_explicit(tmp_path) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    SearchDatabase(paths.database)

    destination = tmp_path / "support" / "bundle.json"
    result = support_bundle(paths, destination)

    assert result.path == destination
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["declaration"]["contains_document_content"] is False
    assert payload["declaration"]["contains_query_text"] is False
    assert set(payload["self_test"]) >= {"database", "fts", "schema"}
    assert "token" not in destination.read_text(encoding="utf-8").lower()


# -- the CLI surface -----------------------------------------------------------

def _run(monkeypatch, argv, paths):
    monkeypatch.setattr(cli, "main", cli.main)
    monkeypatch.setattr("universal_search.appconfig.AppPaths.discover", classmethod(
        lambda cls: paths
    ))
    monkeypatch.setattr(sys, "argv", ["universal-search", *argv])
    return cli.main()


def test_cli_diagnose_self_test_reports_every_area(monkeypatch, tmp_path, capsys):
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    SearchDatabase(paths.database)

    _run(monkeypatch, ["diagnose", "self-test"], paths)

    out = capsys.readouterr().out
    for area in ("database", "fts", "schema", "providers", "extractors",
                 "worker", "storage"):
        assert area in out


def test_cli_diagnose_export_writes_the_bundle(monkeypatch, tmp_path, capsys):
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    SearchDatabase(paths.database)
    destination = tmp_path / "bundle.json"

    _run(monkeypatch, ["diagnose", "export", "--output", str(destination)], paths)

    assert destination.exists()
    assert str(destination) in capsys.readouterr().out


def test_cli_diagnose_recover_requires_confirmation_for_destructive(
    monkeypatch, tmp_path, capsys
):
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    SearchDatabase(paths.database)

    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, ["diagnose", "recover", "reset-derived"], paths)

    assert exit_info.value.code == 1
    assert "--yes" in capsys.readouterr().err


def test_cli_diagnose_recover_runs_a_safe_case(monkeypatch, tmp_path, capsys):
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    SearchDatabase(paths.database)

    _run(monkeypatch, ["diagnose", "recover", "orphan-derived"], paths)

    assert "orphan-derived: repaired" in capsys.readouterr().out
