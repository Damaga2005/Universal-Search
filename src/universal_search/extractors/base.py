"""Shared enforcement for every extractor (phase 025).

One place for the resource rules so no unbounded read exists anywhere:

* :class:`CharBudget` accumulates text while never holding more than the
  character limit plus one part in memory.
* :func:`read_member_bounded` streams a ZIP member in chunks and stops at
  the per-part byte limit, so a lying central directory cannot make the
  extractor materialize a decompression bomb.
* :func:`member_problem` rejects traversal names, oversized parts and
  excessive expansion ratios from the header alone, before reading.
* :func:`has_entity_declaration` rejects DTD entity expansion (billion
  laughs) before the XML parser ever sees the bytes.
* :func:`finalize` stamps every result with sanitized warnings, a status
  and a :class:`ResourceUsage` measurement.

Cancellation is cooperative: extractors accept a ``cancel`` callback and
check it between units of work (pages, sheets, slides).
"""

import re
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

from universal_search.domain.extraction import (
    DEFAULT_LIMITS,
    MAX_STRUCTURE_CHARS,
    MAX_STRUCTURE_ENTRIES,
    DocumentStructure,
    ExtractionLimits,
    ExtractionResult,
    ExtractionStatus,
    ResourceUsage,
)

# A cancel callback returns True when the caller wants the extraction to
# stop; extractors then return what they have with status CANCELLED.
CancelCheck = Callable[[], bool]

# Warnings are human-readable diagnostics: bounded in count and length and
# stripped of control characters so they can be logged, stored in SQLite
# and printed without corrupting terminals.
MAX_WARNINGS = 20
WARNING_MAX_CHARS = 300

_ENTITY_DECL = re.compile(rb"<!ENTITY", re.IGNORECASE)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_warning(message: str) -> str:
    """Make one warning safe to store, log and print."""
    cleaned = _CONTROL_CHARS.sub(" ", str(message)).strip()
    return cleaned[:WARNING_MAX_CHARS]


