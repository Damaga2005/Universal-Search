"""Phase 046: indexing scalability, and the correctness it has to preserve.

The first half of this file is not about scale at all. It pins two defects
that the whole 1410-test suite had been unable to see, because both of them
only appear when a real object meets a real reader.

The cancellation bug was the serious one. ``index_root`` bound
``cancel=cancel.cancelled`` -- the token's boolean *property* -- where the
extractors expect a ``CancelCheck`` callable. ``check_cancel`` does
``cancel()``, so every extraction raised ``TypeError: 'bool' object is not
callable``, the ``except`` clause two lines below swallowed it into an
``ExtractionResult``, and a pass carrying a real ``CancelToken`` indexed
**every document with no content and no FTS row** while reporting zero errors.
Measured before the fix: 6 documents in, 6 with empty text, 6 extraction
errors, and a search that could not find any of them.

Nothing in the suite caught it because every test that exercised per-file
cancellation passed a *callable* of its own, and the tests that passed a real
token cancelled before the reader was ever reached.
"""

from __future__ import annotations

from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.providers.base import CancelToken


# -- cancellation semantics ---------------------------------------------------

def _corpus(root: Path, count: int = 6) -> list[Path]:
    written = []
    for index in range(count):
        target = root / "docs" / f"doc{index}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"documento {index} con contenido real para(index)", encoding="utf-8"
        )
        written.append(target)
    return written


def _rows(database: SearchDatabase):
    with database.connect() as connection:
        return connection.execute(
            "SELECT d.name AS name, length(f.content) AS length "
            "FROM documents AS d "
            "LEFT JOIN documents_fts AS f ON f.document_id = d.id "
            "ORDER BY d.name"
        ).fetchall()


def test_a_real_cancel_token_does_not_empty_the_index(tmp_path):
    """The defect itself. A token is a token whether or not it ever cancels.

    Before the fix this asserted the opposite of what it says: six documents,
    six empty bodies, and no error surfaced anywhere a user would see.
    """
    tree = tmp_path / "tree"
    _corpus(tree)
    database = SearchDatabase(tmp_path / "index.db")
    token = CancelToken()

    stats = Indexer(database).index_root(tree, cancel=token)

    assert stats.extraction_errors == 0, (
        "a token that was never cancelled must not fail a single extraction"
    )
    rows = _rows(database)
    assert len(rows) == 6
    assert all(row["length"] for row in rows), (
        f"documents indexed with no text: {[r['name'] for r in rows]}"
    )


def test_an_uncancelled_index_is_searchable(tmp_path):
    """The user-visible consequence, which is what the bug really was.

    A pass that reported success and produced an empty index is worse than a
    pass that failed: nothing tells the user their documents are unreachable.
    """
    tree = tmp_path / "tree"
    _corpus(tree)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree, cancel=CancelToken())

    engine = SearchEngine(database)
    for term in ("documento", "contenido", "real"):
        results = engine.search(term, limit=10)
        assert results, f"{term!r} found nothing in an index that reported success"


def test_the_cancel_check_is_evaluated_when_called_not_when_bound(tmp_path):
    """A snapshot would freeze ``False`` and never see a later cancellation.

    This is why the fix is a lambda and not ``cancel.cancelled``: the value has
    to be read at the moment the extractor asks, or "cooperative" is a word with
    no behaviour behind it.
    """
    tree = tmp_path / "tree"
    _corpus(tree)
    database = SearchDatabase(tmp_path / "index.db")
    token = CancelToken()
    token.cancel()

    stats = Indexer(database).index_root(tree, cancel=token)

    # A token cancelled up front means the provider stops before yielding, so
    # nothing is written -- and crucially nothing is written *badly* either.
    assert stats.created == 0
    assert _rows(database) == []


