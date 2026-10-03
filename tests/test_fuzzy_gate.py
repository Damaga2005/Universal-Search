"""The fuzzy gate's own labels, so widening one cannot hide a regression.

Phase 045 grew the corpus from 27 documents to 39 and the fuzzy gate's
``ROBUST_QUERIES`` quietly stopped meaning what it said. Its expectation was a
*string* matched with ``in`` against a corpus id, so ``"bjt"`` had always meant
"an id that starts with bjt-" -- an accident of naming, not a statement about
which documents answer a typo'd "transisto". The corpus grew a document whose
body reads "el transistor bipolar" and whose file name is ``T6_BJT_Apuntes.md``;
the fuzzy layer returns it first, which is *correct*, and the gate called it a
miss. T1 fell to 6/10.

The fix was to make the expectation an explicit set of ids. That is only
honest while the ids in it are defensible, which is what this file checks:

* every id named is a document the corpus actually contains;
* every id named genuinely answers the query, by its own text or name -- so a
  future corpus addition cannot widen the gate for free;
* the known-unreachable query is *declared*, not deleted;
* and the measured recall is still asserted, so "the label got wider" is
  visible as a number rather than as a silent pass.

The gate itself is not re-run here: it builds a full index and measures index
growth and added latency, which belongs to the gate and to `evaluation`.
"""

from __future__ import annotations

import pytest

from evaluation import corpus as corpus_module
from evaluation import fuzzy_gate


def _document(identifier: str):
    for document in corpus_module.DOCUMENTS:
        if document.id == identifier:
            return document
    raise AssertionError(f"the gate names {identifier!r}, which is not in the corpus")


def test_every_expected_id_exists_in_the_corpus():
    """A label naming a document that no longer exists would pass by accident."""
    for query, expected in fuzzy_gate.ROBUST_QUERIES:
        for identifier in expected:
            _document(identifier)


#: The layer's documented budget, and the reason OUT_OF_SCOPE exists at all.
TYPO_BUDGET = 2


def _edit_distance(left: str, right: str, *, cap: int = TYPO_BUDGET) -> int:
    """Levenshtein distance, abandoned as soon as it exceeds ``cap``.

    Bounded because the question is never "how far apart" but "is this within
    the budget", and a corpus document has hundreds of words to check.
    """
    if abs(len(left) - len(right)) > cap:
        return cap + 1
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, start=1):
        current = [i]
        for j, b in enumerate(right, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (a != b),
            ))
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def _document_words(document) -> list[str]:
    haystack = f"{document.path} {document.content or ''}"
    return [
        word for word in "".join(
            ch if ch.isalnum() else " " for ch in haystack.casefold()
        ).split()
        if len(word) > 2
    ]


def _unreachable_word(query: str, document) -> tuple[int, str] | None:
    """The first query word the document cannot answer, or ``None``.

    Per word, because a query can be several words and the answer can span
    them: "eberts moll" is answered by a file called ``ebers_moll.md`` only if
    *each* half is close to *some* word. Comparing the joined string against a
    single word was the second version of this check and it rejected a
    correct answer for the same reason it accepted a wrong one -- it never
    asked the question the user asked.
    """
    words = _document_words(document)
    for needle in query.casefold().split():
        best = (TYPO_BUDGET + 1, "")
        for word in words:
            distance = _edit_distance(needle, word)
            if distance < best[0]:
                best = (distance, word)
            if best[0] == 0:
                break
        if best[0] > TYPO_BUDGET:
            return best
    return None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (q, e) for q, e in fuzzy_gate.ROBUST_QUERIES
        if q not in dict(fuzzy_gate.OUT_OF_SCOPE)
    ],
)
def test_every_expected_id_really_answers_the_query(query, expected):
    """The load-bearing check: the label must be defensible, not convenient.

    Every document the gate will accept must contain a word within the layer's
    own typo budget of the query. That is the contract the layer was designed
    around, so a label that violates it would let the gate pass a layer that
    had stopped working.

    Character-trigram overlap was the first version of this check and it was
    the wrong measure: "azarfan" shares exactly one trigram with the recipe's
    "azafran" and the layer answers it anyway, because a transposition is
    inside a budget that trigrams cannot see.
    """
    for identifier in expected:
        document = _document(identifier)
        unreachable = _unreachable_word(query, document)
        assert unreachable is None, (
            f"{identifier!r} cannot answer {query!r}: {unreachable[0]} edits "
            f"from every word in {document.path!r} (nearest: "
            f"{unreachable[1]!r})"
        )


def test_the_declared_exclusions_really_are_unreachable():
    """The documented failures have to be failures, not excuses.

    ``azaarfann`` is more than the budget away from every word in the recipe,
    which is what "beyond the design's reach" means as a number. If that ever
    stops being true, the exclusion is stale and should be withdrawn.
    """
    paella = _document("paella")
    unreachable = _unreachable_word("azaarfann", paella)
    assert unreachable is not None, (
        "azaarfann is now within the budget; withdraw it from OUT_OF_SCOPE "
        "and let the gate measure it"
    )


def test_the_expectation_is_a_set_not_a_substring():
    """The defect itself, pinned so it cannot come back.

    A bare string compared with ``in`` is how "some BJT document" turned into
    "an id that starts with bjt-".
    """
    for _query, expected in fuzzy_gate.ROBUST_QUERIES:
        assert isinstance(expected, tuple)
        assert len(expected) >= 1


def test_the_unreachable_query_is_declared_not_deleted():
    """`azaarfann` for `paella` is a real miss. It stays visible."""
    declared = dict(fuzzy_gate.OUT_OF_SCOPE)
    assert "azaarfann" in declared
    assert "azaarfann" in [q for q, _ in fuzzy_gate.ROBUST_QUERIES], (
        "the known-failing query must keep being measured, not dropped"
    )
    # Every exclusion carries a reason, because an unexplained exclusion is
    # indistinguishable from a moved goalpost.
    for query, reason in fuzzy_gate.OUT_OF_SCOPE:
        assert reason.strip(), query
        assert len(reason) > 20, f"{query!r} has no real justification"


def test_the_gate_still_measures_ten_robust_queries():
    assert len(fuzzy_gate.ROBUST_QUERIES) == 10
    assert fuzzy_gate.THRESHOLDS["T1_recall5_robust_queries"] == 0.80, (
        "the threshold was not lowered when the label was widened"
    )


def test_nonsense_queries_still_must_return_nothing():
    assert len(fuzzy_gate.MUST_BE_EMPTY) >= 3