def sanitize_warnings(messages: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Bounded, sanitized warning tuple (deterministic order preserved)."""
    return tuple(sanitize_warning(message) for message in messages[:MAX_WARNINGS])


def sanitize_text_entry(text: str) -> str:
    """One structure entry: single line, bounded length."""
    return _CONTROL_CHARS.sub(" ", text).strip()[:MAX_STRUCTURE_CHARS]


def check_cancel(cancel: CancelCheck | None) -> bool:
    return cancel is not None and cancel()


def timed_out(started: float, limits: ExtractionLimits) -> bool:
    """True when the time budget is spent (``max_seconds = 0`` disables)."""
    return limits.max_seconds > 0 and (time.perf_counter() - started) > limits.max_seconds


class CharBudget:
    """Accumulates extracted text while bounding resident memory.

    The head of the document is kept (matching the historical truncation
    behavior); once the budget is spent the last part is trimmed and
    :meth:`add` returns False so callers stop reading.

    ``exhausted`` means the budget is full (``total >= limit``) and is used
    to stop reading; ``cut`` means text was actually dropped because it did
    not fit, and is the correct signal for flagging the result truncated —
    a document that exactly fills the budget is complete, not truncated.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(0, int(limit))
        self.parts: list[str] = []
        self.total = 0
        self.cut = False

    def add(self, text: str) -> bool:
        """Add ``text``; returns False when the budget is now exhausted."""
        if not text:
            return self.total < self.limit
        self.parts.append(text)
        self.total += len(text)
        if self.total > self.limit:
            self.cut = True
            overflow = self.total - self.limit
            last = self.parts[-1]
            keep = len(last) - overflow
            if keep > 0:
                self.parts[-1] = last[:keep]
            else:
                self.parts.pop()
            self.total = self.limit
            return False
        return True

    @property
    def exhausted(self) -> bool:
        return self.total >= self.limit

    def text(self) -> str:
        return "".join(self.parts)


def input_size(path: Path) -> int | None:
    """File size in bytes, or None when the path cannot be stated."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def exceeds_input_limit(path: Path, limits: ExtractionLimits) -> str | None:
    """Reason the input is too large to touch, or None when acceptable."""
    size = input_size(path)
    if size is None:
        return "input is not readable"
    if size > limits.max_input_bytes:
        return (
            f"input exceeds the {limits.max_input_bytes}-byte limit "
            f"({size} bytes)"
        )
    return None


def finalize(
    *,
    started: float,
    input_bytes: int,
    text: str | None,
    status: str = ExtractionStatus.OK,
    warnings: tuple[str, ...] = (),
    truncated: bool = False,
    structure: DocumentStructure | None = None,
    error: str | None = None,
    usage: ResourceUsage | None = None,
) -> ExtractionResult:
    """Build the versioned result every extractor returns.

    An error is always mirrored into the warnings: the indexer persists
    status + warnings, so the reason a document failed must survive there
    too, or diagnostics could report "error" without ever saying why.
    """
    if usage is None:
        usage = ResourceUsage(
            input_bytes=input_bytes,
            output_chars=len(text or ""),
            elapsed_ms=(time.perf_counter() - started) * 1_000.0,
        )
    all_warnings = [*warnings]
    if error is not None:
        all_warnings.append(error)
    return ExtractionResult(
        text=text,
        error=error,
        status=status,
        warnings=sanitize_warnings(all_warnings),
        structure=structure,
        resource_usage=usage,
        truncated=truncated,
    )


# -- ZIP member safety ---------------------------------------------------------

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


def member_problem(info: zipfile.ZipInfo, limits: ExtractionLimits) -> str | None:
    """Why this member must not be read, or None when it looks safe.

    Rejection happens from the central-directory header alone: traversal
    names, declared sizes above the per-part cap and implausible expansion
    ratios never reach the reader.
    """
    name = info.filename
    if (
        not name
        or "\x00" in name
        or name.startswith("/")
        or _DRIVE_LETTER.match(name)
        or any(part in ("", ".", "..") for part in name.split("/"))
    ):
        return f"unsafe member name: {name!r}"
    if info.file_size > limits.max_part_bytes:
        return (
            f"part {name!r} exceeds the {limits.max_part_bytes}-byte "
            f"part limit (declared {info.file_size} bytes)"
        )
    if (
        limits.max_expansion_ratio > 0
        and info.compress_size > 0
        and info.file_size / info.compress_size > limits.max_expansion_ratio
    ):
        return (
            f"part {name!r} exceeds the {limits.max_expansion_ratio}x "
            f"expansion limit ({info.file_size} bytes from "
            f"{info.compress_size} compressed)"
        )
    return None


def read_member_bounded(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """Read one member in chunks, stopping at ``limit`` bytes.

    Raises :class:`ValueError` when the member is larger than the limit;
    the claim in the central directory is not trusted.
    """
    chunks: list[bytes] = []
    total = 0
    with archive.open(name) as handle:
        while total <= limit:
            chunk = handle.read(min(1024 * 1024, limit - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    if total > limit:
        raise ValueError(
            f"part {name!r} exceeds the {limit}-byte part limit "
            f"({total} bytes read)"
        )
    return b"".join(chunks)


def has_entity_declaration(data: bytes) -> bool:
    """True when the XML carries a DTD entity declaration.

    A DOCTYPE must precede the root element, so scanning the head of the
    document is complete; anything after the root is ignored by the parser
    anyway.
    """
    return bool(_ENTITY_DECL.search(data[:65536]))


def parse_xml_part(data: bytes, part_name: str) -> ElementTree.Element:
    """Parse one XML part, rejecting entities and malformed input."""
    if has_entity_declaration(data):
        raise ValueError(f"DTD entities are not allowed in {part_name}")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ValueError(f"malformed XML in {part_name}: {exc}") from None


def time_limit_warning(limits: ExtractionLimits, unit: str, position: int) -> str:
    """Deterministic 'time limit' warning shared by every extractor."""
    return f"time limit reached after {limits.max_seconds}s at {unit} {position}"


def structure_entries(values: list[str]) -> tuple[str, ...]:
    """Bounded, sanitized structure entries in first-seen order."""
    seen: set[str] = set()
    entries: list[str] = []
    for value in values:
        cleaned = sanitize_text_entry(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            entries.append(cleaned)
        if len(entries) >= MAX_STRUCTURE_ENTRIES:
            break
    return tuple(entries)


__all__ = [
    "CancelCheck",
    "CharBudget",
    "DEFAULT_LIMITS",
    "MAX_WARNINGS",
    "WARNING_MAX_CHARS",
    "check_cancel",
    "exceeds_input_limit",
    "finalize",
    "has_entity_declaration",
    "input_size",
    "member_problem",
    "parse_xml_part",
    "read_member_bounded",
    "sanitize_text_entry",
    "sanitize_warning",
    "sanitize_warnings",
    "structure_entries",
    "time_limit_warning",
    "timed_out",
]
