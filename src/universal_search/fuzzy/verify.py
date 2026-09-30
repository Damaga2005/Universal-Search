"""Phase 031, part 2: the verification step, which is where truth lives.

The blocking step proposes. This module disposes.

A document survives only when its **real text** satisfies, for every token of
the query, one of:

* it contains the token (exact, in the name or the text) — the strongest rule;
* one of its words *starts with* the token — a prefix such as ``transisto``
  for ``transistor``;
* one of its words is within a bounded Damerau-Levenshtein distance of the
  token — a typo such as ``transsistor``.

The consequence is the property the phase needs: a document the filter loves
but the verifier rejects is never returned. Distance is computed with an early
exit at the budget, because this runs per token per candidate and the contract
is "at most this many edits", not "the exact distance".
"""

from __future__ import annotations

import re

from universal_search.fuzzy.trigrams import (
    MIN_TOKEN_CHARS,
    WORD_RE,
    fold,
)


# Characters read per candidate. The verifier is the only part of the layer
# that touches text, so it is the part that gets a budget.
MAX_VERIFY_CHARS = 4_000


def damerau_levenshtein(left: str, right: str, *, cap: int) -> int:
    """Damerau-Levenshtein distance, abandoned as soon as it exceeds ``cap``.

    The transposition case is included because ``transsistor`` (letters swapped)
    is the single most common typo shape, and plain Levenshtein scores it as
    two edits, which would push a 7-character word over its budget of one.
    """
    if left == right:
        return 0
    if cap < 0:
        return cap + 1
    if abs(len(left) - len(right)) > cap:
        return cap + 1
    previous_previous: list[int] = []
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        best = current[0]
        for j, right_char in enumerate(right, 1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            substitute = previous[j - 1] + (left_char != right_char)
            value = min(insert, delete, substitute)
            if (
                i > 1 and j > 1
                and left_char == right[j - 2] and left[i - 2] == right_char
            ):
                value = min(value, previous_previous[j - 2] + 1)
            current.append(value)
            if value < best:
                best = value
        if best > cap:
            return cap + 1
        previous_previous, previous = previous, current
    return previous[-1]


def edit_budget(token: str) -> int:
    """How many edits a token may be away and still count as the same word.

    Four to seven characters get one edit, eight or more get two, and anything
    below four characters gets none: at that length every short word is one
    keystroke from a different short word, so a "match" there is noise rather
    than a correction.
    """
    length = len(token)
    if length < 4:
        return 0
    if length <= 7:
        return 1
    return 2


def resolve_token(
    token: str,
    name: str,
    text: str,
    *,
    max_chars: int = MAX_VERIFY_CHARS,
) -> tuple[bool, str, int]:
    """Does the document really contain this token?

    Returns ``(resolved, rule, distance)``. The rule is one of ``exact-name``,
    ``exact-text``, ``substring`` or ``edit``; the distance is the edit
    distance for the ``edit`` rule and 0 otherwise.

    The order is cheapest rule first *and* strongest rule first: an exact
    match is already a perfect answer, so computing distances after it would
    be wasted work.
    """
    return resolve_token_folded(
        fold(token), fold(name), fold(text), max_chars=max_chars
    )


def resolve_token_folded(
    folded_token: str,
    folded_name: str,
    folded_text: str,
    *,
    max_chars: int = MAX_VERIFY_CHARS,
) -> tuple[bool, str, int]:
    """The decision, for text that is already folded.

    Accent folding is not free: it normalises every character. Doing it once
    per (candidate, token) rather than once per candidate was measured by the
    phase-031 latency gate as the last big cost of the layer, so the engine
    folds each candidate once and calls this.
    """
    if len(folded_token) < MIN_TOKEN_CHARS:
        return False, "too-short", 0

    bounded_text = folded_text[:max_chars]

    if folded_token in folded_name:
        return True, "exact-name", 0
    if folded_token in bounded_text:
        return True, "exact-text", 0

    # Prefix: the token is the beginning of a word in the document.
    for word in WORD_RE.findall(bounded_text):
        if len(word) > len(folded_token) and word.startswith(folded_token):
            return True, "substring", 0
    for word in WORD_RE.findall(folded_name):
        if len(word) > len(folded_token) and word.startswith(folded_token):
            return True, "substring", 0

    budget = edit_budget(folded_token)
    if budget == 0:
        return False, "no-budget", 0
    # Cheap, exact pre-filter before any dynamic programming.
    #
    # A word within ``budget`` edits of the token must share at least
    # ``len(token) - budget`` characters with it *as a multiset*, because an
    # edit can remove or change at most one character. So requiring that
    # overlap is a superset filter, not a heuristic: it cannot discard a real
    # match, and it skips the overwhelming majority of the words in a
    # 4 000-character window. The phase-031 latency gate measured the
    # unfiltered version at +37 ms p95, which was all of this loop.
    needed = max(1, len(folded_token) - budget)
    best = budget + 1
    for word in WORD_RE.findall(bounded_text):
        if abs(len(word) - len(folded_token)) > budget:
            continue
        if _char_overlap(word, folded_token) < needed:
            continue
        distance = damerau_levenshtein(folded_token, word, cap=budget)
        if distance < best:
            best = distance
            if best == 1:
                break
    if best <= budget:
        return True, "edit", best
    return False, "no-match", best


def _char_overlap(left: str, right: str) -> int:
    """How many characters the two words have in common, as a multiset."""
    counts: dict[str, int] = {}
    for char in left:
        counts[char] = counts.get(char, 0) + 1
    shared = 0
    for char in right:
        if counts.get(char, 0) > 0:
            counts[char] -= 1
            shared += 1
    return shared


def resolve_query(
    query: str,
    name: str,
    text: str,
    *,
    max_chars: int = MAX_VERIFY_CHARS,
) -> tuple[bool, tuple[tuple[str, str, int], ...]]:
    """Every token of the query must be resolved for the document to match.

    A multi-word query is an intersection: if the user typed three words and
    only one is in the document, the document is not an answer. The per-token
    evidence is returned so the caller can explain which token matched and by
    which rule.
    """
    tokens = [
        token
        for token in WORD_RE.findall(fold(query))
        if len(token) >= MIN_TOKEN_CHARS
    ]
    return resolve_tokens(
        tokens, fold(name), fold(text), max_chars=max_chars
    )


def resolve_tokens(
    tokens: list[str],
    folded_name: str,
    folded_text: str,
    *,
    max_chars: int = MAX_VERIFY_CHARS,
) -> tuple[bool, tuple[tuple[str, str, int], ...]]:
    """:func:`resolve_query` for tokens and text that are already folded.

    The engine calls this so that accent folding happens once per candidate
    instead of once per (candidate, token).
    """
    if not tokens:
        return False, ()
    evidence: list[tuple[str, str, int]] = []
    for token in tokens:
        resolved, rule, distance = resolve_token_folded(
            token, folded_name, folded_text, max_chars=max_chars
        )
        if not resolved:
            return False, ()
        evidence.append((token, rule, distance))
    return True, tuple(evidence)


def is_nonsense(token: str) -> bool:
    """A cheap guard used by the caller before spending a verification.

    A token with no vowel at all, or one that is a single repeated character,
    is not a word anyone typed. The project already asserts that nonsense
    queries retrieve nothing; this keeps that cheap.
    """
    folded = fold(token)
    if len(folded) < 3:
        return True
    if len(set(folded)) == 1:
        return True
    return not re.search(r"[aeiou]", folded)
