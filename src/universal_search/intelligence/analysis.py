"""The analysis pipeline: bytes in, one bounded record out (spec 014).

``analyze`` is pure and deterministic: the same content always yields the
same :class:`DocumentAnalysis`, with no clock, no randomness and no
dependence on the filesystem. That property is what makes the derived data
rebuildable and comparable across versions.

Defensive by construction, because extractor output is not to be trusted:
``None``, empty, whitespace-only, NUL-poisoned and multi-megabyte inputs
all produce a valid, bounded record instead of an exception.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from universal_search.index.ranking import tokens
from universal_search.intelligence import keywords as keywords_module
from universal_search.intelligence import language as language_module
from universal_search.intelligence import structure

# Bumped whenever the meaning of the stored fields changes. Rows written by
# an older version are recognised and recomputed by a rebuild, so the
# derived data can be invalidated as a whole.
INTELLIGENCE_VERSION = 1

# Per-document work is bounded: a very long document is sampled from both
# ends (headings live at the top, conclusions at the bottom) instead of
# being tokenized end to end. Deterministic, and far cheaper than 2 MB of
# text per document.
ANALYZED_CHAR_LIMIT = 200_000
HEAD_SHARE = 0.75


@dataclass(frozen=True, slots=True)
class DocumentAnalysis:
    """Everything derived from one document's text, already bounded."""

    version: int
    language: str | None
    title: str | None
    headings: tuple[str, ...]
    sections: int
    terms: tuple[tuple[str, int], ...]
    pairs: tuple[tuple[str, str], ...]
    analyzed_chars: int
    truncated: bool

    @property
    def keywords(self) -> tuple[str, ...]:
        """Public names of the bounded term vector, most frequent first."""
        return tuple(term for term, _ in self.terms)

    @property
    def is_empty(self) -> bool:
        """Nothing worth storing: no text, no terms, no structure."""
        return not self.terms and not self.headings and self.sections == 0


def _sample(text: str) -> tuple[str, bool, int]:
    """Bound the work: head + tail for very long documents.

    Returns the sampled text, whether truncation happened, and how many
    source characters it covers (the join newline is not source text).
    """
    if len(text) <= ANALYZED_CHAR_LIMIT:
        return text, False, len(text)
    head = int(ANALYZED_CHAR_LIMIT * HEAD_SHARE)
    tail = ANALYZED_CHAR_LIMIT - head
    return f"{text[:head]}\n{text[-tail:]}", True, head + tail


def _empty(name: str = "") -> DocumentAnalysis:
    return DocumentAnalysis(
        version=INTELLIGENCE_VERSION,
        language=None,
        title=structure._from_name(name),
        headings=(),
        sections=0,
        terms=(),
        pairs=(),
        analyzed_chars=0,
        truncated=False,
    )


def analyze(text: str | None, *, name: str = "") -> DocumentAnalysis:
    """Derive language, structure and terms from one document's text.

    ``text`` is whatever the extractor produced: ``None`` for a binary the
    extractors cannot read, possibly NUL-poisoned after a failed parse, and
    up to the extractor's 2 MB cap.
    """
    if not text:
        return _empty(name)
    # A failed extraction can leave control characters behind; the ranking
    # tokenizer handles them as separators, and the language profiles never
    # contain them, so no extra sanitising is needed beyond dropping NULs
    # that would confuse the line splitter.
    cleaned = text.replace("\x00", " ")
    sampled, truncated, consumed = _sample(cleaned)
    if not sampled.strip():
        return _empty(name)

    lines = sampled.splitlines()
    words = tokens(sampled.casefold())
    found = structure.headings(lines)
    terms, pairs = keywords_module.analyze_words(sampled)
    return DocumentAnalysis(
        version=INTELLIGENCE_VERSION,
        language=language_module.detect(words),
        title=structure.title(lines, name=name),
        headings=found,
        sections=structure.count_sections(lines, found),
        terms=terms,
        pairs=pairs,
        analyzed_chars=consumed,
        truncated=truncated,
    )


def summarize(analysis: DocumentAnalysis) -> Sequence[str]:
    """Short human-readable lines for ``intelligence show``."""
    lines = [
        f"language:   {analysis.language or 'unknown'}",
        f"title:      {analysis.title or '-'}",
        f"sections:   {analysis.sections}",
        f"headings:   {len(analysis.headings)}",
        f"keywords:   {', '.join(analysis.keywords) or '-'}",
        f"co-ocurr:   {len(analysis.pairs)} pairs",
        f"analyzed:   {analysis.analyzed_chars} chars"
        + (" (truncated)" if analysis.truncated else ""),
        f"version:    {analysis.version}",
    ]
    return lines
