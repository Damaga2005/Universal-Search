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
from pathlib import Path

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

    @property
    def normalized_terms(self) -> tuple[str, ...]:
        """The bounded term names, in their deterministic storage order."""
        return tuple(term for term, _ in self.terms)


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """Canonical document metadata plus the signals used by the graph.

    ``DocumentAnalysis`` deliberately remains a small, private-to-intelligence
    record.  The relationship graph needs a few canonical identity fields in
    addition to that analysis, so it consumes this explicit boundary type
    instead of reaching into SQLite rows or changing the analysis contract.
    A record can be made from a provider ``Document`` or from stored indexed
    content; both paths produce the same immutable object.
    """

    document_id: str
    name: str = ""
    path: str = ""
    source: str = "local"
    content_hash: str | None = None
    analysis: DocumentAnalysis | None = None
    content: str | None = None
    language: str | None = None
    title: str | None = None
    headings: tuple[str, ...] = ()
    terms: tuple[tuple[str, int], ...] = ()
    phrases: tuple[str, ...] = ()
    context: str = ""
    references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Fill optional graph fields from the analysis when available."""
        analysis = self.analysis
        if analysis is None and self.content is not None:
            analysis = analyze(self.content, name=self.name)
            object.__setattr__(self, "analysis", analysis)
        if analysis is None:
            return
        if not self.title:
            object.__setattr__(self, "title", analysis.title)
        if not self.headings:
            object.__setattr__(self, "headings", analysis.headings)
        if not self.terms:
            object.__setattr__(self, "terms", analysis.terms)
        if self.language is None:
            object.__setattr__(self, "language", analysis.language)
        if not self.phrases:
            phrases: list[str] = []
            for left, right in analysis.pairs:
                phrase = " ".join((left, right))
                if phrase not in phrases:
                    phrases.append(phrase)
            object.__setattr__(self, "phrases", tuple(phrases[:16]))

    @property
    def id(self) -> str:
        """Short alias used by provider and graph callers."""
        return self.document_id

    @property
    def normalized_terms(self) -> tuple[str, ...]:
        return tuple(term for term, _ in self.terms)

    @property
    def parent_path(self) -> str:
        """Normalized parent directory used for the directory signal."""
        return str(Path(self.path).parent).replace("\\", "/").casefold()

    @classmethod
    def from_document(
        cls,
        document: object,
        *,
        analysis: DocumentAnalysis | None = None,
        context: str = "",
        references: tuple[str, ...] = (),
    ) -> "DocumentRecord":
        """Adapt a domain ``Document`` without importing the provider layer."""
        return cls(
            document_id=str(getattr(document, "id")),
            name=str(getattr(document, "name", "")),
            path=str(getattr(document, "path", "")),
            source=str(getattr(getattr(document, "source", "local"), "value", getattr(document, "source", "local"))),
            content_hash=getattr(document, "content_hash", None),
            analysis=analysis,
            content=getattr(document, "content", None),
            context=context,
            references=references,
        )


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