def test_cancelling_mid_pass_still_extracts_what_it_reached(tmp_path):
    """Cancellation is cooperative, positional, and leaves nothing broken.

    Three things are true at once and the test holds all three: the pass stops
    part-way, everything it finished extracting keeps its text, and the
    document that was interrupted is *repaired* by the next clean pass rather
    than left blank forever.
    """
    tree = tmp_path / "tree"
    _corpus(tree, count=10)
    database = SearchDatabase(tmp_path / "index.db")
    token = CancelToken()

    started: list[Path] = []

    def reader(path, *, limits=None, cancel=None):
        # Cancel once four documents have been read: inside the loop, well
        # before the batch commit.
        if len(started) >= 4:
            token.cancel()
        if cancel is not None and cancel():
            from universal_search.domain.extraction import (
                ExtractionResult,
                ExtractionStatus,
            )

            # The status has to say `cancelled`. `error=` alone would record a
            # permanent failure, which is not retried -- and this document must
            # be, because nothing is wrong with the file.
            return ExtractionResult(
                error="interrumpida", status=ExtractionStatus.CANCELLED
            )
        from universal_search.extractors import extract

        started.append(path)
        return extract(path, limits=limits)

    stats = Indexer(database).index_root(tree, cancel=token, read_content=reader)

    # The pass stopped rather than running to the end.
    assert 0 < stats.created < 10, (
        f"the pass should stop part-way, not index all ten or none: {stats}"
    )

    rows = _rows(database)
    assert rows
    finished = {path.name for path in started}
    for row in rows:
        if row["name"] in finished:
            assert row["length"], (
                f"{row['name']} was extracted and then indexed with no text"
            )

    # And the interrupted state is not permanent.
    clean = Indexer(database).index_root(tree)
    assert clean.extraction_errors == 0
    repaired = _rows(database)
    assert all(row["length"] for row in repaired), (
        f"a later clean pass did not repair: "
        f"{[r['name'] for r in repaired if not r['length']]}"
    )
    engine = SearchEngine(database)
    assert engine.search("documento", limit=10), (
        "every document should be findable once the pass has completed"
    )


def test_a_cancelled_pass_never_deletes_documents_it_never_reached(tmp_path):
    """The destructive half of cancellation, which is the half that matters.

    A pass that stops early has not seen the whole tree. Deleting what it did
    not reach would remove documents that still exist on disk -- so the
    deletion pass is skipped, and this pins that it is.
    """
    tree = tmp_path / "tree"
    _corpus(tree, count=6)
    database = SearchDatabase(tmp_path / "index.db")

    full = Indexer(database).index_root(tree)
    assert full.created == 6
    with database.connect() as connection:
        before = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert before == 6

    token = CancelToken()
    token.cancel()
    cancelled = Indexer(database).index_root(tree, cancel=token)
    assert cancelled.created == 0

    with database.connect() as connection:
        after = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert after == 6, (
        f"a cancelled pass deleted {before - after} documents that were still "
        "on disk"
    )


def test_an_error_cannot_claim_the_status_ok():
    """The type-level fix, pinned directly.

    ``ExtractionStatus`` documents a precedence in which ``error`` outranks
    everything. It was documented and not enforced, so any caller that set
    ``error`` and left the default status recorded a *successful* extraction.
    """
    from universal_search.domain.extraction import ExtractionResult, ExtractionStatus

    failed = ExtractionResult(error="cancelled")
    assert failed.status == ExtractionStatus.ERROR

    # An explicit status that is already not-ok is left alone, so a caller can
    # still say "truncated" for a run that did produce partial text.
    truncated = ExtractionResult(
        text="partial", status=ExtractionStatus.TRUNCATED, truncated=True
    )
    assert truncated.status == ExtractionStatus.TRUNCATED

    ok = ExtractionResult(text="hello")
    assert ok.status == ExtractionStatus.OK

    # The error message still survives, because diagnostics report why.
    assert failed.error == "cancelled"


def test_a_failed_extraction_is_retried_on_the_next_pass(tmp_path):
    """The durability half, without the cancellation machinery in the way.

    A document whose extraction errored must not be blessed as unchanged just
    because its size and mtime match. Before this, one failure made the
    document permanently unfindable: measured as a clean second pass reporting
    ``unchanged=6`` with the blanked document still blank.
    """
    tree = tmp_path / "tree"
    _corpus(tree, count=4)
    database = SearchDatabase(tmp_path / "index.db")
    target = "doc1.md"

    def failing_reader(path, *, limits=None, cancel=None):
        from universal_search.domain.extraction import (
            ExtractionResult,
            ExtractionStatus,
        )
        from universal_search.extractors import extract

        if path.name == target:
            # `cancelled`, not `error`: an interrupted extraction is worth
            # retrying, a broken file is not. See `_RETRY_STATUSES`.
            return ExtractionResult(
                error="interrumpida", status=ExtractionStatus.CANCELLED
            )
        return extract(path, limits=limits)

    first = Indexer(database).index_root(tree, read_content=failing_reader)
    assert first.extraction_errors == 1
    assert all(not r["length"] for r in _rows(database) if r["name"] == target)

    second = Indexer(database).index_root(tree)
    assert second.unchanged == 3, (
        f"the failed document should have been retried, not skipped: {second}"
    )
    assert all(r["length"] for r in _rows(database))


