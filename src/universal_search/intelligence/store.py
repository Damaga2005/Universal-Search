"""Persistence and queries for derived document intelligence (spec 014).

Contract with the rest of the application:

* **Search never reads this data.** The table is derived, disposable and
  rebuilt from the indexed content; deleting it costs nothing but the
  related-document feature.
* **Every row is versioned** (``INTELLIGENCE_VERSION``) and carries the
  ``content_hash`` it was derived from, so a rebuild recomputes exactly
  what changed — and everything, after a version bump.
* **Document similarity is not query relevance.** Similarity here is a
  cosine over two bounded term vectors, computed in this module, with no
  involvement from the ranking formula, the FTS pool or the query
  language. A document can be highly similar and score badly for a query;
  the two answers answer different questions.
"""

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.ranking import tokens
from universal_search.intelligence.analysis import (
    INTELLIGENCE_VERSION,
    DocumentAnalysis,
    analyze,
)

WRITE_BATCH = 200

UPSERT_SQL = """
INSERT INTO document_intelligence (
    document_id, version, language, title, headings, terms, pairs,
    sections, analyzed_chars, truncated, content_hash, analyzed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
ON CONFLICT(document_id) DO UPDATE SET
    version=excluded.version,
    language=excluded.language,
    title=excluded.title,
    headings=excluded.headings,
    terms=excluded.terms,
    pairs=excluded.pairs,
    sections=excluded.sections,
    analyzed_chars=excluded.analyzed_chars,
    truncated=excluded.truncated,
    content_hash=excluded.content_hash,
    analyzed_at=CURRENT_TIMESTAMP
"""

STORED_COLUMNS = (
    "document_id, name, path, content, content_hash, id AS document_id"
)


@dataclass(frozen=True, slots=True)
class RebuildStats:
    """What one rebuild pass did (printed by the CLI, asserted by tests)."""

    scanned: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    removed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "removed": self.removed,
        }


@dataclass(frozen=True, slots=True)
class RelatedDocument:
    """One neighbour of a document, with why it is a neighbour."""

    document_id: str
    name: str
    path: str
    score: float
    shared_terms: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "name": self.name,
            "path": self.path,
            "score": round(self.score, 6),
            "shared_terms": list(self.shared_terms),
        }


# -- serialization -------------------------------------------------------------

