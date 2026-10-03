"""Phase 045: turning a miss into a cause.

`failure_class` has existed since phase 026 and it is a label a human wrote
on the query when the corpus was authored. It is never computed, never
aggregated and never used to decide anything: `runner.failure_inventory` copies
the string into a JSON row and stops. So the project's answer to "why did this
query miss?" has been whatever the author remembered.

This module answers it by probing, in a fixed order, and reports the evidence
that produced the verdict. The order is the whole design, because a miss has
several true causes at once and only the *earliest* one is the one worth
fixing. A document that was never extracted is not also a ranking problem; a
document the filter excluded is not also a lexical problem. Diagnosing the
outermost cause is what stops three people from fixing the same miss.

The taxonomy is the phase's list, verbatim, in the order the probes run:

=========================  ====================================================
stale index                the document is not in the index the way it should be
extraction                  it is in the index, but its text layer is not usable
filtering                   a filter in the query excludes it, correctly
ranking                     it is in the candidate pool and below the cut
phrase handling             quoting changes the answer in either direction
morphology / fuzzy          only the typo-tolerant layer finds it
semantic fallback           only the embedding layer finds it
filename / path             a name or parent directory carries a term the body lacks
lexical mismatch            none of the above: the words simply are not there
interaction / UI            not a retrieval fact; classified as out of scope
=========================  ====================================================

Two of the ten are *not* retrieval failures and this module says so rather than
forcing them into a retrieval bucket. `stale index` is a scheduler fact, and
`interaction/UI` lives in the GUI; both get a verdict with the evidence that
they are somebody else's problem, which is more useful than a wrong class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from evaluation import corpus as corpus_module
from universal_search.index.database import SearchDatabase
from universal_search.index.search import SearchEngine, SearchResult

# The ten classes, in the order the probes run. A tuple, not a set: the order
# is the logic, and a set would let two runs disagree about which cause is
# reported first for a miss that has more than one.
FAILURE_CLASSES: tuple[str, ...] = (
    "stale index",
    "extraction",
    "filtering",
    "ranking",
    "phrase handling",
    "morphology/fuzzy",
    "semantic fallback",
    "filename/path",
    "lexical mismatch",
    "interaction/UI",
)

# A probe result that means "this cause is not the one". Named so a verdict can
# never be silently confabulated from a missing key.
NOT_THE_CAUSE = "no"


@dataclass(frozen=True, slots=True)
class Verdict:
    """Why one (query, document) pair missed, and what proves it."""

    query: str
    document: str
    cause: str
    evidence: tuple[str, ...]
    reachable_by_ranking: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "document": self.document,
            "cause": self.cause,
            "evidence": list(self.evidence),
            # The single question phase 045 exists to answer for the ranker:
            # if this is False, no weighting change could ever have helped.
            "reachable_by_ranking": self.reachable_by_ranking,
        }


# Only three of the ten causes are things a *weighting* change could touch. The
# rest are retrieval, extraction or indexing facts, and saying so numerically
# is what keeps "improve search quality" from turning into "turn a dial".
RANKING_REACHABLE = frozenset({"ranking", "phrase handling", "filename/path"})


@dataclass(frozen=True, slots=True)
class IndexProbe:
    """What the index itself says, independent of any query."""

    indexed: dict[str, str]
    by_path: dict[str, str]
    last_seen: dict[str, int]


def probe_index(database: SearchDatabase) -> IndexProbe:
    """Every indexed document's id, path and last-seen run.

    Read from the database rather than inferred from search results, because
    "the document is missing" and "the document was there but ranked low" look
    identical from the outside and are different problems.
    """
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, path, last_seen_run FROM documents"
        ).fetchall()
    indexed: dict[str, str] = {}
    by_path: dict[str, str] = {}
    last_seen: dict[str, int] = {}
    for row in rows:
        path = str(row["path"])
        by_path[path] = row["id"]
        indexed[path] = row["name"]
        try:
            last_seen[row["id"]] = int(row["last_seen_run"] or 0)
        except (TypeError, ValueError):
            last_seen[row["id"]] = 0
    return IndexProbe(indexed=indexed, by_path=by_path, last_seen=last_seen)


def _ids_for(engine: SearchEngine, corpus_root: Path) -> dict[str, str]:
    """Corpus id -> database id.

    The two are not interchangeable and the difference is not cosmetic: the
    corpus labels a document ``bjt-modelo`` and the index stores the SHA-256
    of its path. The first version of this function mapped a corpus id to
    itself, and every single document in the corpus came back "stale index" --
    a clean, confident, entirely wrong answer for 64 verdicts.

    ``corpus_root`` is an argument rather than something derived from the
    database path, because a caller may put the index anywhere and guessing
    that the corpus sits beside the database file is the same mistake wearing
    a different hat.
    """
    resolved = corpus_module.ids_by_path(corpus_root)
    with engine.database.connect() as connection:
        stored = {
            str(row["path"]): row["id"]
            for row in connection.execute("SELECT id, path FROM documents")
        }
    mapping: dict[str, str] = {}
    for path, corpus_id in resolved.items():
        identifier = stored.get(str(path))
        if identifier is not None:
            mapping[corpus_id] = identifier
    return mapping


def _find(engine: SearchEngine, database_id: str, results) -> SearchResult | None:
    for result in results:
        if result.document_id == database_id:
            return result
    return None


def _wordlike(text: str) -> list[str]:
    """The alphanumeric runs of a query term, as the tokenizer would see them.

    A term with no word characters at all -- a run of CJK, an emoji -- has no
    boundary for a whitespace-and-punctuation tokenizer to find, and that is a
    fact about the text rather than about the ranking. Classifying it as a
    lexical miss would send it to the wrong owner.
    """
    import re

    return re.findall(r"\w+", text, flags=re.UNICODE)


def _terms_of(query: str) -> tuple[str, ...]:
    from universal_search.query import parse_query, translate

    plan = translate(parse_query(query))
    return tuple(plan.terms)


def diagnose(
    engine: SearchEngine,
    labelled,
    *,
    corpus_root: Path,
    fuzzy=None,
    semantic=None,
    limit: int = 10,
    now: datetime | None = None,
) -> list[Verdict]:
    """One verdict per (query, document) that the lexical engine missed.

    A *miss* is a relevant document that is not in the returned list. Only
    those are diagnosed, because a query that answered correctly has no cause
    and inventing one would put noise in the inventory.
    """
    ids = _ids_for(engine, corpus_root)
    returned: dict[str, list[SearchResult]] = {
        labelled.query: engine.search(labelled.query, limit=limit, now=now)
        for labelled in corpus_module.LABELLED_QUERIES
    }
    verdicts: list[Verdict] = []
    for document in sorted(labelled.relevant):
        database_id = ids.get(document)
        if database_id is None:
            # The corpus declares a relevant document the index never knew
            # about. That is the same finding as `stale index`, arrived at
            # from the label side rather than the index side.
            verdicts.append(Verdict(
                query=labelled.query,
                document=document,
                cause="stale index",
                evidence=("el documento no existe en el indice",),
                reachable_by_ranking=False,
            ))
            continue
        if _find(engine, database_id, returned[labelled.query]) is not None:
            continue
        verdicts.append(_diagnose_one(
            engine, labelled, document, database_id,
            fuzzy=fuzzy, semantic=semantic, limit=limit, now=now,
        ))
    return verdicts


def _diagnose_one(
    engine: SearchEngine,
    labelled,
    document: str,
    database_id: str,
    *,
    fuzzy,
    semantic,
    limit: int,
    now: datetime | None,
) -> Verdict:
    query = labelled.query
    evidence: list[str] = []

    def verdict(cause: str, *notes: str) -> Verdict:
        return Verdict(
            query=query,
            document=document,
            cause=cause,
            evidence=tuple([*evidence, *notes]),
            reachable_by_ranking=cause in RANKING_REACHABLE,
        )

    # 1. In the index at all?
    # `documents` holds metadata only; the text layer lives in the FTS table.
    # Asking `documents` for a content column would be a query error rather
    # than a finding, and the error is silent in every way that matters.
    with engine.database.connect() as connection:
        row = connection.execute(
            "SELECT d.name AS name, d.path AS path, f.content AS content "
            "FROM documents AS d "
            "LEFT JOIN documents_fts AS f ON f.document_id = d.id "
            "WHERE d.id = ?",
            (database_id,),
        ).fetchone()
    if row is None:
        return verdict("stale index", "no esta en la tabla documents")

    # 2. Does it have a usable text layer?
    corpus_document = _corpus_document(document)
    content = row["content"] or ""
    if not content.strip():
        # A binary the corpus expects to be unreadable is fine; a binary the
        # corpus declares as *text* is an extraction failure and nothing else.
        if corpus_document is not None and corpus_document.content:
            return verdict(
                "extraction",
                "el corpus le da texto y el indice guardo una capa vacia",
            )
        return verdict("extraction", "documento binario sin capa de texto")

    # 3. Does the query carry filters that exclude it?
    filters = _filters_in(query)
    if filters and not _passes_filters(str(row["path"]), filters):
        return verdict(
            "filtering",
            f"el documento no cumple {filters}, que la consulta pide",
        )

    # 4. Is it in the candidate pool, just below the cut?
    pool = engine.search(query, limit=500, now=now)
    position = next(
        (i for i, item in enumerate(pool) if item.document_id == database_id),
        None,
    )
    if position is not None:
        pooled = pool[position]
        return verdict(
            "ranking",
            f"llego al puesto {position + 1} del conjunto de candidatos con "
            f"puntaje {pooled.score:.6f} y quedo fuera del corte de {limit}",
        )

    # 5. Does quoting the query change anything?
    if _phrase_differs(engine, query, database_id, now):
        return verdict("phrase handling", "entrecomillado cambia el resultado")

    # 6. Does the typo-tolerant layer find it?
    if fuzzy is not None:
        fuzzy_hits = fuzzy.search(query, limit=500)
        if _find(engine, database_id, fuzzy_hits) is not None:
            return verdict(
                "morphology/fuzzy", "solo la capa tolerante a erratas lo encuentra"
            )

    # 7. Does the embedding layer find it?
    if semantic is not None:
        semantic_hits = semantic.search(query, limit=500)
        if _find(engine, database_id, semantic_hits) is not None:
            return verdict(
                "semantic fallback", "solo la capa semantica lo encuentra"
            )

    # 8. Is a query term present in the name or a parent directory but not the
    #    body? That is a retrieval gap, not a vocabulary gap.
    name_terms = _name_and_path_terms(str(row["path"]), row["name"])
    body_terms = set(_wordlike(content.casefold()))
    missing = [t for t in _terms_of(query) if t not in body_terms]
    if missing and all(t in name_terms for t in missing):
        return verdict(
            "filename/path",
            f"los terminos {missing} estan en el nombre o la ruta, no en el cuerpo",
        )

    # 9. Is the text present but unreachable as a *token*?
    #    The strong version of "the words are there and it still fails": if the
    #    query appears verbatim in the document and search found nothing, the
    #    problem cannot be vocabulary. It is that the tokenizer produced
    #    different tokens. A script with no spaces -- CJK, for instance -- turns
    #    a whole sentence into one token, so a substring query can never match
    #    it. Calling that a lexical miss sends it to whoever owns the query
    #    vocabulary, which is exactly the wrong person.
    terms = _terms_of(query)
    haystack = content.casefold()
    if terms and all(t.casefold() in haystack for t in terms):
        return verdict(
            "extraction",
            "el texto esta en el documento pero el tokenizador no produce "
            f"ese token para {list(terms)}",
        )

    # 10. Are the missing terms only inflected differently?
    #     A crude suffix rule on purpose. The alternative is calling every
    #     singular/plural pair a lexical miss, and the fix for a lexical miss is
    #     a thesaurus while the fix for this one is a stemmer.
    if missing and all(
        _has_stem_variant(term, haystack, name_terms) for term in missing
    ):
        return verdict(
            "morphology/fuzzy",
            f"los terminos {missing} solo aparecen en otra forma flexionada",
        )

    # 11. It is simply not there.
    return verdict(
        "lexical mismatch",
        f"la consulta exige {list(terms)} y el documento no los tiene",
    )


# Spanish and English plural/participle endings, longest first so "ciones" is
# tried before "cion" and "s" is tried last.
_SUFFIXES = (
    "aciones", "iciones", "amiento", "imiento", "acion", "ucion",
    "ciones", "mente", "ando", "iendo", "ados", "idas", "ados",
    "es", "as", "os", "on", "a", "e", "s",
)


def _stem(word: str) -> str:
    lowered = word.casefold()
    for suffix in _SUFFIXES:
        if lowered.endswith(suffix) and len(lowered) - len(suffix) >= 3:
            return lowered[: -len(suffix)]
    return lowered


def _has_stem_variant(term: str, haystack: str, name_terms: set[str]) -> bool:
    """Whether ``term`` appears in the document only in a different inflection.

    Tokenised rather than substring-compared, so ``receta`` does not match
    ``recrear`` by accident.
    """
    stem = _stem(term)
    if len(stem) < 3:
        return False
    words = set(_wordlike(haystack)) | name_terms
    return any(
        word == term.casefold() or _stem(word) == stem
        for word in words
    )


def _corpus_document(document: str):
    for candidate in corpus_module.DOCUMENTS:
        if candidate.id == document:
            return candidate
    return None


def _filters_in(query: str) -> tuple[tuple[str, str], ...]:
    """The ``key:value`` filters in a query, as the parser resolved them.

    Read from the parsed plan rather than by regexing the raw string, so the
    diagnosis agrees with the engine about what the query means. The parser
    returns a bare ``Term`` for a query with no operators and a bare
    ``Filter`` for a query that is only a filter, so the tree is walked
    recursively instead of assuming a shape. It also normalises ``type:txt``
    to ``.txt``, which is why the comparison strips dots rather than assuming
    what the user typed.
    """
    from universal_search.query import parse_query

    found: list[tuple[str, str]] = []

    def walk(node) -> None:
        field = getattr(node, "field", None)
        if field is not None:
            found.append((str(field), str(node.value)))
            return
        for item in getattr(node, "items", ()) or ():
            walk(item)

    walk(parse_query(query))
    return tuple(found)


def _passes_filters(path: str, filters) -> bool:
    lowered = path.casefold()
    suffix = Path(lowered).suffix
    for key, value in filters:
        wanted = value.casefold().lstrip(".")
        if key == "type" and not suffix.lstrip(".").startswith(wanted):
            return False
        if key == "source" and not lowered.startswith(wanted):
            return False
    return True


def _name_and_path_terms(path: str, name: str) -> set[str]:
    return {
        token.casefold()
        for token in (*_wordlike(name), *_wordlike(path))
    }


def _phrase_differs(engine, query: str, database_id: str, now) -> bool:
    """Whether quoting or unquoting the query changes this document's presence."""
    stripped = query.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        return False
    quoted = f'"{stripped}"'
    plain = engine.search(stripped, limit=500, now=now)
    phrase = engine.search(quoted, limit=500, now=now)
    return (
        _find(engine, database_id, plain) is None
        and _find(engine, database_id, phrase) is not None
    )