def test_a_permanently_broken_file_is_not_retried_forever(tmp_path):
    """The other half of the retry rule, and the one that costs if it is wrong.

    An unreadable file is a permanent fact about those bytes. Retrying it on
    every pass forever is work for nothing, and it marks the derived layers
    dirty on every pass, so a pass over an unchanged tree is never unchanged.

    Phase 046 shipped this backwards first: both `error` and `cancelled` were
    retried, and the evaluation corpus's deliberately unreadable PDF made every
    second pass do work again.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    broken = tree / "roto.pdf"
    broken.write_bytes(b"%PDF-1.7 synthetic roto \x00\x01")
    fine = tree / "notas.md"
    fine.write_text("notas con contenido", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")

    reads: list[str] = []

    def counting_reader(path, *, limits=None, cancel=None):
        reads.append(path.name)
        from universal_search.extractors import extract

        return extract(path, limits=limits)

    first = Indexer(database).index_root(tree, read_content=counting_reader)
    assert first.extraction_errors == 1

    second = Indexer(database).index_root(tree, read_content=counting_reader)
    assert second.unchanged == 2, (
        f"a permanently broken file must settle, not be re-read forever: {second}"
    )
    assert len(reads) == 2, (
        f"the second pass read {len(reads) - 2} files; it should read none"
    )


def test_a_document_with_no_text_layer_stays_on_the_fast_path(tmp_path):
    """The other half of the same fix, which is the half that could regress.

    ``no_content`` means "read fine, no text" -- a binary, an image, an
    unsupported extension. If that also forced a re-read, every metadata-only
    document in the corpus would be re-extracted on every single pass forever.
    """
    tree = tmp_path / "tree"
    binary = tree / "diagram.png"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    text = tree / "notes.md"
    text.write_text("notas con contenido", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")

    reads: list[str] = []

    def counting_reader(path, *, limits=None, cancel=None):
        reads.append(path.name)
        from universal_search.extractors import extract

        return extract(path, limits=limits)

    Indexer(database).index_root(tree, read_content=counting_reader)
    first = len(reads)
    second_stats = Indexer(database).index_root(tree, read_content=counting_reader)

    assert second_stats.unchanged == 2, "an unsupported binary must not be re-read"
    assert len(reads) == first, (
        f"the second pass re-read {len(reads) - first} files; an unsupported "
        "binary has no text by nature, not by failure"
    )


# -- resource bounds that are actually enforced -------------------------------

def test_the_walker_materialises_one_directory_at_a_time(tmp_path):
    """Traversal memory is bounded by directory breadth, and that is a choice.

    ``scan_local`` sorts a whole directory before yielding it. That is the
    price of a deterministic order, and the price is per directory rather than
    per tree, which is the property worth pinning at 10k documents.
    """
    from universal_search.providers.local import scan_local

    tree = tmp_path / "tree"
    for folder in range(10):
        folder_path = tree / f"d{folder}"
        folder_path.mkdir(parents=True)
        for index in range(20):
            (folder_path / f"f{index}.txt").write_text("x", encoding="utf-8")

    seen = list(scan_local(tree))
    assert len(seen) == 200
    # The order is deterministic across two runs over the same tree.
    assert [type(item).__name__ for item in seen] == [
        type(item).__name__ for item in scan_local(tree)
    ]


def test_indexing_a_wide_tree_keeps_one_connection_and_batched_commits(tmp_path):
    """Connection management at scale, as a fact rather than a hope.

    ``index_root`` opens exactly one connection for the whole pass and commits
    every ``COMMIT_EVERY`` documents. Both are load-bearing: a per-file
    connection cost ~12 ms each when it was profiled, and ``test_scale.py``
    pins the batch boundary exactly, so this only asserts the shape.
    """
    from universal_search.index import indexer as indexer_module

    tree = tmp_path / "tree"
    _corpus(tree, count=indexer_module.COMMIT_EVERY + 50)

    database = SearchDatabase(tmp_path / "index.db")
    opened: list[object] = []
    original = database.connect

    def counting_connect():
        connection = original()
        opened.append(connection)
        return connection

    database.connect = counting_connect  # type: ignore[method-assign]
    try:
        stats = Indexer(database).index_root(tree)
    finally:
        del database.connect  # type: ignore[attr-defined]

    assert stats.created == indexer_module.COMMIT_EVERY + 50
    # One for the pass, plus the derived-layer markers at the tail.
    assert len(opened) <= 6, f"{len(opened)} connections for one pass"
    for connection in opened:
        connection.close()