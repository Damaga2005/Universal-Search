"""Bounded, deterministic related-document graph (phase 022).

The graph is derived data beside :mod:`intelligence`.  It never participates
in query translation or ranking.  Canonical documents are the only input;
SQLite rows can be deleted and rebuilt without changing search results.

Candidate generation is deliberately inverted and bounded.  A source looks
up postings for its terms, phrases, title/heading terms, directory and safe
references, then compares only the highest-ranked candidates.  There is no
``for every document, compare every other document`` path.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.ranking import tokens
from universal_search.intelligence.analysis import (
    INTELLIGENCE_VERSION,
    DocumentAnalysis,
    DocumentRecord,
    analyze,
)
from universal_search.intelligence.keywords import meaningful_terms
from universal_search.intelligence.language import STOPWORDS


# These values are part of the graph's maintenance contract.  They are
# exported so reports and callers can measure the bounds instead of guessing.
GRAPH_SCHEMA_VERSION = 2
GRAPH_PREPROCESSING_VERSION = 2
MAX_GRAPH_TERMS = 32
MAX_LEXICAL_TERMS = 128
MAX_PHRASES = 24
MAX_CANDIDATES_PER_DOCUMENT = 64
MAX_EDGES_PER_DOCUMENT = 32
MAX_STORED_EDGES = 100_000
MAX_SIGNAL_CHARS = 200_000
MAX_PHRASE_CANDIDATES = 512
MAX_POSTINGS_PER_TERM = 64
MAX_DIRECTORY_POSTINGS = 64
MAX_ALIAS_POSTINGS = 64
MAX_REFERENCES_PER_DOCUMENT = 16
MAX_TARGETED_RECORDS = MAX_CANDIDATES_PER_DOCUMENT + 2
# Alias/mention/declared ids are merged before pair comparison.  Keep that
# aggregate bounded independently of corpus size; posting caps alone do not
# bound the union of many bounded lists.
MAX_AGGREGATE_CANDIDATE_IDS = MAX_CANDIDATES_PER_DOCUMENT * 4
MAX_ALIAS_LOOKUPS_PER_DOCUMENT = MAX_AGGREGATE_CANDIDATE_IDS * 8
MAX_CANDIDATE_COUNTER_VALUES = MAX_AGGREGATE_CANDIDATE_IDS
MIN_GRAPH_SIMILARITY = 0.12
MIN_TERM_LENGTH = 3

# Compatibility aliases for callers that use the shorter names from the
# design brief.  The explicit names above remain the documented API.
MAX_TERMS = MAX_GRAPH_TERMS
MAX_CANDIDATES = MAX_CANDIDATES_PER_DOCUMENT
MAX_EDGES = MAX_EDGES_PER_DOCUMENT
MIN_SIMILARITY = MIN_GRAPH_SIMILARITY
PREPROCESSING_VERSION = GRAPH_PREPROCESSING_VERSION

_SIGNAL_ORDER = {
    "explicit_reference": 0,
    "phrase_overlap": 1,
    "shared_terms": 2,
    "keyword_overlap": 3,
    "lexical_similarity": 4,
    "title_heading_overlap": 5,
    "directory_relationship": 6,
    "provider_context": 7,
}
_SIGNAL_WEIGHTS = {
    "explicit_reference": 0.34,
    "phrase_overlap": 0.18,
    "shared_terms": 0.25,
    "keyword_overlap": 0.25,
    "lexical_similarity": 0.14,
    "title_heading_overlap": 0.16,
    "directory_relationship": 0.04,
    "provider_context": 0.02,
}
_STRONG_SIGNALS = {
    "explicit_reference",
    "phrase_overlap",
    "shared_terms",
    "keyword_overlap",
    "lexical_similarity",
    "title_heading_overlap",
}


def _generation() -> str:
    """A stable generation label for all rows written by this version."""
    return f"graph-v{GRAPH_SCHEMA_VERSION}-preprocessing-v{GRAPH_PREPROCESSING_VERSION}"


GRAPH_GENERATION_VERSION = _generation()
GRAPH_VERSION = GRAPH_SCHEMA_VERSION


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, default: object) -> object:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _bounded_buckets(
    entries: Iterable[tuple[object, float, str]],
    *,
    cap: int,
) -> dict[object, tuple[tuple[float, str], ...]]:
    """Keep only the strongest capped entries for each posting-list key."""
    buckets: dict[object, list[tuple[float, str]]] = defaultdict(list)
    for key, weight, document_id in entries:
        bucket = buckets[key]
        bucket.append((float(weight), str(document_id)))
        # Bound transient memory as well as final retrieval.  The final sort
        # makes the retained set deterministic.
        if len(bucket) > max(cap * 2, cap + 1):
            bucket.sort(key=lambda item: (-item[0], item[1]))
            del bucket[cap:]
    return {
        key: tuple(
            sorted(values, key=lambda item: (-item[0], item[1]))[:cap]
        )
        for key, values in buckets.items()
    }


def _bounded_ids(values: Iterable[tuple[float, str]]) -> tuple[str, ...]:
    return tuple(document_id for _weight, document_id in values)


def mark_graph_dirty(
    connection: sqlite3.Connection,
    document_ids: Iterable[str],
    reason: str = "canonical-change",
) -> int:
    """Durably mark graph inputs changed in the caller's transaction."""
    ids = sorted({str(item) for item in document_ids if item})
    if not ids:
        return 0
    if len(ids) > MAX_TARGETED_RECORDS:
        connection.execute(
            "INSERT INTO document_graph_metadata(key, value, updated_at)"
            " VALUES ('dirty:*', ?, CURRENT_TIMESTAMP)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
            " updated_at=CURRENT_TIMESTAMP",
            (str(reason)[:120],),
        )
        return 1
    connection.executemany(
        "INSERT INTO document_graph_metadata(key, value, updated_at)"
        " VALUES (?, ?, CURRENT_TIMESTAMP)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
        " updated_at=CURRENT_TIMESTAMP",
        [(f"dirty:{document_id}", str(reason)[:120]) for document_id in ids],
    )
    return len(ids)


def _dirty_ids(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(row["key"])[len("dirty:") :]
            for row in connection.execute(
                "SELECT key FROM document_graph_metadata WHERE key LIKE 'dirty:%'"
            )
        )
    )


def _clear_dirty(connection: sqlite3.Connection, document_ids: Iterable[str]) -> None:
    ids = tuple(sorted({str(item) for item in document_ids if item}))
    if not ids:
        return
    connection.executemany(
        "DELETE FROM document_graph_metadata WHERE key = ?",
        [(f"dirty:{document_id}",) for document_id in ids],
    )


def _reference_key(document_id: str) -> str:
    return f"references:{document_id}"


def _write_reference_metadata(
    connection: sqlite3.Connection, record: DocumentRecord
) -> None:
    references = tuple(
        str(item)
        for item in record.references[:MAX_REFERENCES_PER_DOCUMENT]
        if str(item)
    )
    connection.execute(
        "INSERT INTO document_graph_metadata(key, value, updated_at)"
        " VALUES (?, ?, CURRENT_TIMESTAMP)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
        " updated_at=CURRENT_TIMESTAMP",
        (_reference_key(record.document_id), _dump(list(references))),
    )


