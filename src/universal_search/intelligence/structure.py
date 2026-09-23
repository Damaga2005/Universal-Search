"""Title, headings and section boundaries from plain text (spec 014).

Pure line inspection, no model and no layout engine: markdown headings,
numbered sections, ALL-CAPS lines and Title Case lines are the four
patterns a text-only extractor can recognise with confidence. Anything
that looks like an ordinary sentence is not promoted to a heading — a
document with no structure must report no structure, not an invented one.

Everything is bounded (``MAX_HEADINGS``) and order-preserving, and
deduplication keeps the first occurrence so the result is stable for the
same bytes.
"""

import re
from collections.abc import Sequence

# A heading is a short label, not a sentence.
MAX_HEADING_CHARS = 120
MAX_HEADINGS = 32

MARKDOWN_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
NUMBERED_RE = re.compile(r"^\s{0,3}\d+(?:\.\d+)*[.)]?\s+(\S.*)$")
UPPERCASE_RE = re.compile(r"^[A-ZÁÉÍÓÚÜÑ0-9][A-ZÁÉÍÓÚÜÑ0-9 \-:,'()/&]{2,}$")
TITLE_CASE_RE = re.compile(
    r"^[A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ'’-]*(?:\s+[A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ'’-]*)+$"
)


def _looks_like_heading(line: str) -> str | None:
    """Return the heading text of ``line``, or ``None`` if it is prose."""
    stripped = line.strip()
    if not stripped or len(stripped) > MAX_HEADING_CHARS:
        return None
    match = MARKDOWN_RE.match(stripped)
    if match:
        return match.group(1).strip()
    match = NUMBERED_RE.match(stripped)
    if match:
        candidate = match.group(1).strip()
        # "1. Introduction." is a heading; "1. The model was invented in
        # 1947 by Bardeen." is a sentence that happens to start with a
        # number, and a 120-character line is the cut-off.
        if len(candidate) <= 90 and not candidate.endswith((".", ",", ";")):
            return candidate
        return None
    if UPPERCASE_RE.match(stripped) and any(
        character.isalpha() for character in stripped
    ):
        return stripped
    if (
        TITLE_CASE_RE.match(stripped)
        and len(stripped.split()) >= 2
        and not stripped.endswith(".")
    ):
        return stripped
    return None


def headings(lines: Sequence[str]) -> tuple[str, ...]:
    """Detected headings, deduplicated, in document order."""
    found: list[str] = []
    seen: set[str] = set()
    for line in lines:
        heading = _looks_like_heading(line)
        if heading is None or heading in seen:
            continue
        seen.add(heading)
        found.append(heading)
        if len(found) == MAX_HEADINGS:
            break
    return tuple(found)


def _from_name(name: str) -> str | None:
    """Filename stem as a last-resort title (``informe_final.md``)."""
    cleaned = name.replace("_", " ").replace("-", " ").strip()
    cleaned = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", cleaned).strip()
    if not cleaned or len(cleaned) > MAX_HEADING_CHARS:
        return None
    return cleaned or None


def title(lines: Sequence[str], *, name: str = "") -> str | None:
    """First heading, else a short leading line, else the file stem."""
    for line in lines:
        heading = _looks_like_heading(line)
        if heading is not None:
            return heading
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) <= MAX_HEADING_CHARS and not stripped.endswith("."):
            return stripped
        break
    return _from_name(name)


def count_sections(lines: Sequence[str], found: Sequence[str]) -> int:
    """Section count: one per heading, or one implicit section for prose."""
    if found:
        return len(found)
    if any(line.strip() for line in lines):
        return 1
    return 0