def _dump(value: object) -> str:
    """Deterministic JSON: sorted keys, no ASCII escaping, compact."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None) -> object:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:  # pragma: no cover - defensive: corrupted row
        return None


def row_to_analysis(row: sqlite3.Row) -> DocumentAnalysis:
    """Rebuild a :class:`DocumentAnalysis` from a stored row."""
    headings = _load(row["headings"]) or []
    terms = _load(row["terms"]) or []
    pairs = _load(row["pairs"]) or []
    return DocumentAnalysis(
        version=int(row["version"]),
        language=row["language"],
        title=row["title"],
        headings=tuple(str(item) for item in headings),
        sections=int(row["sections"]),
        terms=tuple((str(pair[0]), int(pair[1])) for pair in terms),
        pairs=tuple((str(pair[0]), str(pair[1])) for pair in pairs),
        analyzed_chars=int(row["analyzed_chars"]),
        truncated=bool(row["truncated"]),
    )


def analysis_params(
    document_id: str, analysis: DocumentAnalysis, content_hash: str | None
) -> tuple:
    """Bind values for :data:`UPSERT_SQL`, in its column order."""
    return (
        document_id,
        analysis.version,
        analysis.language,
        analysis.title,
        _dump(list(analysis.headings)),
        _dump([[term, count] for term, count in analysis.terms]),
        _dump([list(pair) for pair in analysis.pairs]),
        analysis.sections,
        analysis.analyzed_chars,
        1 if analysis.truncated else 0,
        content_hash,
    )


# -- rebuild -------------------------------------------------------------------

def _stored_state(
    connection: sqlite3.Connection,
) -> dict[str, tuple[int, str | None]]:
    """document_id -> (version, content_hash) for everything analysed."""
    return {
        row["document_id"]: (int(row["version"]), row["content_hash"])
        for row in connection.execute(
            "SELECT document_id, version, content_hash FROM document_intelligence"
        )
    }


def _remove_orphans(
    connection: sqlite3.Connection, live_ids: set[str]
) -> int:
    """Drop analyses whose document no longer exists (deleted on disk)."""
    stale = [
        row["document_id"]
        for row in connection.execute(
            "SELECT document_id FROM document_intelligence"
        )
        if row["document_id"] not in live_ids
    ]
    connection.executemany(
        "DELETE FROM document_intelligence WHERE document_id = ?",
        [(document_id,) for document_id in stale],
    )
    return len(stale)


def rebuild(
    database: SearchDatabase,
    *,
    limit: int | None = None,
    force: bool = False,
) -> RebuildStats:
    """(Re)analyse indexed documents, skipping unchanged ones.

    ``force`` recomputes everything (used after a version bump or to
    recover from corrupted rows). Otherwise a document is recomputed only
    when its stored version is outdated or its content changed. Rows whose
    document disappeared are deleted, so the derived data can never outlive
    the index.
    """
    scanned = updated = skipped = failed = 0
    with database.connect() as connection:
        stored = {} if force else _stored_state(connection)
        pending: list[tuple] = []
        live_ids: set[str] = set()
        rows = connection.execute(
            """
            SELECT d.id AS document_id, d.name AS name, d.path AS path,
                   d.content_hash AS content_hash,
                   f.content AS content
            FROM documents AS d
            LEFT JOIN documents_fts AS f ON f.document_id = d.id
            ORDER BY d.id
            """
        )
        for row in rows:
            if limit is not None and scanned >= limit:
                break
            scanned += 1
            document_id = row["document_id"]
            live_ids.add(document_id)
            content_hash = row["content_hash"]
            known = stored.get(document_id)
            if (
                known is not None
                and known[0] == INTELLIGENCE_VERSION
                and known[1] == content_hash
            ):
                skipped += 1
                continue
            try:
                analysis = analyze(row["content"], name=row["name"] or "")
            except Exception:  # pragma: no cover - defensive: one bad
                # document must never abort a rebuild of a thousand good
                # ones; it is reported and left for the next pass.
                failed += 1
                continue
            pending.append(analysis_params(document_id, analysis, content_hash))
            if len(pending) == WRITE_BATCH:
                connection.executemany(UPSERT_SQL, pending)
                updated += len(pending)
                pending.clear()
        if pending:
            connection.executemany(UPSERT_SQL, pending)
            updated += len(pending)
        removed = _remove_orphans(connection, live_ids)
        connection.commit()
    return RebuildStats(
        scanned=scanned, updated=updated, skipped=skipped,
        failed=failed, removed=removed,
    )


def clear(database: SearchDatabase) -> int:
    """Delete every derived row. The index is untouched and still complete."""
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM document_intelligence"
        ).fetchone()[0]
        connection.execute("DELETE FROM document_intelligence")
        connection.commit()
    return int(count)


# -- single document -----------------------------------------------------------

def _resolve(
    connection: sqlite3.Connection, reference: str
) -> sqlite3.Row | None:
    """Find the analysis row for a path, a bare file name or a document id.

    A person types a path they can see; tooling passes an id. A bare name
    is accepted too, because that is what people paste from a listing.
    """
    candidate = Path(reference)
    if candidate.exists():
        row = connection.execute(
            """
            SELECT i.* FROM document_intelligence AS i
            JOIN documents AS d ON d.id = i.document_id
            WHERE d.path = ? ORDER BY (d.path = ?) DESC LIMIT 1
            """,
            (str(candidate), str(candidate)),
        ).fetchone()
        if row is not None:
            return row
    row = connection.execute(
        """
        SELECT i.* FROM document_intelligence AS i
        JOIN documents AS d ON d.id = i.document_id
        WHERE d.name = ? ORDER BY d.path LIMIT 1
        """,
        (candidate.name,),
    ).fetchone()
    if row is not None:
        return row
    return connection.execute(
        "SELECT * FROM document_intelligence WHERE document_id = ?",
        (reference,),
    ).fetchone()


def analysis_for(
    database: SearchDatabase, reference: str
) -> DocumentAnalysis | None:
    """Stored analysis of a document, addressed by path, name or id."""
    with database.connect() as connection:
        row = _resolve(connection, reference)
        return row_to_analysis(row) if row is not None else None


# -- related documents ---------------------------------------------------------

def _cosine(
    left: dict[str, int], right: dict[str, int]
) -> tuple[float, list[str]]:
    """Cosine similarity of two term vectors, plus the shared terms."""
    if not left or not right:
        return 0.0, []
    shared = sorted(set(left) & set(right))
    if not shared:
        return 0.0, []
    dot = sum(left[term] * right[term] for term in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0.0 or right_norm == 0.0:  # pragma: no cover - defensive
        return 0.0, shared
    return dot / (left_norm * right_norm), shared


def _terms_dict(analysis: DocumentAnalysis) -> dict[str, int]:
    return dict(analysis.terms)


def related(
    database: SearchDatabase,
    reference: str,
    *,
    limit: int = 10,
) -> list[RelatedDocument]:
    """Documents sharing concepts with ``reference``, closest first.

    A pure lexical question, answered over the bounded term vectors: no
    query parsing, no ranking weights, no FTS pool. Ties break by ascending
    path so the list is reproducible.
    """
    with database.connect() as connection:
        source = _resolve(connection, reference)
        if source is None:
            return []
        source_id = source["document_id"]
        origin = _terms_dict(row_to_analysis(source))
        if not origin:
            return []
        others = connection.execute(
            """
            SELECT i.document_id AS document_id, i.terms AS terms,
                   d.name AS name, d.path AS path
            FROM document_intelligence AS i
            JOIN documents AS d ON d.id = i.document_id
            WHERE i.document_id <> ? AND i.terms <> '[]'
            """,
            (source_id,),
        ).fetchall()

    neighbours: list[RelatedDocument] = []
    for row in others:
        pairs = _load(row["terms"]) or []
        vector = {str(pair[0]): int(pair[1]) for pair in pairs}
        score, shared = _cosine(origin, vector)
        if score <= 0.0:
            continue
        neighbours.append(
            RelatedDocument(
                document_id=row["document_id"],
                name=row["name"],
                path=row["path"],
                score=score,
                shared_terms=tuple(shared),
            )
        )
    neighbours.sort(key=lambda item: (-item.score, item.path))
    return neighbours[: max(limit, 0)]


def term_vocabulary(text: str) -> list[str]:
    """Top terms of a free-text query, for comparing with keywords.

    Convenience for the CLI: it answers "which of this document's
    neighbours share concepts with *these words*" without pretending to be
    a search query.
    """
    from universal_search.intelligence.keywords import meaningful_terms

    return [term for term, _ in meaningful_terms(tokens(text.casefold()))]
