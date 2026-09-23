"""Deterministic language detection by function-word profiles (spec 014).

No model, no API, no statistics file: every supported language contributes
a small, reviewable set of high-frequency function words. The text is
tokenized once and the language with the strongest evidence wins — but
only when it wins *clearly* over the runner-up and the text is long enough
to be evidence at all. Otherwise the answer is ``None`` ("unknown"),
because a coin flip presented as a fact is worse than no label.

The same profiles double as the stop-word list for keyword extraction
(:data:`STOPWORDS`), so "what counts as a function word" is defined once.

Honest limits: this is a coarse label for local discovery, not a language
identifier. A 12-word snippet with no function words returns ``None``; a
Spanish text quoting an English manual may return ``en`` if the English
sample is longer. Both are acceptable for the feature it serves.
"""

from collections import Counter
from collections.abc import Sequence

# Below this many tokens there is no evidence to speak of.
MIN_TOKENS = 12

# The winner must lead the runner-up by this many evidence points, or the
# answer is "unknown" rather than a coin flip.
MIN_MARGIN = 2

# A single repeated function word is capped: twenty "de" in one paragraph
# is one strong signal, not twenty.
MAX_EVIDENCE_PER_WORD = 3

PROFILES: dict[str, tuple[str, ...]] = {
    "es": (
        "de", "la", "el", "los", "las", "un", "una", "en", "y", "que",
        "del", "por", "con", "para", "su", "al", "lo", "como", "mas",
        "pero", "sus", "ya", "este", "esta", "son", "entre", "sobre",
        "todos", "tambien", "muy", "donde", "cuando", "porque", "cada",
        "sin", "hasta", "hay", "ser", "puede", "desde", "nos", "les",
    ),
    "en": (
        "the", "of", "and", "to", "in", "is", "it", "for", "that", "with",
        "was", "on", "as", "by", "at", "from", "or", "this", "be", "are",
        "have", "not", "but", "they", "which", "their", "has", "an", "its",
        "we", "can", "will", "would", "there", "these", "about",
    ),
    "fr": (
        "le", "la", "les", "de", "des", "du", "et", "est", "une", "dans",
        "que", "qui", "pour", "pas", "sur", "plus", "avec", "sont", "nous",
        "vous", "cette", "aux", "leur", "mais", "par", "tout", "bien",
    ),
    "de": (
        "der", "die", "das", "und", "ist", "nicht", "ein", "eine", "mit",
        "den", "von", "sich", "auf", "des", "dem", "für", "auch", "als",
        "wird", "sind", "aber", "durch", "bei", "nach", "über", "oder",
    ),
    "pt": (
        "de", "que", "não", "para", "com", "uma", "os", "as", "do", "da",
        "no", "na", "por", "mais", "como", "mas", "foi", "ao", "ele",
        "das", "dos", "seu", "sua", "ou", "quando", "muito", "também",
    ),
    "it": (
        "il", "lo", "la", "gli", "che", "di", "del", "della", "per", "con",
        "non", "una", "sono", "come", "più", "anche", "nel", "alla", "da",
        "si", "ma", "questo", "nella", "suo", "loro", "essere", "hanno",
    ),
}

# One definition of "function word" for both detection and keyword
# extraction, so the two features can never disagree about a stop word.
STOPWORDS: frozenset[str] = frozenset().union(*PROFILES.values())

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(sorted(PROFILES))


def score(tokens: Sequence[str]) -> dict[str, int]:
    """Evidence points per language for one token sequence."""
    counts = Counter(tokens)
    return {
        language: sum(
            min(counts[word], MAX_EVIDENCE_PER_WORD) for word in words
        )
        for language, words in PROFILES.items()
    }


def detect(tokens: Sequence[str]) -> str | None:
    """Best-supported language, or ``None`` when the evidence is thin."""
    if len(tokens) < MIN_TOKENS:
        return None
    ranked = sorted(score(tokens).items(), key=lambda item: (-item[1], item[0]))
    best_language, best_points = ranked[0]
    if best_points == 0 or best_points - ranked[1][1] < MIN_MARGIN:
        return None
    return best_language
