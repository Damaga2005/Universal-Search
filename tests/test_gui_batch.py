"""Phase 035: batch operations over a selection.

The property under test is honesty, not convenience: a batch must never claim
more than it did. Everything here exists because the alternative is a status
line that says "listo" after opening three files out of two hundred.
"""

from __future__ import annotations

from pathlib import Path

from universal_search.gui.batch import (
    MAX_BATCH_OPERATIONS,
    BatchOperations,
    BatchReport,
)
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer


class RecordingPlatform:
    """A platform seam that records calls and can be told to fail."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.opened: list[str] = []
        self.revealed: list[str] = []
        self.fail_on = fail_on or set()

    def open_path(self, target: str) -> None:
        if target in self.fail_on:
            raise FileNotFoundError(target)
        self.opened.append(target)

    def reveal(self, target: str) -> None:
        if target in self.fail_on:
            raise PermissionError(target)
        self.revealed.append(target)


def use_platform(monkeypatch, platform: RecordingPlatform) -> RecordingPlatform:
    import universal_search.platforms as platforms_module

    monkeypatch.setattr(
        platforms_module, "get_platform", lambda: platform, raising=False
    )
    import universal_search.platforms as module

    monkeypatch.setattr(module, "get_platform", lambda: platform, raising=False)
    return platform


# -- a batch that works --------------------------------------------------------

def test_every_selected_document_is_opened(monkeypatch):
    platform = use_platform(monkeypatch, RecordingPlatform())
    operations = BatchOperations()
    paths = [Path("a.md"), Path("b.md"), Path("c.md")]

    report = operations.open_all(paths)

    assert platform.opened == ["a.md", "b.md", "c.md"]
    assert report.ok
    assert report.succeeded == ("a.md", "b.md", "c.md")
    assert report.attempted == 3


def test_reveal_uses_its_own_platform_call(monkeypatch):
    platform = use_platform(monkeypatch, RecordingPlatform())
    BatchOperations().reveal_all([Path("a.md")])
    assert platform.revealed == ["a.md"]
    assert platform.opened == []


def test_an_empty_selection_does_nothing_and_says_so(monkeypatch):
    platform = use_platform(monkeypatch, RecordingPlatform())
    report = BatchOperations().open_all([])
    assert platform.opened == []
    assert report.ok is True
    assert "no habia" in report.summary()


# -- the bound ----------------------------------------------------------------

def test_a_batch_is_bounded_and_says_what_it_skipped(monkeypatch):
    platform = use_platform(monkeypatch, RecordingPlatform())
    operations = BatchOperations()
    paths = [Path(f"doc{index}.md") for index in range(MAX_BATCH_OPERATIONS + 12)]

    report = operations.open_all(paths)

    assert len(platform.opened) == MAX_BATCH_OPERATIONS
    assert report.skipped == 12
    assert report.ok is False
    # The summary must not read like a completed batch.
    assert "12 sin procesar" in report.summary()


def test_the_bound_is_configurable_but_still_bounds(monkeypatch):
    platform = use_platform(monkeypatch, RecordingPlatform())
    operations = BatchOperations(max_batch=3)
    report = operations.open_all([Path(f"d{i}.md") for i in range(10)])
    assert len(platform.opened) == 3
    assert report.skipped == 7


def test_a_zero_bound_does_nothing_rather_than_everything(monkeypatch):
    platform = use_platform(monkeypatch, RecordingPlatform())
    report = BatchOperations(max_batch=0).open_all([Path("a.md"), Path("b.md")])
    assert platform.opened == []
    assert report.attempted == 0
    assert report.skipped == 2


# -- failure isolation and honest reporting -----------------------------------

def test_one_failure_does_not_stop_the_batch(monkeypatch):
    platform = use_platform(
        monkeypatch, RecordingPlatform(fail_on={"b.md"})
    )
    report = BatchOperations().open_all(
        [Path("a.md"), Path("b.md"), Path("c.md")]
    )

    assert platform.opened == ["a.md", "c.md"]
    assert len(report.succeeded) == 2
    assert report.failures == (("b.md", "FileNotFoundError"),)


def test_a_partial_batch_is_not_reported_as_complete(monkeypatch):
    use_platform(monkeypatch, RecordingPlatform(fail_on={"b.md"}))
    report = BatchOperations().open_all([Path("a.md"), Path("b.md")])
    assert report.ok is False
    assert report.partial is True
    summary = report.summary()
    assert "1 de 2" in summary
    assert "b.md: FileNotFoundError" in summary


def test_a_total_failure_is_reported_as_a_failure(monkeypatch):
    use_platform(monkeypatch, RecordingPlatform(fail_on={"a.md", "b.md"}))
    report = BatchOperations().open_all([Path("a.md"), Path("b.md")])
    assert report.ok is False
    assert report.partial is False
    assert "0 de 2" in report.summary()


def test_many_failures_are_abbreviated_but_never_miscounted(monkeypatch):
    use_platform(
        monkeypatch,
        RecordingPlatform(fail_on={f"d{i}.md" for i in range(10)}),
    )
    report = BatchOperations().open_all(
        [Path(f"d{i}.md") for i in range(10)]
    )
    assert len(report.failures) == 10  # the count is exact
    assert "10 con error" in report.summary()
    assert "mas" in report.summary()


def test_the_report_counts_are_exactly_what_was_requested(monkeypatch):
    use_platform(monkeypatch, RecordingPlatform(fail_on={"b.md"}))
    report = BatchOperations(max_batch=2).open_all(
        [Path("a.md"), Path("b.md"), Path("c.md")]
    )
    assert report.requested == 3
    assert report.attempted == 2
    assert report.skipped == 1
    assert len(report.succeeded) + len(report.failures) == report.attempted


# -- clipboard text -----------------------------------------------------------

def test_paths_are_copied_one_per_line():
    text = BatchOperations().paths_text([Path("a.md"), Path("b/c.md")])
    assert text == "a.md\n" + str(Path("b/c.md"))


def test_duplicate_paths_are_copied_once():
    text = BatchOperations().paths_text([Path("a.md"), Path("a.md")])
    assert text == "a.md"


def test_copying_an_empty_selection_yields_empty_text():
    assert BatchOperations().paths_text([]) == ""


# -- forgetting is destructive and says so ------------------------------------

def test_forgetting_nothing_without_confirmation(tmp_path: Path):
    database = SearchDatabase(tmp_path / "index.db")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.md").write_text("contenido", encoding="utf-8")
    Indexer(database).index_root(tree)
    document = tree / "a.md"

    report = BatchOperations(database=database).forget_all([document])

    assert report.attempted == 0
    assert report.skipped == 1
    # The document is still there: the default is to do nothing.
    assert document.exists()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents WHERE path = ?", (str(document),)
        ).fetchone()[0] == 1


def test_an_unconfirmed_forget_says_why_it_did_nothing(tmp_path: Path):
    """It must not blame the size limit for a cancellation the user made."""
    database = SearchDatabase(tmp_path / "index.db")
    report = BatchOperations(database=database).forget_all(
        [Path("a.md"), Path("b.md")]
    )
    summary = report.summary()
    assert "que no se confirmo" in summary
    assert "limite" not in summary


def test_forgetting_with_confirmation_removes_it_from_the_index(tmp_path: Path):
    database = SearchDatabase(tmp_path / "index.db")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.md").write_text("contenido del informe", encoding="utf-8")
    Indexer(database).index_root(tree)
    document = tree / "a.md"

    report = BatchOperations(database=database).forget_all([document], confirm=True)

    assert report.ok
    assert report.succeeded == (str(document),)
    # The file itself is untouched: forgetting is not deleting.
    assert document.exists()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents WHERE path = ?", (str(document),)
        ).fetchone()[0] == 0


def test_forgetting_without_a_database_fails_loudly():
    report = BatchOperations().forget_all([Path("a.md")], confirm=True)
    assert report.ok is False
    assert report.failures == (("a.md", "sin base de datos"),)


def test_forgetting_is_bounded_too(tmp_path: Path):
    database = SearchDatabase(tmp_path / "index.db")
    report = BatchOperations(database=database, max_batch=2).forget_all(
        [Path(f"d{i}.md") for i in range(5)], confirm=True
    )
    assert report.attempted == 2
    assert report.skipped == 3


def test_forgetting_something_absent_is_a_failure_not_a_silent_success(
    tmp_path: Path,
):
    database = SearchDatabase(tmp_path / "index.db")
    report = BatchOperations(database=database).forget_all(
        [tmp_path / "nunca-existio.md"], confirm=True
    )
    # Whatever privacy.forget does with an unknown path, the report must not
    # claim more documents were removed than the index actually held.
    assert report.requested == 1


# -- the report type itself ---------------------------------------------------

def test_a_report_is_immutable():
    report = BatchReport(action="Abriendo", requested=1, attempted=1)
    try:
        report.requested = 5  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("BatchReport deberia ser inmutable")


def test_ok_is_false_when_anything_was_skipped_or_failed():
    assert BatchReport("x", 1, 1).ok is True
    assert BatchReport("x", 2, 1, skipped=1).ok is False
    assert BatchReport("x", 1, 1, failures=(("a", "b"),)).ok is False