def _load_reference_metadata(
    connection: sqlite3.Connection, document_ids: Iterable[str] | None = None
) -> dict[str, tuple[str, ...]]:
    if document_ids is None:
        rows = connection.execute(
            "SELECT key, value FROM document_graph_metadata"
            " WHERE key LIKE 'references:%'"
        ).fetchall()
    else:
        ids = tuple(sorted({str(item) for item in document_ids if item}))[:MAX_TARGETED_RECORDS]
        if not ids:
            return {}
        rows = connection.execute(
            f"SELECT key, value FROM document_graph_metadata"
            f" WHERE key IN ({','.join('?' for _ in ids)})",
            tuple(_reference_key(item) for item in ids),
        ).fetchall()
    result: dict[str, tuple[str, ...]] = {}
    for row in rows:
        raw = _load(row["value"], [])
        if isinstance(raw, list):
            result[str(row["key"])[len("references:") :]] = tuple(
                str(item) for item in raw[:MAX_REFERENCES_PER_DOCUMENT]
            )
    return result


def _bounded_id_set(values: Iterable[object], cap: int) -> set[str]:
    """Return a deterministic set with a hard membership cap."""
    result: set[str] = set()
    for value in sorted({str(item) for item in values if item}):
        if len(result) >= cap:
            break
        result.add(value)
    return result


class _BoundedCandidateCounter:
    """Count candidate ids without ever materializing a corpus-sized map."""

    __slots__ = ("cap", "counts", "protected")

    def __init__(self, cap: int = MAX_AGGREGATE_CANDIDATE_IDS) -> None:
        self.cap = max(1, int(cap))
        self.counts: dict[str, int] = {}
        self.protected: set[str] = set()

    def add(
        self, values: Iterable[object], *, protected: bool = False
    ) -> None:
        for raw_id in values:
            document_id = str(raw_id)
            if not document_id:
                continue
            if document_id in self.counts:
                self.counts[document_id] += 1
                if protected:
                    self.protected.add(document_id)
                continue
            if len(self.counts) < self.cap:
                self.counts[document_id] = 1
                if protected:
                    self.protected.add(document_id)
                continue
            if not protected:
                # The aggregate is full; do not grow it with another id.
                continue
            unprotected = [
                document_id
                for document_id in self.counts
                if document_id not in self.protected
            ]
            if not unprotected:
                continue
            evicted = min(
                unprotected,
                key=lambda document_id: (self.counts[document_id], document_id),
            )
            del self.counts[evicted]
            self.counts[document_id] = 1
            self.protected.add(document_id)

    def ordered(self, limit: int) -> tuple[str, ...]:
        return tuple(
            document_id
            for document_id in sorted(
                self.counts,
                key=lambda document_id: (-self.counts[document_id], document_id),
            )[:limit]
        )


def scrub_reference_metadata(
    connection: sqlite3.Connection,
    document_ids: Iterable[str],
    aliases: Mapping[str, Iterable[object]] | None = None,
) -> int:
    """Remove direct and reverse reference values in the caller's transaction."""
    ids = tuple(sorted({str(item) for item in document_ids if item}))
    if not ids:
        return 0
    aliases = aliases or {}
    exact_values: set[str] = set()
    normalized_values: set[str] = set()
    for document_id in ids:
        values: list[object] = [document_id]
        values.extend(aliases.get(document_id, ()))
        for value in values:
            text = str(value)
            if not text:
                continue
            candidates = (value, Path(text).stem)
            for candidate in candidates:
                candidate_text = str(candidate)
                if not candidate_text:
                    continue
                exact_values.add(candidate_text.casefold())
                normalized = _normalized_word(candidate)
                if normalized:
                    normalized_values.add(normalized)
    connection.executemany(
        "DELETE FROM document_graph_metadata WHERE key = ?",
        [(f"references:{document_id}",) for document_id in ids],
    )
    removed = 0
    rows = connection.execute(
        "SELECT key, value FROM document_graph_metadata"
        " WHERE key LIKE 'references:%'"
    ).fetchall()
    for row in rows:
        raw = _load(row["value"], [])
        if not isinstance(raw, list):
            continue
        kept: list[object] = []
        for value in raw:
            text = str(value)
            normalized = _normalized_word(value)
            if text.casefold() in exact_values or normalized in normalized_values:
                removed += 1
            else:
                kept.append(value)
        if len(kept) == len(raw):
            continue
        if kept:
            connection.execute(
                "UPDATE document_graph_metadata SET value = ?, updated_at ="
                " CURRENT_TIMESTAMP WHERE key = ?",
                (_dump(kept), row["key"]),
            )
        else:
            connection.execute(
                "DELETE FROM document_graph_metadata WHERE key = ?",
                (row["key"],),
            )
    return removed


