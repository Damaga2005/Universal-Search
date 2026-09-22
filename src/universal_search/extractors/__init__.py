"""Extractor registry: file extension -> content extraction.

The provider discovers files; this package decides whether and how their
content can be read. Unsupported formats return no text and are never opened,
so binaries are never interpreted as UTF-8.
"""

from collections.abc import Callable
from pathlib import Path

from universal_search.domain.extraction import ExtractionResult
from universal_search.extractors.office import read_docx, read_pptx, read_xlsx
from universal_search.extractors.pdf import read_pdf
from universal_search.extractors.text import (
    MAX_CONTENT_CHARS,
    TEXT_EXTENSIONS,
    is_text_extension,
    normalize_text,
    read_text,
)

ExtractFunction = Callable[[Path], ExtractionResult]

EXTRACTORS: dict[str, ExtractFunction] = {
    extension: read_text for extension in TEXT_EXTENSIONS
}
EXTRACTORS.update({
    ".pdf": read_pdf,
    ".docx": read_docx,
    ".xlsx": read_xlsx,
    ".xlsm": read_xlsx,
    ".pptx": read_pptx,
})

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(EXTRACTORS)


def supports(extension: str) -> bool:
    return extension.lower() in EXTRACTORS


def extract(path: Path) -> ExtractionResult:
    """Extract text for ``path``, never raising for broken files."""
    extractor = EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        return ExtractionResult()  # unsupported: no text, not an error
    try:
        return extractor(path)
    except Exception as exc:  # a broken extractor must not stop an indexing run
        return ExtractionResult(error=f"{type(exc).__name__}: {exc}")


__all__ = [
    "EXTRACTORS",
    "MAX_CONTENT_CHARS",
    "SUPPORTED_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "ExtractionResult",
    "extract",
    "is_text_extension",
    "normalize_text",
    "supports",
]
