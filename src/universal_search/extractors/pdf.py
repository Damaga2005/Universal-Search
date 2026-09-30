"""PDF text extraction backed by pypdf (pure Python, no native dependencies).

Phase 025 hardening: the input byte limit is enforced before the file is
opened; page, character and time limits bound the loop; one broken page
costs a warning, never the whole document; an empty text layer is reported
as ``no_content`` instead of an empty success; encrypted files are rejected
unless the empty user password works (then they carry a warning); and a
cooperative cancel stops the pass with a ``cancelled`` status.
"""

import time
from pathlib import Path

from universal_search.domain.extraction import (
    DEFAULT_LIMITS,
    DocumentStructure,
    ExtractionLimits,
    ExtractionResult,
    ExtractionStatus,
    ResourceUsage,
)
from universal_search.extractors.base import (
    CancelCheck,
    CharBudget,
    check_cancel,
    exceeds_input_limit,
    finalize,
    input_size,
    sanitize_text_entry,
    time_limit_warning,
    timed_out,
)
from universal_search.extractors.text import normalize_text


def _pdf_title(reader) -> str | None:
    try:
        metadata = reader.metadata
    except Exception:  # metadata parsing must never fail the extraction
        return None
    title = getattr(metadata, "title", None) if metadata else None
    if not title:
        return None
    cleaned = sanitize_text_entry(str(title))
    return cleaned or None


def read_pdf(
    path: Path,
    *,
    limits: ExtractionLimits | None = None,
    cancel: CancelCheck | None = None,
) -> ExtractionResult:
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
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - pypdf is a declared dependency
        return finalize(
            started=started,
            input_bytes=size,
            text=None,
            status=ExtractionStatus.ERROR,
            error="pypdf is not installed",
        )

    warnings: list[str] = []
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            if reader.decrypt("") == 0:
                return finalize(
                    started=started,
                    input_bytes=size,
                    text=None,
                    status=ExtractionStatus.ERROR,
                    error="encrypted PDF",
                )
            warnings.append("PDF is encrypted (readable with the empty user password)")
        title = _pdf_title(reader)
        structure = DocumentStructure(title=title) if title else None

        budget = CharBudget(limits.max_chars)
        pages = reader.pages
        total_pages = len(pages)
        visited = 0
        failures = 0
        truncated = False
        cancelled = False
        for index, page in enumerate(pages):
            if index >= limits.max_pages:
                warnings.append(
                    f"stopped at {limits.max_pages} pages of {total_pages}"
                )
                truncated = True
                break
            if check_cancel(cancel):
                warnings.append("extraction cancelled")
                cancelled = True
                break
            if timed_out(started, limits):
                warnings.append(time_limit_warning(limits, "page", index + 1))
                truncated = True
                break
            try:
                page_text = page.extract_text() or ""
            except Exception:
                failures += 1
                warnings.append(f"page {index + 1} extraction failed")
                visited = index + 1
                continue
            visited = index + 1
            if not budget.add(page_text):
                truncated = True
                break
    except Exception as exc:
        return finalize(
            started=started,
            input_bytes=size,
            text=None,
            status=ExtractionStatus.ERROR,
            error=f"{type(exc).__name__}: {exc}",
            warnings=warnings,
        )

    text = normalize_text(budget.text())
    if not text or not text.strip():
        # An empty text layer is "no content", not an empty success.
        text = None
    if cancelled:
        status = ExtractionStatus.CANCELLED
        text = None
    elif truncated:
        status = ExtractionStatus.TRUNCATED
    elif not text:
        status = ExtractionStatus.PARTIAL if failures else ExtractionStatus.NO_CONTENT
        warnings.append("no extractable text")
    elif failures:
        status = ExtractionStatus.PARTIAL
    else:
        status = ExtractionStatus.OK
    usage = ResourceUsage(
        input_bytes=size,
        output_chars=len(text or ""),
        pages=visited,
        elapsed_ms=(time.perf_counter() - started) * 1_000.0,
    )
    return finalize(
        started=started,
        input_bytes=size,
        text=text,
        status=status,
        warnings=warnings,
        truncated=truncated or cancelled,
        structure=structure,
        usage=usage,
    )