def _normalized_word(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(tokens(text.replace("_", " ")))


def _signal_content(record: DocumentRecord) -> str:
    """Bound graph work to the same order as document analysis."""
    content = record.content or ""
    if len(content) <= MAX_SIGNAL_CHARS:
        return content
    head = int(MAX_SIGNAL_CHARS * 0.75)
    tail = MAX_SIGNAL_CHARS - head
    return f"{content[:head]}\n{content[-tail:]}"


def _term_counts(record: DocumentRecord) -> dict[str, float]:
    """Return the bounded weighted concept vector for one record."""
    analysis = record.analysis
    supplied = dict(record.terms)
    if not supplied and analysis is not None:
        supplied = dict(analysis.terms)
    if not supplied and record.content:
        supplied = dict(
            meaningful_terms(tokens(_signal_content(record).casefold()))
        )

    weighted: Counter[str] = Counter()
    for term, count in supplied.items():
        normalized = _normalized_word(term)
        if not normalized or len(normalized) < MIN_TERM_LENGTH:
            continue
        try:
            value = max(1.0, float(count))
        except (TypeError, ValueError):
            value = 1.0
        weighted[normalized] += value

    # A filename and headings are useful relationship evidence, but should
    # not overwhelm the body concepts.  Add them as bounded boosts.
    title_text = " ".join(
        item for item in (record.title, record.name, *record.headings) if item
    )
    for term in tokens(title_text.casefold()):
        if len(term) >= MIN_TERM_LENGTH and term not in STOPWORDS:
            weighted[term] += 2.0

    ordered = sorted(weighted.items(), key=lambda item: (-item[1], item[0]))
    return dict(ordered[:MAX_GRAPH_TERMS])


def _lexical_counts(record: DocumentRecord) -> dict[str, float]:
    source = _signal_content(record)
    if source is None and record.analysis is not None:
        # Pairs are still useful lexical evidence when the original text is
        # not retained by a caller.  The title/headings are added below.
        source = " ".join(
            item for item in (record.title, record.headings) if item
        )
    counts: Counter[str] = Counter()
    for term in tokens((source or "").casefold()):
        if len(term) >= MIN_TERM_LENGTH and term not in STOPWORDS and not term.isdigit():
            counts[term] += 1.0
    for term in _term_counts(record):
        counts.setdefault(term, 1.0)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return dict(ordered[:MAX_LEXICAL_TERMS])


def _phrases(record: DocumentRecord) -> tuple[str, ...]:
    """Extract bounded adjacent concepts, including names like Ebers-Moll."""
    values: list[str] = []
    for supplied in record.phrases:
        phrase = _normalized_word(supplied)
        if phrase and phrase not in values:
            values.append(phrase)
    source = _signal_content(record)
    words = [
        word
        for word in tokens(source.casefold())
        if len(word) >= MIN_TERM_LENGTH and word not in STOPWORDS
    ]
    examined = 0
    for size in (2, 3):
        for start in range(0, len(words) - size + 1):
            if examined >= MAX_PHRASE_CANDIDATES or len(values) >= MAX_PHRASES:
                break
            phrase = " ".join(words[start : start + size])
            examined += 1
            if phrase not in values:
                values.append(phrase)
        if len(values) >= MAX_PHRASES or examined >= MAX_PHRASE_CANDIDATES:
            break
    return tuple(values[:MAX_PHRASES])


def _title_terms(record: DocumentRecord) -> frozenset[str]:
    text = " ".join(item for item in (record.title, record.name, *record.headings) if item)
    return frozenset(
        term
        for term in tokens(text.casefold())
        if len(term) >= MIN_TERM_LENGTH and term not in STOPWORDS
    )


def _directory_parts(record: DocumentRecord) -> tuple[str, ...]:
    normalized = str(Path(record.path).parent).replace("\\", "/").casefold()
    return tuple(part for part in normalized.split("/") if part and part != ".")


def _directory_similarity(left: DocumentRecord, right: DocumentRecord) -> float:
    return _directory_similarity_parts(
        _directory_parts(left), _directory_parts(right)
    )


def _directory_similarity_parts(
    left_parts: tuple[str, ...], right_parts: tuple[str, ...]
) -> float:
    if not left_parts or not right_parts:
        return 0.0
    if left_parts == right_parts:
        return 1.0
    common = 0
    for first, second in zip(left_parts, right_parts):
        if first != second:
            break
        common += 1
    if not common:
        return 0.0
    return common / max(len(left_parts), len(right_parts))


def _reference_values(record: DocumentRecord) -> tuple[str, ...]:
    values: list[str] = []
    for candidate in (record.name, record.path, _safe_stem(record.name)):
        normalized = _normalized_word(candidate)
        if normalized and len(normalized) >= 4 and normalized not in values:
            values.append(normalized)
    return tuple(values)


def _mention_candidates(
    item: _PreparedRecord,
    alias_postings: Mapping[str, tuple[tuple[float, str], ...]],
    stats: GraphStats | None = None,
) -> set[str]:
    """Find safe filename/path mentions with hard lookup and id caps."""
    words = tokens(item.mention_text)
    found: set[str] = set()
    examined = 0
    # Names are bounded metadata strings; eight tokens is a generous upper
    # bound that keeps this lookup finite even for adversarial filenames.
    for size in range(1, min(8, len(words)) + 1):
        for start in range(0, len(words) - size + 1):
            if examined >= MAX_ALIAS_LOOKUPS_PER_DOCUMENT:
                return found
            examined += 1
            alias = " ".join(words[start : start + size])
            for _weight, document_id in alias_postings.get(alias, ()):
                if stats is not None:
                    stats.alias_values_read += 1
                    stats.alias_candidate_values += 1
                if (
                    document_id != item.record.document_id
                    and document_id not in found
                    and len(found) < MAX_AGGREGATE_CANDIDATE_IDS
                ):
                    found.add(document_id)
                if len(found) >= MAX_AGGREGATE_CANDIDATE_IDS:
                    return found
    return found


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(term, 0.0) for term, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return _clamp(dot / (left_norm * right_norm))


def _weighted_overlap(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    shared = set(left) & set(right)
    if not shared:
        return 0.0
    numerator = sum(min(left[term], right[term]) for term in shared)
    denominator = min(sum(left.values()), sum(right.values()))
    return _clamp(numerator / denominator) if denominator else 0.0


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _safe_stem(name: str) -> str:
    stem = Path(name).stem.casefold()
    # Short stems such as ``a`` create accidental references in prose.
    return stem if len(stem) >= 4 else ""


def _reference_map(records: Mapping[str, DocumentRecord]) -> dict[str, set[str]]:
    """Map safe explicit references to target ids.

    References are limited to document ids, exact names/stems and exact path
    strings.  URLs, arbitrary prose and short names are deliberately ignored;
    this avoids treating sensitive or incidental text as a relationship.
    """
    alias_entries = (
        (alias, 1.0, target_id)
        for target_id, target in records.items()
        for alias in {
            _normalized_word(target.name),
            _normalized_word(target.path),
            _normalized_word(_safe_stem(target.name)),
        }
        if alias and len(alias) >= 4
    )
    aliases = _bounded_buckets(alias_entries, cap=MAX_ALIAS_POSTINGS)
    result: dict[str, set[str]] = defaultdict(set)
    for document_id, record in records.items():
        for reference in tuple(record.references)[:MAX_REFERENCES_PER_DOCUMENT]:
            normalized = _normalized_word(reference)
            if not normalized:
                continue
            targets = (
                {str(reference)}
                if reference in records
                else {
                    target_id
                    for target_id in _bounded_ids(aliases.get(normalized, ()))
                    if target_id != document_id
                }
            )
            current = result[document_id]
            for target_id in sorted(targets):
                if len(current) >= MAX_AGGREGATE_CANDIDATE_IDS:
                    break
                current.add(target_id)
    return result


@lru_cache(maxsize=4096)
def _mention_pattern(value: str):
    return re.compile(rf"(?<![\w]){re.escape(value)}(?![\w])")


def _prepared_mentions(left: _PreparedRecord, right: _PreparedRecord) -> bool:
    if not left.mention_text:
        return False
    return any(
        _mention_pattern(value).search(left.mention_text)
        for value in right.reference_values
    )


def _mentions(left: DocumentRecord, right: DocumentRecord) -> bool:
    return _prepared_mentions(
        _PreparedRecord(
            record=left,
            terms={},
            lexical={},
            phrases=(),
            title_terms=frozenset(),
            directory=(),
            mention_text=_signal_content(left).casefold(),
            reference_values=(),
        ),
        _PreparedRecord(
            record=right,
            terms={},
            lexical={},
            phrases=(),
            title_terms=frozenset(),
            directory=(),
            mention_text="",
            reference_values=_reference_values(right),
        ),
    )


@dataclass(frozen=True, slots=True)
class RelationshipEvidence:
    """One explainable contribution to a relationship edge."""

    kind: str
    weight: float
    values: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "weight", _clamp(self.weight))
        object.__setattr__(self, "values", tuple(str(item) for item in self.values))

    @property
    def value(self) -> float:
        return self.weight

    @property
    def score(self) -> float:
        return self.weight

    @property
    def contribution(self) -> float:
        return self.weight

    @property
    def label(self) -> str:
        return self.description or self.kind.replace("_", " ")

    @property
    def terms(self) -> tuple[str, ...]:
        return self.values

    @property
    def signal(self) -> str:
        return self.kind

    @property
    def details(self) -> tuple[str, ...]:
        return self.values

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "weight": round(self.weight, 8),
            "values": list(self.values),
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class RelationshipEdge:
    source_document_id: str
    target_document_id: str
    edge_type: str
    weight: float
    evidence: tuple[RelationshipEvidence, ...]
    version: int = GRAPH_SCHEMA_VERSION
    preprocessing_version: int = GRAPH_PREPROCESSING_VERSION
    generation: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class GraphNode:
    document_id: str
    name: str
    path: str
    source: str
    version: int = GRAPH_SCHEMA_VERSION
    preprocessing_version: int = GRAPH_PREPROCESSING_VERSION
    generation: str = ""


@dataclass(frozen=True, slots=True, init=False)
class RelatedDocument:
    """A ranked graph neighbour, independent of query relevance.

    The phase-022 constructor is ``(document_id, score, evidence)``.  The
    old phase-014 positional shape ``(document_id, name, path, score,
    shared_terms)`` is accepted as a compatibility courtesy for integrations
    that used the intelligence API directly.
    """

    document_id: str
    score: float
    evidence: tuple[RelationshipEvidence, ...]
    name: str
    path: str
    edge_type: str
    weight: float
    generation: str
    _legacy_shared_terms: tuple[str, ...]

    def __init__(
        self,
        document_id: str,
        score: float | str = 0.0,
        evidence: tuple[RelationshipEvidence, ...] | str = (),
        name: str | float = "",
        path: str | tuple[str, ...] = "",
        edge_type: str = "",
        weight: float = 0.0,
        generation: str = "",
        shared_terms: tuple[str, ...] = (),
    ) -> None:
        legacy_values: tuple[str, ...] = tuple(shared_terms)
        # Detect the old positional call without making the new API obscure.
        if isinstance(score, str) and isinstance(name, (int, float)):
            legacy_name = score
            legacy_path = str(evidence)
            legacy_score = float(name)
            if isinstance(path, tuple):
                legacy_values = tuple(str(item) for item in path)
            score_value = legacy_score
            evidence_value: tuple[RelationshipEvidence, ...] = ()
            name_value = legacy_name
            path_value = legacy_path
        else:
            score_value = float(score)
            evidence_value = tuple(evidence) if not isinstance(evidence, str) else ()
            name_value = str(name)
            path_value = str(path)
        object.__setattr__(self, "document_id", str(document_id))
        object.__setattr__(self, "score", _clamp(score_value))
        object.__setattr__(self, "evidence", evidence_value)
        object.__setattr__(self, "name", name_value)
        object.__setattr__(self, "path", path_value)
        object.__setattr__(self, "edge_type", edge_type)
        object.__setattr__(self, "weight", _clamp(weight))
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "_legacy_shared_terms", legacy_values)

    @property
    def shared_terms(self) -> tuple[str, ...]:
        if self._legacy_shared_terms:
            return self._legacy_shared_terms
        values: list[str] = []
        for item in self.evidence:
            if item.kind in {"shared_terms", "keyword_overlap", "lexical_similarity"}:
                for value in item.values:
                    if value not in values:
                        values.append(value)
        return tuple(values)

    def as_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "score": round(self.score, 6),
            "evidence": [item.as_dict() for item in self.evidence],
            "name": self.name,
            "path": self.path,
            "edge_type": self.edge_type,
            "weight": round(self.weight or self.score, 6),
            "shared_terms": list(self.shared_terms),
            "generation": self.generation,
        }