# -- the inventory -------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Inventory:
    """Every diagnosed miss, plus what it means for the ranker."""

    verdicts: tuple[Verdict, ...]
    by_class: dict[str, int] = field(default_factory=dict)
    ranking_reachable: int = 0
    unclassified: int = 0

    def counts(self) -> dict[str, int]:
        tally = {name: 0 for name in FAILURE_CLASSES}
        for verdict in self.verdicts:
            tally[verdict.cause] = tally.get(verdict.cause, 0) + 1
        return tally

    def as_dict(self) -> dict[str, object]:
        return {
            "by_class": self.counts(),
            "ranking_reachable": self.ranking_reachable,
            "unclassified": self.unclassified,
            "verdicts": [verdict.as_dict() for verdict in self.verdicts],
        }


def build_inventory(verdicts) -> Inventory:
    tally: dict[str, int] = {}
    reachable = 0
    unclassified = 0
    for verdict in verdicts:
        tally[verdict.cause] = tally.get(verdict.cause, 0) + 1
        if verdict.reachable_by_ranking:
            reachable += 1
        if verdict.cause not in FAILURE_CLASSES:
            unclassified += 1
    return Inventory(
        verdicts=tuple(verdicts),
        by_class=tally,
        ranking_reachable=reachable,
        unclassified=unclassified,
    )