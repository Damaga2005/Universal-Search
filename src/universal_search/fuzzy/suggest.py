"""Phase 032: query suggestions, derived from the index and verified.

The rule that makes this safe: **a suggestion is a query that was actually
run and actually returned a document.** Nothing is offered on the strength of
looking like a word. There is no dictionary here, no spell-checking service,
no network, and no list of "common typos" — the only words this module may
propose are the words that exist in the user's own index.

That means a suggestion can never be wrong in the way a suggestion usually is:
it cannot invent a term, and it cannot propose a correction for a query that
was simply right. If nothing verifies, nothing is offered.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from typing import Callable, Iterable
from weakref import WeakKeyDictionary

from universal_search.fuzzy.trigrams import (
    MIN_TOKEN_CHARS,
    fold,
    query_trigrams,
    tokens_of,
)
from universal_search.fuzzy.verify import (
    damerau_levenshtein,
    edit_budget,
    is_nonsense,
)


# How many corrections to try per token, and how many to keep overall. Both
# are bounds, not preferences: the verification step is what decides.
MAX_CANDIDATES_PER_TOKEN = 12
MAX_SUGGESTIONS = 3

# A vocabulary larger than this is scanned with a character pre-filter rather
# than in full. A local index of a few thousand documents is well under it; a
# very large one must not turn a keystroke into a full dictionary scan.
VOCABULARY_SCAN_LIMIT = 50_000

# The view is created and dropped around every use, so nothing is stored.
_VOCAB_TABLE = "documents_query_vocab"


@dataclass(frozen=True, slots=True)
class Suggestion:
    query: str
    token: str
    replacement: str
    distance: int
    results: int

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "token": self.token,
            "replacement": self.replacement,
            "distance": self.distance,
            "results": self.results,
        }


def indexed_vocabulary(connection, *, limit: int = VOCABULARY_SCAN_LIMIT) -> list[tuple[str, int]]:
    """The words the index actually contains, most frequent first.

    ``fts5vocab`` is a *view* over the existing FTS index: creating it costs
    nothing and storing it would cost something, so it is created, read and
    dropped inside this function. If the process dies in between, a stray view
    is left behind — harmless, and dropped on the next call.
    """
    connection.execute(f"DROP TABLE IF EXISTS {_VOCAB_TABLE}")
    try:
        connection.execute(
            f"CREATE VIRTUAL TABLE {_VOCAB_TABLE}"
            " USING fts5vocab(documents_fts, 'row')"
        )
    except Exception:
        # An SQLite build without fts5vocab simply has no suggestions. The
        # feature is an aid, never a dependency of search.
        return []
    try:
        rows = connection.execute(
            f"SELECT term, cnt FROM {_VOCAB_TABLE} ORDER BY cnt DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        connection.execute(f"DROP TABLE IF EXISTS {_VOCAB_TABLE}")
    return [
        (str(row["term"]), int(row["cnt"] or 0))
        for row in rows
        if str(row["term"])
    ]


def correct_token(
    token: str,
    vocabulary: Iterable[tuple[str, int]],
    *,
    limit: int = MAX_CANDIDATES_PER_TOKEN,
) -> list[tuple[str, int]]:
    """Words from the index that could replace ``token``, best first.

    Ordered by edit distance, then by how common the word is in the index:
    a correction to a word the user has written 200 times beats one they
    wrote once, at the same distance.
    """
    folded = fold(token)
    budget = edit_budget(folded)
    if budget == 0:
        return []
    grams = query_trigrams(folded)
    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for term, count in vocabulary:
        candidate = fold(term)
        if candidate in seen or len(candidate) < MIN_TOKEN_CHARS:
            continue
        if candidate == folded:
            continue
        if abs(len(candidate) - len(folded)) > budget:
            continue
        # Cheap character pre-filter: a word within ``budget`` edits must share
        # at least ``len - budget`` characters. Exact, and it skips the bulk
        # of the vocabulary without a single dynamic-programming run.
        if _char_overlap(candidate, folded) < max(1, len(folded) - budget):
            continue
        if grams and not (grams & query_trigrams(candidate)):
            continue
        distance = damerau_levenshtein(folded, candidate, cap=budget)
        if distance <= budget:
            seen.add(candidate)
            scored.append((distance, -count, candidate))
    scored.sort()
    return [(word, distance) for distance, _count, word in scored[:limit]]


def _char_overlap(left: str, right: str) -> int:
    counts: dict[str, int] = {}
    for char in left:
        counts[char] = counts.get(char, 0) + 1
    shared = 0
    for char in right:
        if counts.get(char, 0) > 0:
            counts[char] -= 1
            shared += 1
    return shared


class QuerySuggester:
    """Proposes queries that are verified to return a real document."""

    def __init__(
        self,
        engine,
        *,
        vocabulary: Callable[[], list[tuple[str, int]]] | None = None,
        max_suggestions: int = MAX_SUGGESTIONS,
    ) -> None:
        # ``engine`` is the whole search engine the user is already searching
        # with (lexical, plus the fuzzy layer of phase 031 when enabled). A
        # suggestion is verified with that same engine, so it can never promise
        # something the search itself would not return.
        self.engine = engine
        self._vocabulary_source = vocabulary
        self.max_suggestions = max_suggestions
        self._cached: tuple[tuple, list[tuple[str, int]]] | None = None

    @property
    def search(self):
        return self.engine.search

    def vocabulary(self) -> list[tuple[str, int]]:
        """The indexed vocabulary, cached on a content signal.

        Reading it means creating, scanning and dropping an ``fts5vocab``
        view, which the phase-032 latency gate measured at 9,3 ms — far too
        much to repeat on every keystroke.

        The cache key is *what the vocabulary depends on*: how many documents
        are indexed and when the most recent one was written. That pair
        changes exactly when the vocabulary can change, and one indexed
        lookup costs a fraction of a millisecond.

        The first version keyed on the database file's size and modification
        time, and it never hit: the WAL rewrites the main file underneath it
        during ordinary checkpointing, so every keystroke looked like a brand
        new index. A cache that never hits is worse than no cache, because it
        also hides that fact.
        """
        if self._vocabulary_source is not None:
            return self._vocabulary_source()
        key = self._vocabulary_key()
        if self._cached is not None and self._cached[0] == key:
            return self._cached[1]
        with closing(self.engine.database.connect()) as connection:
            vocabulary = indexed_vocabulary(connection)
        self._cached = (key, vocabulary)
        return vocabulary

    def _vocabulary_key(self) -> tuple:
        try:
            with closing(self.engine.database.connect()) as connection:
                row = connection.execute(
                    "SELECT COUNT(*), COALESCE(MAX(indexed_at), '')"
                    " FROM documents"
                ).fetchone()
            return (int(row[0]), str(row[1]))
        except Exception:
            # A database that cannot answer this is a database that cannot
            # offer suggestions either.
            return (0, "")


    def suggest(self, query: str, *, limit: int | None = None) -> list[Suggestion]:
        """Verified corrections for ``query``, best first, possibly empty."""
        keep = self.max_suggestions if limit is None else limit
        tokens = [
            token for token in tokens_of(query) if len(token) >= MIN_TOKEN_CHARS
        ]
        if not tokens:
            return []
        if all(is_nonsense(token) for token in tokens):
            # Nothing to correct: "zzz no existe" gets no invented advice.
            return []
        vocabulary = self.vocabulary()
        if not vocabulary:
            return []
        known = {fold(term) for term, _count in vocabulary}
        # A typo is a misspelling of something the index contains. A token that
        # is neither indexed nor correctable means the query is not a typo of
        # anything: it is a different kind of input, and advising on it is
        # noise. The first version of this phase suggested for
        # "zzz no existe" because "existe" happened to have a neighbour, and
        # the gate caught it.
        correctable: list[tuple[int, str, list[tuple[str, int]]]] = []
        for index, token in enumerate(tokens):
            if token in known:
                continue
            corrections = correct_token(token, vocabulary)
            if not corrections:
                return []
            correctable.append((index, token, corrections))
        if not correctable:
            return []
        found: list[Suggestion] = []
        for index, token, corrections in correctable:
            for replacement, distance in corrections:
                candidate = list(tokens)
                candidate[index] = replacement
                proposed = " ".join(candidate)
                if proposed == query:
                    continue
                results = len(self.search(proposed, limit=1))
                if not results:
                    # The whole point: a suggestion is a query that worked.
                    continue
                found.append(
                    Suggestion(
                        query=proposed,
                        token=token,
                        replacement=replacement,
                        distance=distance,
                        results=results,
                    )
                )
                break  # the best correction for this token is enough
        found.sort(key=lambda item: (item.distance, -item.results, item.query))
        return found[:keep]


# One suggester per engine, held weakly: the vocabulary cache only pays off if
# it outlives a single call, and the search session (GUI) is what outlives it.
# A one-shot command line search builds a fresh engine, reads the vocabulary
# once and exits, which is the cold cost reported by the evidence gate.
_SUGGESTER_CACHE: "WeakKeyDictionary[object, QuerySuggester]" = WeakKeyDictionary()


def suggester_for(engine) -> QuerySuggester:
    """The suggester belonging to this search engine, created once."""
    existing = _SUGGESTER_CACHE.get(engine)
    if existing is None:
        existing = QuerySuggester(engine)
        _SUGGESTER_CACHE[engine] = existing
    return existing