@dataclass
class GraphStats:
    """Counts and work bounds from one graph maintenance operation."""

    nodes_written: int = 0
    edges_written: int = 0
    edges_removed: int = 0
    nodes_removed: int = 0
    candidates_considered: int = 0
    comparisons: int = 0
    posting_values_read: int = 0
    alias_values_read: int = 0
    alias_candidate_values: int = 0
    records_loaded: int = 0
    skipped: int = 0
    failed: int = 0
    generation: str = _generation()

    @property
    def nodes(self) -> int:
        return self.nodes_written

    @property
    def edges(self) -> int:
        return self.edges_written

    @property
    def removed(self) -> int:
        return self.edges_removed + self.nodes_removed

    @property
    def updated(self) -> int:
        return self.nodes_written + self.edges_written

    @property
    def removed_edges(self) -> int:
        return self.edges_removed

    @property
    def candidates(self) -> int:
        return self.candidates_considered

    @property
    def candidates_compared(self) -> int:
        return self.comparisons

    @property
    def candidate_count(self) -> int:
        return self.candidates_considered

    @property
    def comparison_count(self) -> int:
        return self.comparisons

    @property
    def posting_reads(self) -> int:
        return self.posting_values_read

    @property
    def loaded_records(self) -> int:
        return self.records_loaded

    @property
    def stored_edges(self) -> int:
        return self.edges_written

    @property
    def node_count(self) -> int:
        return self.nodes_written

    @property
    def scanned(self) -> int:
        return self.nodes_written + self.skipped

    def as_dict(self) -> dict[str, object]:
        return {
            "nodes_written": self.nodes_written,
            "edges_written": self.edges_written,
            "edges_removed": self.edges_removed,
            "nodes_removed": self.nodes_removed,
            "candidates_considered": self.candidates_considered,
            "comparisons": self.comparisons,
            "posting_values_read": self.posting_values_read,
            "alias_values_read": self.alias_values_read,
            "alias_candidate_values": self.alias_candidate_values,
            "records_loaded": self.records_loaded,
            "skipped": self.skipped,
            "failed": self.failed,
            "generation": self.generation,
            "nodes": self.nodes,
            "edges": self.edges,
            "removed": self.removed,
        }


@dataclass(frozen=True, slots=True)
class _PreparedRecord:
    record: DocumentRecord
    terms: dict[str, float]
    lexical: dict[str, float]
    phrases: tuple[str, ...]
    title_terms: frozenset[str]
    directory: tuple[str, ...]
    mention_text: str
    reference_values: tuple[str, ...]


