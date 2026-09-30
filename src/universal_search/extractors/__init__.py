"""Extractor registry: file extension -> content extraction.

The provider discovers files; this package decides whether and how their
content can be read. Unsupported formats return no text and are never opened,
so binaries are never interpreted as UTF-8.

Phase 025: the registry is declarative data — one :class:`ExtractorSpec`
per format family — and ``extract()`` forwards the resource ``limits``
and the cooperative ``cancel`` callback to whichever extractor runs, so
an indexer can bound every document and stop a pass mid-file.
"""

from collections.abc import Callable
from dataclasses import dataclass
from inspect import Parameter, signature
from pathlib import Path

from universal_search.domain.extraction import (
    CONTRACT_VERSION,
    DEFAULT_LIMITS,
    ExtractionLimits,
    ExtractionResult,
)
from universal_search.extractors.archive import ARCHIVE_EXTENSIONS, read_archive
from universal_search.extractors.base import CancelCheck
from universal_search.extractors.mail import MAIL_EXTENSIONS, read_mail
from universal_search.extractors.office import read_docx, read_pptx, read_xlsx
from universal_search.extractors.pdf import read_pdf
from universal_search.extractors.text import (
    MAX_CONTENT_CHARS,
    TEXT_EXTENSIONS,
    is_text_extension,
    normalize_text,
    read_text,
)

ExtractFunction = Callable[..., ExtractionResult]

# One declarative row per extractor family. Adding a format is a one-line
# change: the registry, the inspectable infos() and the extension map all
# derive from this table.
@dataclass(frozen=True, slots=True)
class ExtractorSpec:
    key: str
    extensions: tuple[str, ...]
    max_chars: int
    binary_safe: bool
    note: str


EXTRACTOR_SPECS: tuple[ExtractorSpec, ...] = (
    ExtractorSpec(
        key="text",
        extensions=tuple(sorted(TEXT_EXTENSIONS)),
        max_chars=MAX_CONTENT_CHARS,
        binary_safe=True,
        note="UTF-8 with replacement, NUL stripped",
    ),
    ExtractorSpec(
        key="pdf",
        extensions=(".pdf",),
        max_chars=MAX_CONTENT_CHARS,
        binary_safe=True,
        note="pypdf, text only; encrypted files are rejected",
    ),
    ExtractorSpec(
        key="office",
        extensions=(".docx", ".xlsx", ".xlsm", ".pptx"),
        max_chars=MAX_CONTENT_CHARS,
        binary_safe=True,
        note=(
            "stdlib ZIP/XML reader; member, size, expansion and entity "
            "limits enforced"
        ),
    ),
    ExtractorSpec(
        key="mail",
        extensions=MAIL_EXTENSIONS,
        max_chars=MAX_CONTENT_CHARS,
        binary_safe=True,
        note=(
            "stdlib email parser; headers indexed, HTML flattened to text, "
            "attachments never read"
        ),
    ),
    ExtractorSpec(
        key="archive",
        extensions=ARCHIVE_EXTENSIONS,
        max_chars=MAX_CONTENT_CHARS,
        binary_safe=True,
        note=(
            "ZIP members read through the existing traversal/size/expansion "
            "checks; text members only, no recursion, nothing written to disk"
        ),
    ),
)

EXTRACTORS: dict[str, ExtractFunction] = {
    extension: read_text for extension in TEXT_EXTENSIONS
}
EXTRACTORS.update({
    ".pdf": read_pdf,
    ".docx": read_docx,
    ".xlsx": read_xlsx,
    ".xlsm": read_xlsx,
    ".pptx": read_pptx,
    ".eml": read_mail,
    ".mbox": read_mail,
    ".mbx": read_mail,
    ".email": read_mail,
    ".zip": read_archive,
})

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(EXTRACTORS)


@dataclass(frozen=True, slots=True)
class ExtractorInfo:
    """One registered extractor, inspectable (spec 019).

    The registry is deliberately plain data: an extractor is an extension
    plus a function plus limits, and `extract()` is the only
    way content is produced. That makes adding a format a one-line change
    and makes "what can this application read?" answerable without
    reading the code.
    """

    key: str
    extensions: tuple[str, ...]
    max_chars: int
    binary_safe: bool
    note: str
    contract_version: int = CONTRACT_VERSION
    limits: ExtractionLimits = DEFAULT_LIMITS

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "extensions": list(self.extensions),
            "max_chars": self.max_chars,
            "binary_safe": self.binary_safe,
            "note": self.note,
            "contract_version": self.contract_version,
            "limits": self.limits.as_dict(),
        }


def infos() -> tuple[ExtractorInfo, ...]:
    """Every registered extractor with its declared limits."""
    return tuple(
        ExtractorInfo(
            key=spec.key,
            extensions=spec.extensions,
            max_chars=spec.max_chars,
            binary_safe=spec.binary_safe,
            note=spec.note,
        )
        for spec in EXTRACTOR_SPECS
    )


def supports(extension: str) -> bool:
    return extension.lower() in EXTRACTORS


def _wants_resource_arguments(func: Callable[..., object]) -> bool:
    """True when ``func`` accepts the ``limits``/``cancel`` keywords.

    Registry compatibility (phase 025): extractors registered before the
    resource contract took ``path`` only. They keep working — and keep
    their own built-in ceilings — when ``extract()`` calls them the old
    way; anything accepting the keywords (or ``**kwargs``) gets them.
    """
    try:
        parameters = signature(func).parameters.values()
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        return True
    for parameter in parameters:
        if parameter.kind is Parameter.VAR_KEYWORD:
            return True
    return any(
        parameter.name in ("limits", "cancel")
        for parameter in parameters
        if parameter.kind
        in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
    )


def extract(
    path: Path,
    *,
    limits: ExtractionLimits | None = None,
    cancel: CancelCheck | None = None,
) -> ExtractionResult:
    """Extract text for ``path``, never raising for broken files.

    ``limits`` bounds what the extraction may consume (input bytes,
    characters, pages, sheets, slides, time, ZIP members, per-part bytes,
    expansion); ``cancel`` is a cooperative callback checked between units
    of work. A broken extractor must not stop an indexing run, so any
    escape is reported as extraction error data.
    """
    extractor = EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        return ExtractionResult()  # unsupported: no text, not an error
    try:
        if _wants_resource_arguments(extractor):
            return extractor(path, limits=limits, cancel=cancel)
        return extractor(path)
    except Exception as exc:  # a broken extractor must not stop an indexing run
        return ExtractionResult(error=f"{type(exc).__name__}: {exc}")


__all__ = [
    "ARCHIVE_EXTENSIONS",
    "CONTRACT_VERSION",
    "DEFAULT_LIMITS",
    "EXTRACTORS",
    "EXTRACTOR_SPECS",
    "MAIL_EXTENSIONS",
    "MAX_CONTENT_CHARS",
    "SUPPORTED_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "ExtractionLimits",
    "ExtractionResult",
    "ExtractorInfo",
    "ExtractorSpec",
    "extract",
    "infos",
    "is_text_extension",
    "normalize_text",
    "supports",
]
