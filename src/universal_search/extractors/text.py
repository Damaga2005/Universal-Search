"""Plain-text extraction for text formats and source code."""

import re
import time
from pathlib import Path

from universal_search.domain.extraction import (
    DEFAULT_LIMITS,
    DocumentStructure,
    ExtractionLimits,
    ExtractionResult,
    ExtractionStatus,
)
from universal_search.extractors.base import (
    CancelCheck,
    CharBudget,
    check_cancel,
    exceeds_input_limit,
    finalize,
    input_size,
    structure_entries,
)


TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp",
    ".hpp", ".cs", ".go", ".rs", ".sql", ".yaml", ".yml", ".toml",
    ".ini", ".log",
}

# Upper bound for extracted text, in characters, so a single file can never
# cause uncontrolled memory growth in the indexer or the search engine.
MAX_CONTENT_CHARS = 2_000_000

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def normalize_text(text: str) -> str:
    """Remove characters that would corrupt storage or display."""
    return text.replace("\x00", "").replace("\ufeff", "")


def is_text_extension(extension: str) -> bool:
    return extension.lower() in TEXT_EXTENSIONS


def markdown_headings(text: str) -> tuple[str, ...]:
    """Markdown ``#`` headings, bounded and in document order."""
    return structure_entries([match.group(2) for match in _HEADING.finditer(text)])


def read_text(
    path: Path,
    *,
    limits: ExtractionLimits | None = None,
    cancel: CancelCheck | None = None,
) -> ExtractionResult:
    """Read a text file with bounded size, or report why it failed.

    Behavior is unchanged for ordinary files: UTF-8 with a BOM skip and
    replacement for undecodable bytes, NUL and BOM stripped, output cut at
    ``max_chars``. What is new (phase 025) is that the cut is *visible* —
    the result is marked truncated with a warning — and that the input is
    rejected up front when it exceeds the byte limit.
    """
    limits = limits or DEFAULT_LIMITS
    started = time.perf_counter()
    size = input_size(path) or 0
    over_limit = exceeds_input_limit(path, limits)
    if over_limit is not None:
        return finalize(
            started=started,
            input_bytes=size,
            text=None,
            status=ExtractionStatus.ERROR,
            error=over_limit,
        )
    if check_cancel(cancel):
        return finalize(
            started=started,
            input_bytes=size,
            text=None,
            status=ExtractionStatus.CANCELLED,
            truncated=True,
            warnings=("extraction cancelled",),
        )
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            # Read one character past the cap so an over-long file is
            # detectable as truncation instead of a silent cut.
            raw = handle.read(limits.max_chars + 1)
    except OSError as exc:
        return finalize(
            started=started,
            input_bytes=size,
            text=None,
            status=ExtractionStatus.ERROR,
            error=f"{type(exc).__name__}: {exc}",
        )
    budget = CharBudget(limits.max_chars)
    budget.add(raw)
    text = normalize_text(budget.text())
    warnings: list[str] = []
    status = ExtractionStatus.OK
    truncated = False
    if len(raw) > limits.max_chars:
        warnings.append(
            f"truncated at {limits.max_chars} characters "
            f"({len(raw)} read)"
        )
        status = ExtractionStatus.TRUNCATED
        truncated = True
    structure = None
    if path.suffix.lower() == ".md":
        headings = markdown_headings(text)
        if headings:
            structure = DocumentStructure(headings=headings)
    return finalize(
        started=started,
        input_bytes=size,
        text=text,
        status=status,
        warnings=warnings,
        truncated=truncated,
        structure=structure,
    )