class GraphStore:
    """Versioned local relationship graph over canonical indexed documents."""

    def __init__(
        self,
        database: SearchDatabase | Path | str,
        *,
        preprocessing_version: int = GRAPH_PREPROCESSING_VERSION,
    ) -> None:
        if isinstance(database, SearchDatabase):
            self.database = database
        else:
            self.database = SearchDatabase(Path(database))
        self.preprocessing_version = int(preprocessing_version)
        self._generation_label = (
            f"graph-v{GRAPH_SCHEMA_VERSION}-preprocessing-v{self.preprocessing_version}"
        )
        self._records_cache: dict[str, DocumentRecord] = {}

    # -- public record loading ------------------------------------------------

    def document_records(self) -> list[DocumentRecord]:
        """Load and deterministically order canonical records from SQLite."""
        records = _records_from_database(self.database)
        self._records_cache = {item.document_id: item for item in records}
        return records

    # -- full rebuild ---------------------------------------------------------

    def rebuild(self, documents: Iterable[DocumentRecord]) -> GraphStats:
        """Deterministically rebuild nodes, postings and bounded edges."""
        records = _ordered_records(documents)
        stats = GraphStats(generation=self._generation_label)
        stats.records_loaded = len(records)
        with closing(self.database.connect()) as connection:
            # Delete in FK-safe order.  A full rebuild is atomic from the
            # caller's perspective: a failed write rolls back the old graph.
            connection.execute("DELETE FROM document_graph_edges")
            connection.execute("DELETE FROM document_graph_terms")
            connection.execute("DELETE FROM document_graph_nodes")
            connection.execute("DELETE FROM document_graph_metadata")
            self._write_nodes(connection, records, stats)
            edge_rows = self._compute_edges(records, stats)
            self._write_edges(connection, edge_rows, stats)
            self._write_metadata(connection)
            connection.commit()
        self._records_cache = {item.document_id: item for item in records}
        return stats

    def rebuild_from_database(self) -> GraphStats:
        """Convenience wrapper used by service and repair layers."""
        return self.rebuild(self.document_records())

    # -- incremental maintenance --------------------------------------------

    def invalidate(self, document_ids: Iterable[str]) -> GraphStats:
        """Mark ids dirty and repair only their bounded neighbourhood."""
        ids = {
            str(item)
            for item in document_ids
            if item is not None and str(item)
        }
        if not ids:
            return GraphStats(generation=self._generation_label)
        with closing(self.database.connect()) as connection:
            mark_graph_dirty(connection, ids)
            connection.commit()
        return self.repair_dirty(ids)

    refresh = invalidate
    update = invalidate

    def repair_dirty(
        self,
        document_ids: Iterable[str] | None = None,
        *,
        allow_rebuild: bool = False,
    ) -> GraphStats:
        """Repair durable dirty markers, using targeted reads when possible."""
        stats = GraphStats(generation=self._generation_label)
        requested = {
            str(item)
            for item in (document_ids or ())
            if item is not None and str(item)
        }
        rebuild_required = False
        with closing(self.database.connect()) as connection:
            dirty = set(_dirty_ids(connection)) | requested
            if not dirty:
                return stats
            if "*" in dirty or len(dirty) > MAX_TARGETED_RECORDS:
                if not allow_rebuild:
                    return stats
                rebuild_required = True
            elif not self._graph_exists(connection) or not self._is_current(connection):
                if not allow_rebuild:
                    return stats
                rebuild_required = True
            else:
                self._repair_targeted(connection, dirty, stats)
                connection.commit()
        if rebuild_required:
            records = self._load_live_records()
            result = self.rebuild(records)
            result.records_loaded = len(records)
            return result
        return stats

    def _repair_targeted(
        self,
        connection: sqlite3.Connection,
        dirty: set[str],
        stats: GraphStats,
    ) -> None:
        """Recompute a changed document from a bounded candidate window."""
        ids = tuple(sorted(dirty))
        placeholders = ",".join("?" for _ in ids)
        params = tuple(ids)
        old_neighbors = {
            str(row[0])
            for row in connection.execute(
                f"SELECT target_document_id FROM document_graph_edges"
                f" WHERE source_document_id IN ({placeholders})"
                f" UNION SELECT source_document_id FROM document_graph_edges"
                f" WHERE target_document_id IN ({placeholders})",
                params + params,
            )
        }
        changed_records = _records_from_database(self.database, ids)
        stats.records_loaded += len(changed_records)
        references = _load_reference_metadata(connection, ids)
        changed: dict[str, DocumentRecord] = {}
        for record in changed_records:
            refs = references.get(record.document_id, record.references)
            changed[record.document_id] = _with_references(record, refs)

        candidate_ids = set(old_neighbors)
        changed_aliases = {
            _normalized_word(value)
            for record in changed.values()
            for value in (record.document_id, record.name, record.path)
            if _normalized_word(value)
        }
        for record in changed.values():
            for reference in record.references[:MAX_REFERENCES_PER_DOCUMENT]:
                row = connection.execute(
                    "SELECT id FROM documents"
                    " WHERE id = ? OR path = ? OR name = ?"
                    " ORDER BY (path = ?) DESC, path LIMIT 1",
                    (reference, reference, reference, reference),
                ).fetchone()
                if row is not None:
                    candidate_ids.add(str(row["id"]))
        reverse_rows = connection.execute(
            "SELECT key, value FROM document_graph_metadata"
            " WHERE key LIKE 'references:%' LIMIT ?",
            (MAX_TARGETED_RECORDS,),
        ).fetchall()
        stats.alias_values_read += len(reverse_rows)
        for row in reverse_rows:
            raw = _load(row["value"], [])
            if not isinstance(raw, list):
                continue
            if any(_normalized_word(item) in changed_aliases for item in raw):
                candidate_ids.add(str(row["key"])[len("references:") :])
        terms = sorted(
            {
                term
                for record in changed.values()
                for term in _term_counts(record)
            }
        )[:MAX_GRAPH_TERMS]
        if terms:
            term_placeholders = ",".join("?" for _ in terms)
            rows = connection.execute(
                f"SELECT document_id FROM document_graph_terms"
                f" WHERE term IN ({term_placeholders})"
                f" ORDER BY term, document_id LIMIT ?",
                (*terms, MAX_POSTINGS_PER_TERM * len(terms)),
            ).fetchall()
            stats.posting_values_read += len(rows)
            candidate_ids.update(str(row["document_id"]) for row in rows)
        candidate_ids.difference_update(changed)
        candidate_ids = set(sorted(candidate_ids)[:MAX_TARGETED_RECORDS])
        candidate_records = _records_from_database(
            self.database, sorted(candidate_ids)
        ) if candidate_ids else []
        stats.records_loaded += len(candidate_records)
        candidate_references = _load_reference_metadata(
            connection, candidate_ids
        )
        all_references = {**references, **candidate_references}

        stats.edges_removed = int(
            connection.execute(
                f"DELETE FROM document_graph_edges"
                f" WHERE source_document_id IN ({placeholders})"
                f" OR target_document_id IN ({placeholders})",
                params + params,
            ).rowcount
        )
        stats.nodes_removed = int(
            connection.execute(
                f"DELETE FROM document_graph_nodes"
                f" WHERE document_id IN ({placeholders})",
                params,
            ).rowcount
        )
        connection.execute(
            f"DELETE FROM document_graph_terms WHERE document_id IN ({placeholders})",
            params,
        )
        self._write_nodes(connection, changed.values(), stats)
        for document_id in ids:
            if document_id not in changed:
                connection.execute(
                    "DELETE FROM document_graph_metadata WHERE key = ?",
                    (_reference_key(document_id),),
                )
        working = [
            *changed.values(),
            *[
                _with_references(record, all_references.get(record.document_id, ()))
                for record in candidate_records
            ],
        ]
        new_edges = self._compute_edges(
            working, stats, changed_ids=set(changed)
        )
        self._write_edges(connection, new_edges, stats)
        self._write_metadata(connection)
        _clear_dirty(connection, dirty)
        for document_id in ids:
            self._records_cache.pop(document_id, None)
        self._records_cache.update(changed)
        self._records_cache.update({item.document_id: item for item in candidate_records})

    # -- lookup ---------------------------------------------------------------

    def has_data(self) -> bool:
        """Whether a current graph generation exists and is not dirty."""
        with closing(self.database.connect()) as connection:
            dirty = _dirty_ids(connection)
        if dirty:
            self.repair_dirty(dirty, allow_rebuild=True)
        with closing(self.database.connect()) as connection:
            return self._graph_exists(connection) and self._is_current(connection)

    def related(self, document_id: str, limit: int = 10) -> list[RelatedDocument]:
        """Return ranked stored neighbours with their evidence."""
        if limit <= 0 or not document_id:
            return []
        with closing(self.database.connect()) as connection:
            dirty = _dirty_ids(connection)
        if dirty:
            self.repair_dirty(dirty, allow_rebuild=True)
        rows: list[sqlite3.Row] = []
        rebuild_required = False
        with closing(self.database.connect()) as connection:
            if not self._graph_exists(connection):
                return []
            if not self._is_current(connection):
                rebuild_required = True
            else:
                rows = connection.execute(
                    """
                    SELECT e.*,
                           COALESCE(d.name, n.name) AS related_name,
                           COALESCE(d.path, n.path) AS related_path
                    FROM document_graph_edges AS e
                    LEFT JOIN documents AS d
                      ON d.id = CASE
                          WHEN e.source_document_id = ? THEN e.target_document_id
                          ELSE e.source_document_id
                      END
                    LEFT JOIN document_graph_nodes AS n
                      ON n.document_id = CASE
                          WHEN e.source_document_id = ? THEN e.target_document_id
                          ELSE e.source_document_id
                      END
                    WHERE e.source_document_id = ? OR e.target_document_id = ?
                    ORDER BY e.weight DESC, related_path, related_name
                    """,
                    (document_id, document_id, document_id, document_id),
                ).fetchall()
        if rebuild_required:
            self.rebuild(self._load_live_records())
            return self.related(document_id, limit=limit)
        results: list[RelatedDocument] = []
        for row in rows:
            evidence = _evidence_from_json(row["evidence"])
            target_id = (
                row["target_document_id"]
                if row["source_document_id"] == document_id
                else row["source_document_id"]
            )
            results.append(
                RelatedDocument(
                    document_id=target_id,
                    score=float(row["weight"]),
                    evidence=evidence,
                    name=str(row["related_name"]),
                    path=str(row["related_path"]),
                    edge_type=str(row["edge_type"]),
                    weight=float(row["weight"]),
                    generation=str(row["generation"]),
                )
            )
        results.sort(key=lambda item: (-item.score, item.path, item.document_id))
        return results[:limit]

    # -- maintenance helpers --------------------------------------------------

    def clear(self) -> GraphStats:
        """Delete graph data only; canonical documents remain untouched."""
        stats = GraphStats(generation=self._generation_label)
        with closing(self.database.connect()) as connection:
            stats.edges_removed = int(
                connection.execute("SELECT COUNT(*) FROM document_graph_edges").fetchone()[0]
            )
            stats.nodes_removed = int(
                connection.execute("SELECT COUNT(*) FROM document_graph_nodes").fetchone()[0]
            )
            connection.execute("DELETE FROM document_graph_edges")
            connection.execute("DELETE FROM document_graph_terms")
            connection.execute("DELETE FROM document_graph_nodes")
            connection.execute("DELETE FROM document_graph_metadata")
            connection.commit()
        self._records_cache.clear()
        return stats

    def stats(self) -> dict[str, int]:
        with closing(self.database.connect()) as connection:
            nodes = int(connection.execute("SELECT COUNT(*) FROM document_graph_nodes").fetchone()[0])
            edges = int(connection.execute("SELECT COUNT(*) FROM document_graph_edges").fetchone()[0])
            terms = int(connection.execute("SELECT COUNT(*) FROM document_graph_terms").fetchone()[0])
        return {"nodes": nodes, "edges": edges, "terms": terms}

    def edges(self) -> list[RelationshipEdge]:
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM document_graph_edges"
                " ORDER BY source_document_id, target_document_id, edge_type"
            ).fetchall()
        return [
            RelationshipEdge(
                source_document_id=str(row["source_document_id"]),
                target_document_id=str(row["target_document_id"]),
                edge_type=str(row["edge_type"]),
                weight=float(row["weight"]),
                evidence=_evidence_from_json(row["evidence"]),
                version=int(row["version"]),
                preprocessing_version=int(row["preprocessing_version"]),
                generation=str(row["generation"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    # -- internals ------------------------------------------------------------

    def _graph_exists(self, connection: sqlite3.Connection) -> bool:
        try:
            return bool(
                connection.execute(
                    "SELECT 1 FROM document_graph_metadata LIMIT 1"
                ).fetchone()
            ) or bool(
                connection.execute(
                    "SELECT 1 FROM document_graph_nodes LIMIT 1"
                ).fetchone()
            )
        except sqlite3.OperationalError:
            return False

    def _is_current(self, connection: sqlite3.Connection) -> bool:
        expected = (
            GRAPH_SCHEMA_VERSION,
            self.preprocessing_version,
        )
        metadata = {
            row["key"]: row["value"]
            for row in connection.execute(
                "SELECT key, value FROM document_graph_metadata"
            )
        }
        if metadata.get("version") != str(GRAPH_SCHEMA_VERSION):
            return False
        if metadata.get("preprocessing_version") != str(self.preprocessing_version):
            return False
        for table in ("document_graph_nodes", "document_graph_edges", "document_graph_terms"):
            row = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE version <> ?"
                " OR preprocessing_version <> ?",
                expected,
            ).fetchone()
            if int(row[0]):
                return False
        invalid_weight = connection.execute(
            "SELECT COUNT(*) FROM document_graph_edges"
            " WHERE weight IS NULL OR weight < 0.0 OR weight > 1.0"
        ).fetchone()
        if int(invalid_weight[0]):
            return False
        return True

    def _load_live_records(self) -> list[DocumentRecord]:
        records = _records_from_database(self.database)
        if records:
            self._records_cache = {item.document_id: item for item in records}
            return records
        if self._records_cache:
            return [
                self._records_cache[key]
                for key in sorted(self._records_cache)
            ]
        # A graph-only database can still be reopened by a new store.  The
        # node table contains the bounded normalized signals needed to rebuild
        # without the original document text.
        return _records_from_graph_nodes(self.database)

    def _write_metadata(self, connection: sqlite3.Connection) -> None:
        values = {
            "version": str(GRAPH_SCHEMA_VERSION),
            "preprocessing_version": str(self.preprocessing_version),
            "generation": self._generation_label,
        }
        connection.executemany(
            "INSERT INTO document_graph_metadata(key, value, updated_at)"
            " VALUES (?, ?, CURRENT_TIMESTAMP)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
            " updated_at=CURRENT_TIMESTAMP",
            sorted(values.items()),
        )

    def _write_nodes(
        self,
        connection: sqlite3.Connection,
        records: Iterable[DocumentRecord],
        stats: GraphStats,
    ) -> None:
        term_entries: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for record in sorted(records, key=lambda item: item.document_id):
            terms = _term_counts(record)
            phrases = _phrases(record)
            headings = tuple(record.headings)
            connection.execute(
                """
                INSERT INTO document_graph_nodes (
                    document_id, version, preprocessing_version, generation,
                    name, path, source, content_hash, language, title,
                    headings, terms, normalized_terms, phrases, context
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.document_id,
                    GRAPH_SCHEMA_VERSION,
                    self.preprocessing_version,
                    self._generation_label,
                    str(record.name),
                    str(record.path),
                    str(record.source),
                    record.content_hash,
                    record.language,
                    record.title,
                    _dump(list(headings)),
                    _dump([[term, int(weight)] for term, weight in terms.items()]),
                    _dump(sorted(terms)),
                    _dump(list(phrases)),
                    str(record.context),
                ),
            )
            for term, weight in terms.items():
                term_entries[term].append((weight, record.document_id))
            _write_reference_metadata(connection, record)
            stats.nodes_written += 1

        # A posting list is a bounded retrieval structure, not a second copy
        # of the corpus.  Merge only the affected term keys with their
        # already-capped rows and retain the strongest deterministic entries.
        for term in sorted(term_entries):
            existing = [
                (float(row["weight"]), str(row["document_id"]))
                for row in connection.execute(
                    "SELECT document_id, weight FROM document_graph_terms"
                    " WHERE term = ?",
                    (term,),
                )
            ]
            combined = _bounded_buckets(
                ((term, weight, document_id) for weight, document_id in existing + term_entries[term]),
                cap=MAX_POSTINGS_PER_TERM,
            ).get(term, ())
            connection.execute(
                "DELETE FROM document_graph_terms WHERE term = ?", (term,)
            )
            connection.executemany(
                """
                INSERT INTO document_graph_terms (
                    term, document_id, weight, version,
                    preprocessing_version, generation
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        term,
                        document_id,
                        _clamp(weight),
                        GRAPH_SCHEMA_VERSION,
                        self.preprocessing_version,
                        self._generation_label,
                    )
                    for weight, document_id in combined
                ],
            )

    def _compute_edges(
        self,
        records: Iterable[DocumentRecord],
        stats: GraphStats,
        *,
        changed_ids: set[str] | None = None,
    ) -> list[RelationshipEdge]:
        ordered = _ordered_records(records)
        by_id = {record.document_id: record for record in ordered}
        prepared = {
            record.document_id: _PreparedRecord(
                record=record,
                terms=_term_counts(record),
                lexical=_lexical_counts(record),
                phrases=_phrases(record),
                title_terms=_title_terms(record),
                directory=_directory_parts(record),
                mention_text=_signal_content(record).casefold(),
                reference_values=_reference_values(record),
            )
            for record in ordered
        }
        term_postings = _bounded_buckets(
            (
                (term, weight, record_id)
                for record_id, item in prepared.items()
                for term, weight in item.terms.items()
            ),
            cap=MAX_POSTINGS_PER_TERM,
        )
        phrase_postings = _bounded_buckets(
            (
                (phrase, 1.0, record_id)
                for record_id, item in prepared.items()
                for phrase in item.phrases
            ),
            cap=MAX_POSTINGS_PER_TERM,
        )
        directory_postings = _bounded_buckets(
            (
                (item.directory[-1], 1.0, record_id)
                for record_id, item in prepared.items()
                if item.directory
            ),
            cap=MAX_DIRECTORY_POSTINGS,
        )
        alias_postings = _bounded_buckets(
            (
                (alias, 1.0, record_id)
                for record_id, item in prepared.items()
                for alias in item.reference_values
            ),
            cap=MAX_ALIAS_POSTINGS,
        )

        explicit = _reference_map(by_id)
        candidate_map: dict[str, list[str]] = {}
        reference_pairs: set[tuple[str, str]] = set()
        for source_id, item in prepared.items():
            mentioned = _mention_candidates(item, alias_postings, stats)
            declared = _bounded_id_set(
                explicit.get(source_id, ()), MAX_AGGREGATE_CANDIDATE_IDS
            )
            stats.alias_candidate_values += len(declared)
            reference_ids = _bounded_id_set(
                declared | mentioned, MAX_AGGREGATE_CANDIDATE_IDS
            )
            reference_ids.discard(source_id)
            if changed_ids is not None and source_id not in changed_ids:
                # Preserve only reverse-reference pairs that can affect a
                # changed node; do not retain a corpus-wide mention set.
                for target_id in reference_ids:
                    if target_id in changed_ids and target_id in prepared:
                        reference_pairs.add(
                            tuple(sorted((source_id, target_id)))
                        )
                continue
            candidates = _BoundedCandidateCounter(MAX_AGGREGATE_CANDIDATE_IDS)
            for term in item.terms:
                values = term_postings.get(term, ())
                stats.posting_values_read += len(values)
                candidates.add(_bounded_ids(values))
            for phrase in item.phrases:
                values = phrase_postings.get(phrase, ())
                stats.posting_values_read += len(values)
                candidates.add(_bounded_ids(values))
            if item.directory:
                values = directory_postings.get(item.directory[-1], ())
                stats.posting_values_read += len(values)
                candidates.add(_bounded_ids(values))
            candidates.add(reference_ids, protected=True)
            ordered_candidates = candidates.ordered(MAX_CANDIDATES_PER_DOCUMENT)
            candidate_map[source_id] = list(ordered_candidates)
            for target_id in reference_ids:
                if target_id in ordered_candidates and target_id in prepared:
                    reference_pairs.add(
                        tuple(sorted((source_id, target_id)))
                    )
            stats.candidates_considered += len(ordered_candidates)

        edge_candidates: dict[tuple[str, str], RelationshipEdge] = {}
        for source_id in sorted(candidate_map):
            source = prepared[source_id]
            for target_id in candidate_map[source_id]:
                target = prepared.get(target_id)
                if target is None:
                    continue
                left, right = sorted((source_id, target_id))
                if left == right:
                    continue
                if (
                    changed_ids is None
                    and source_id > target_id
                    and (left, right) not in reference_pairs
                ):
                    # Full rebuild scores each ordinary pair once.  An
                    # explicit reference is symmetric and must be scored
                    # even when the referencing document has the higher id.
                    continue
                if (
                    changed_ids is not None
                    and target_id in changed_ids
                    and source_id > target_id
                    and (left, right) not in reference_pairs
                ):
                    continue
                stats.comparisons += 1
                edge = _score_pair(
                    left,
                    right,
                    source,
                    target,
                    explicit,
                    self.preprocessing_version,
                    reference_pairs,
                )
                if edge is not None and edge.weight >= MIN_GRAPH_SIMILARITY:
                    edge_candidates[(left, right)] = edge

        # Keep the strongest bounded neighbourhood for every node.  A dropped
        # edge cannot make a document lose all evidence: the cap is applied
        # after scoring, and the remaining strongest edges stay deterministic.
        incident: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for pair in edge_candidates:
            incident[pair[0]].append(pair)
            incident[pair[1]].append(pair)
        kept: set[tuple[str, str]] = set()
        for node_id in sorted(incident):
            ranked = sorted(
                incident[node_id],
                key=lambda pair: (-edge_candidates[pair].weight, pair[0], pair[1]),
            )
            kept.update(ranked[:MAX_EDGES_PER_DOCUMENT])
        final_pairs = sorted(
            kept,
            key=lambda pair: (-edge_candidates[pair].weight, pair[0], pair[1]),
        )[:MAX_STORED_EDGES]
        return [edge_candidates[pair] for pair in sorted(final_pairs)]

    def _write_edges(
        self,
        connection: sqlite3.Connection,
        edges: Iterable[RelationshipEdge],
        stats: GraphStats,
    ) -> None:
        for edge in sorted(
            edges,
            key=lambda item: (
                item.source_document_id,
                item.target_document_id,
                item.edge_type,
            ),
        ):
            evidence = _dump([item.as_dict() for item in edge.evidence])
            connection.execute(
                """
                INSERT INTO document_graph_edges (
                    source_document_id, target_document_id, edge_type,
                    relationship_type, weight, evidence, version,
                    preprocessing_version, generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.source_document_id,
                    edge.target_document_id,
                    edge.edge_type,
                    edge.edge_type,
                    _clamp(edge.weight),
                    evidence,
                    GRAPH_SCHEMA_VERSION,
                    self.preprocessing_version,
                    self._generation_label,
                ),
            )
            stats.edges_written += 1


def _with_references(
    record: DocumentRecord, references: Iterable[str]
) -> DocumentRecord:
    return replace(record, references=tuple(references))


def _ordered_records(documents: Iterable[DocumentRecord]) -> list[DocumentRecord]:
    unique: dict[str, DocumentRecord] = {}
    for raw_item in documents:
        if isinstance(raw_item, DocumentRecord):
            item = raw_item
        elif hasattr(raw_item, "id"):
            item = DocumentRecord.from_document(raw_item)
        elif isinstance(raw_item, Mapping):
            values = dict(raw_item)
            if "id" in values and "document_id" not in values:
                values["document_id"] = values.pop("id")
            item = DocumentRecord(**values)
        else:
            raise TypeError("GraphStore.rebuild expects DocumentRecord values")
        if not item.document_id:
            continue
        unique[str(item.document_id)] = item
    return [unique[key] for key in sorted(unique)]


def _records_from_database(
    database: SearchDatabase,
    document_ids: Iterable[str] | None = None,
) -> list[DocumentRecord]:
    from universal_search.intelligence.store import row_to_analysis

    ids = tuple(sorted({str(item) for item in document_ids if item})) if document_ids is not None else None
    where = ""
    params: tuple[str, ...] = ()
    if ids is not None:
        if not ids:
            return []
        where = " WHERE d.id IN (" + ",".join("?" for _ in ids) + ")"
        params = ids[:MAX_TARGETED_RECORDS]
    with closing(database.connect()) as connection:
        rows = connection.execute(
            """
            SELECT d.id AS document_id, d.name, d.path, d.source,
                   d.content_hash, i.version AS version,
                   i.content_hash AS analysis_content_hash,
                   i.language, i.title, i.headings, i.terms, i.pairs,
                   i.sections, i.analyzed_chars, i.truncated,
                   (SELECT f.content FROM documents_fts AS f
                    WHERE f.document_id = d.id LIMIT 1) AS content
            FROM documents AS d
            LEFT JOIN document_intelligence AS i ON i.document_id = d.id
            """
            + where
            + " ORDER BY d.id",
            params,
        ).fetchall()
    result: list[DocumentRecord] = []
    for row in rows:
        content = row["content"]
        content_hash = row["content_hash"]
        if content is not None:
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        analysis: DocumentAnalysis | None = None
        if (
            row["version"] == INTELLIGENCE_VERSION
            and row["analysis_content_hash"] == content_hash
        ):
            analysis = row_to_analysis(row)
        else:
            analysis = analyze(content or "", name=str(row["name"] or ""))
        result.append(
            DocumentRecord(
                document_id=str(row["document_id"]),
                name=str(row["name"] or ""),
                path=str(row["path"] or ""),
                source=str(row["source"] or "local"),
                content_hash=content_hash,
                analysis=analysis,
                content=content,
            )
        )
    return result


def _records_from_graph_nodes(database: SearchDatabase) -> list[DocumentRecord]:
    """Reconstruct bounded graph records when no canonical rows are present."""
    with closing(database.connect()) as connection:
        rows = connection.execute(
            "SELECT document_id, name, path, source, content_hash, language,"
            " title, headings, terms, phrases, context"
            " FROM document_graph_nodes ORDER BY document_id"
        ).fetchall()
    result: list[DocumentRecord] = []
    for row in rows:
        raw_terms = _load(row["terms"], [])
        terms = tuple(
            (str(pair[0]), int(pair[1]))
            for pair in raw_terms
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        ) if isinstance(raw_terms, list) else ()
        raw_headings = _load(row["headings"], [])
        headings = tuple(str(item) for item in raw_headings) if isinstance(raw_headings, list) else ()
        raw_phrases = _load(row["phrases"], [])
        phrases = tuple(str(item) for item in raw_phrases) if isinstance(raw_phrases, list) else ()
        result.append(
            DocumentRecord(
                document_id=str(row["document_id"]),
                name=str(row["name"] or ""),
                path=str(row["path"] or ""),
                source=str(row["source"] or "local"),
                content_hash=row["content_hash"],
                language=row["language"],
                title=row["title"],
                headings=headings,
                terms=terms,
                phrases=phrases,
                context=str(row["context"] or ""),
            )
        )
    return result


def _evidence_from_json(value: str | None) -> tuple[RelationshipEvidence, ...]:
    raw = _load(value, [])
    if not isinstance(raw, list):
        return ()
    result: list[RelationshipEvidence] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            result.append(
                RelationshipEvidence(
                    kind=str(item.get("kind", "unknown")),
                    weight=float(item.get("weight", 0.0)),
                    values=tuple(str(part) for part in item.get("values", ())),
                    description=str(item.get("description", "")),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(
        sorted(result, key=lambda item: (-item.weight, item.kind, item.values))
    )


def _score_pair(
    left_id: str,
    right_id: str,
    left: _PreparedRecord,
    right: _PreparedRecord,
    explicit: Mapping[str, set[str]],
    preprocessing_version: int,
    reference_pairs: set[tuple[str, str]] | None = None,
) -> RelationshipEdge | None:
    shared_terms = sorted(set(left.terms) & set(right.terms))
    keyword = _weighted_overlap(left.terms, right.terms)
    lexical = _cosine(left.lexical, right.lexical)
    title_overlap = _jaccard(left.title_terms, right.title_terms)
    phrase_values = sorted(set(left.phrases) & set(right.phrases))
    phrase_overlap = _jaccard(left.phrases, right.phrases)
    directory = _directory_similarity_parts(left.directory, right.directory)
    reference = (
        (left_id, right_id) in (reference_pairs or set())
        or right_id in explicit.get(left_id, ())
        or left_id in explicit.get(right_id, ())
    )
    if (
        not reference
        and not shared_terms
        and not phrase_values
        and title_overlap <= 0.0
        and lexical <= 0.0
    ):
        # Prose reference detection is the expensive fallback.  When a
        # content signal already explains the pair, avoid scanning both
        # documents; explicit references still have their own fast map.
        reference = _prepared_mentions(left, right) or _prepared_mentions(right, left)
    provider_context = 0.0
    if left.record.source == right.record.source:
        provider_context = 0.6
    if left.record.context and left.record.context == right.record.context:
        provider_context = 1.0

    values: list[tuple[str, float, tuple[str, ...], str]] = []
    if reference:
        values.append((
            "explicit_reference", 1.0, (right.record.name,),
            "one document explicitly names the other",
        ))
    if phrase_values:
        values.append((
            "phrase_overlap", phrase_overlap, tuple(phrase_values[:8]),
            "shared normalized phrase",
        ))
    if shared_terms:
        values.append((
            "shared_terms", min(1.0, len(shared_terms) / 4.0),
            tuple(shared_terms[:8]), "shared normalized terms",
        ))
        values.append((
            "keyword_overlap", keyword, tuple(shared_terms[:8]),
            "weighted concept overlap",
        ))
    if lexical > 0.0:
        values.append((
            "lexical_similarity", lexical,
            tuple(sorted(set(left.lexical) & set(right.lexical))[:8]),
            "bounded lexical similarity",
        ))
    if title_overlap > 0.0:
        values.append((
            "title_heading_overlap", title_overlap,
            tuple(sorted(left.title_terms & right.title_terms)[:8]),
            "title or heading overlap",
        ))
    if directory > 0.0:
        values.append((
            "directory_relationship", directory,
            (str(Path(left.record.path).parent),),
            "shared directory relationship",
        ))
    if provider_context > 0.0:
        values.append((
            "provider_context", provider_context,
            (str(left.record.source),), "same provider/context",
        ))

    if not any(kind in _STRONG_SIGNALS for kind, _weight, _values, _description in values):
        return None
    evidence = tuple(
        RelationshipEvidence(kind, weight, evidence_values, description)
        for kind, weight, evidence_values, description in values
        if weight > 0.0
    )
    score = sum(_SIGNAL_WEIGHTS[item.kind] * item.weight for item in evidence)
    score = _clamp(score)
    if score < MIN_GRAPH_SIMILARITY:
        return None
    edge_type = min(
        evidence,
        key=lambda item: (
            -_SIGNAL_WEIGHTS[item.kind] * item.weight,
            _SIGNAL_ORDER.get(item.kind, 99),
            item.kind,
        ),
    ).kind
    return RelationshipEdge(
        source_document_id=min(left_id, right_id),
        target_document_id=max(left_id, right_id),
        edge_type=edge_type,
        weight=score,
        evidence=tuple(
            sorted(evidence, key=lambda item: (-item.weight, item.kind, item.values))
        ),
        version=GRAPH_SCHEMA_VERSION,
        preprocessing_version=preprocessing_version,
        generation=(
            f"graph-v{GRAPH_SCHEMA_VERSION}-preprocessing-v{preprocessing_version}"
        ),
    )


def document_records(database: SearchDatabase) -> list[DocumentRecord]:
    """Public loader used by repair and service layers."""
    return _records_from_database(database)


__all__ = [
    "GRAPH_GENERATION_VERSION",
    "GRAPH_PREPROCESSING_VERSION",
    "GRAPH_SCHEMA_VERSION",
    "GRAPH_VERSION",
    "MAX_CANDIDATES",
    "MAX_CANDIDATES_PER_DOCUMENT",
    "MAX_EDGES",
    "MAX_EDGES_PER_DOCUMENT",
    "MAX_AGGREGATE_CANDIDATE_IDS",
    "MAX_ALIAS_LOOKUPS_PER_DOCUMENT",
    "MAX_GRAPH_TERMS",
    "MAX_ALIAS_POSTINGS",
    "MAX_CANDIDATE_COUNTER_VALUES",
    "MAX_DIRECTORY_POSTINGS",
    "MAX_POSTINGS_PER_TERM",
    "MAX_REFERENCES_PER_DOCUMENT",
    "mark_graph_dirty",
    "scrub_reference_metadata",
    "MAX_STORED_EDGES",
    "MIN_GRAPH_SIMILARITY",
    "MIN_SIMILARITY",
    "DocumentRecord",
    "GraphNode",
    "GraphStats",
    "GraphStore",
    "RelatedDocument",
    "RelationshipEdge",
    "RelationshipEvidence",
    "document_records",
]
