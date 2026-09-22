"""PDF text extraction backed by pypdf (pure Python, no native dependencies)."""

from pathlib import Path

from universal_search.domain.extraction import ExtractionResult
from universal_search.extractors.text import MAX_CONTENT_CHARS, normalize_text


def read_pdf(path: Path) -> ExtractionResult:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - pypdf is a declared dependency
        return ExtractionResult(error="pypdf is not installed")

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            if reader.decrypt("") == 0:
                return ExtractionResult(error="encrypted PDF")
        parts: list[str] = []
        total = 0
        for page in reader.pages:
            try:
                page_text = page.extract_text() or ""
            except Exception:
                continue  # one broken page must not lose the whole document
            parts.append(page_text)
            total += len(page_text)
            if total >= MAX_CONTENT_CHARS:
                break
    except Exception as exc:
        return ExtractionResult(error=f"{type(exc).__name__}: {exc}")
    return ExtractionResult(text=normalize_text("\n".join(parts))[:MAX_CONTENT_CHARS])
