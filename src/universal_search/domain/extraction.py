"""Result of attempting to read a document's text content.

The domain model distinguishes these states (phase 025):

- ``text`` present, ``error`` absent: extraction succeeded.
- ``text`` absent, ``error`` absent, ``status == "no_content"``: the format
  carries no extractable text (empty text layer).
- ``text`` absent, ``error`` present: extraction was attempted and failed.

Every result carries the contract ``contract_version``, a machine-readable
``status``, sanitizer ``warnings``, a ``structure`` summary (headings, sheet
and slide names, title) and a ``resource_usage`` measurement, so a truncated
or partial extraction is always explainable instead of silently lossy.

:class:`ExtractionLimits` bounds what a single extraction may consume —
input bytes, characters, pages, sheets, slides, time, ZIP members, per-part
bytes and decompression expansion — before any unbounded read happens.
"""

from dataclasses import dataclass
from enum import StrEnum

# Version of the extraction contract. Bump when ExtractionResult gains a
# field or a status changes meaning; consumers persist it alongside the
# diagnostics it describes.
CONTRACT_VERSION = 1


class ExtractionStatus(StrEnum):
    """Machine-readable outcome of one extraction attempt.

    Precedence when several apply: ``error`` > ``cancelled`` > ``truncated``
    > ``partial`` > ``no_content`` > ``ok``. ``truncated`` means the run
    stopped before the end of the document by a limit; ``partial`` means
    some components failed but usable text was produced; ``no_content``
    means the document was read fine and simply has no text layer.
    """

    OK = "ok"
    TRUNCATED = "truncated"
    PARTIAL = "partial"
    NO_CONTENT = "no_content"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    """Per-extraction resource bounds.

    Defaults are generous for real documents and hostile against bombs:
    input is capped before opening, output is capped while accumulating,
    and every loop (pages, sheets, slides, ZIP members) checks time and
    count limits cooperatively. ``max_seconds = 0`` disables the time
    limit (used by tests that need deterministic completion).
    """

    max_input_bytes: int = 512 * 1024 * 1024
    max_chars: int = 2_000_000
    max_pages: int = 5_000
    max_sheets: int = 500
    max_slides: int = 1_000
    max_seconds: float = 30.0
    max_zip_members: int = 4_096
    max_part_bytes: int = 16 * 1024 * 1024
    max_expansion_ratio: int = 100

    def as_dict(self) -> dict[str, float | int]:
        return {
            "max_input_bytes": self.max_input_bytes,
            "max_chars": self.max_chars,
            "max_pages": self.max_pages,
            "max_sheets": self.max_sheets,
            "max_slides": self.max_slides,
            "max_seconds": self.max_seconds,
            "max_zip_members": self.max_zip_members,
            "max_part_bytes": self.max_part_bytes,
            "max_expansion_ratio": self.max_expansion_ratio,
        }


DEFAULT_LIMITS = ExtractionLimits()

# Bounds for the structure summary: enough to orient a human, never enough
# to duplicate the document's metadata in the diagnostics.
MAX_STRUCTURE_ENTRIES = 64
MAX_STRUCTURE_CHARS = 200


@dataclass(frozen=True, slots=True)
class DocumentStructure:
    """Visible structure recovered without parsing layout.

    ``headings`` are styled paragraphs (DOCX) or markdown ``#`` lines;
    ``sheets``/``slides`` are the names/first text of each container;
    ``title`` comes from core properties or the PDF Info dictionary. Every
    entry is sanitized and the lists are bounded.
    """

    title: str | None = None
    headings: tuple[str, ...] = ()
    sheets: tuple[str, ...] = ()
    slides: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    """What one extraction actually consumed.

    ``input_bytes`` is the file size, ``output_chars`` the extracted text
    length, ``pages``/``sheets``/``slides`` the containers visited,
    ``temp_bytes`` bytes materialized from decompressed ZIP members and
    ``elapsed_ms`` the wall-clock cost.
    """

    input_bytes: int = 0
    output_chars: int = 0
    pages: int = 0
    sheets: int = 0
    slides: int = 0
    temp_bytes: int = 0
    elapsed_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    text: str | None = None
    error: str | None = None
    contract_version: int = CONTRACT_VERSION
    status: str = ExtractionStatus.OK
    warnings: tuple[str, ...] = ()
    structure: DocumentStructure | None = None
    resource_usage: ResourceUsage | None = None
    truncated: bool = False
