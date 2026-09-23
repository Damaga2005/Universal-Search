"""Extractor registry: file extension -> content extraction.

The provider discovers files; this package decides whether and how their
content can be read. Unsupported formats return no text and are never opened,
so binaries are never interpreted as UTF-8.
"""

from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class ExtractorInfo:
    """One registered extractor, inspectable (spec 019).

    The registry is deliberately plain data: an extractor is an extension
    plus a function plus a character limit, and `extract()` is the only
    way content is produced. That makes adding a format a one-line change
    and makes "what can this application read?" answerable without
    reading the code.
    """

    key: str
    extensions: tuple[str, ...]
    max_chars: int
    binary_safe: bool
    note: str

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "extensions": list(self.extensions),
            "max_chars": self.max_chars,
            "binary_safe": self.binary_safe,
            "note": self.note,
        }


def infos() -> tuple[ExtractorInfo, ...]:
    """Every registered extractor with its declared limits."""
    text = tuple(
        sorted(extension for extension in EXTRACTORS if extension in TEXT_EXTENSIONS)
    )
    described = [
        ExtractorInfo(
            key="text",
            extensions=text,
            max_chars=MAX_CONTENT_CHARS,
            binary_safe=True,
            note="UTF-8 with replacement, NUL stripped",
        )
    ]
    for key, extensions, note in (
        ("pdf", (".pdf",), "pypdf, text only"),
        ("office", (".docx", ".xlsx", ".xlsm", ".pptx"), "python-docx/openpyxl/python-pptx"),
    ):
        supported = tuple(
            sorted(extension for extension in extensions if extension in EXTRACTORS)
        )
        described.append(
            ExtractorInfo(
                key=key,
                extensions=supported,
                # A broken office file must not be able to exhaust memory:
                # the same ceiling as text, applied after parsing.
                max_chars=MAX_CONTENT_CHARS,
                binary_safe=True,
                note=note,
            )
        )
    return tuple(described)


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
    "ExtractorInfo",
    "extract",
    "infos",
    "is_text_extension",
    "normalize_text",
    "supports",
]
