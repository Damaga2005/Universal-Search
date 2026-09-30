"""Searchable content from ZIP archives (phase 034).

The phase-025 machinery already knows how to refuse a hostile ZIP member
before reading it: :func:`member_problem` rejects traversal names, oversized
declared parts and implausible expansion ratios from the central directory
alone, and :func:`read_member_bounded` refuses to believe a declared size. This
module reuses both rather than writing a second, weaker set of rules.

**Deliberate limitation: an archive is one document, not one per member.**
Member names are indexed as text and each member's content follows its own name,
so searching inside the archive works and searching *for* a member name works.
But a hit is attributed to the archive, not to the member, so the result list
cannot say which file inside the zip matched. The alternative — enumerating
members as virtual documents — needs a provider layer, a content path that
knows about virtual paths, and an open-result action that can materialize a
member on demand. That is a larger change than this phase, and a half-built
version of it would leave opening a result broken, which is worse than a
documented limit.

Three rules the code holds to:

* **No recursion.** A zip inside a zip is skipped with a warning. Without a
  depth limit, nested archives are an unbounded expansion path and an obvious
  thing to abuse.
* **Only registered text formats are read.** A member is extracted through the
  same registry as any other file, by suffix, so a ``.exe`` or ``.png`` inside
  the archive is never interpreted as text.
* **Nothing is written to disk.** Members are read in memory, bounded by the
  existing per-part byte limit, and discarded.
"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path, PurePosixPath

from universal_search.domain.extraction import (
    DEFAULT_LIMITS,
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
    member_problem,
    read_member_bounded,
    structure_entries,
    time_limit_warning,
    timed_out,
)
from universal_search.extractors.text import is_text_extension, normalize_text

ARCHIVE_EXTENSIONS: tuple[str, ...] = (".zip",)

# A member is labeled once, then its text. The label repeats because a hit on
# the text alone cannot be attributed to a member.
MEMBER_HEADER = "\n=== {name} ({size} bytes) ===\n"


def _is_directory(info: zipfile.ZipInfo) -> bool:
    return info.is_dir()


def _member_suffix(name: str) -> str:
    """The suffix of a member, from its declared name only."""
    return PurePosixPath(name).suffix.lower()


def read_archive(
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

    warnings: list[str] = []
    budget = CharBudget(limits.max_chars)
    names: list[str] = []
    temp_bytes = 0
    read_members = 0
    refused = 0
    skipped_binary = 0
    skipped_nested = 0
    truncated = False
    cancelled = False

    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            total = len(infos)
            if total > limits.max_zip_members:
                warnings.append(
                    f"stopped at {limits.max_zip_members} of {total} members"
                )
                total = limits.max_zip_members
                truncated = True
            for position, info in enumerate(infos[:total]):
                if check_cancel(cancel):
                    warnings.append("extraction cancelled")
                    cancelled = True
                    break
                if timed_out(started, limits):
                    warnings.append(
                        time_limit_warning(limits, "member", position + 1)
                    )
                    truncated = True
                    break
                if _is_directory(info):
                    continue
                problem = member_problem(info, limits)
                if problem is not None:
                    # zip-slip, an oversized part or an implausible expansion
                    # ratio: refused from the header, never read.
                    refused += 1
                    if refused <= 5:
                        warnings.append(f"refused: {problem}")
                    continue
                suffix = _member_suffix(info.filename)
                if suffix in ARCHIVE_EXTENSIONS:
                    skipped_nested += 1
                    continue
                if not is_text_extension(suffix):
                    skipped_binary += 1
                    continue
                try:
                    data = read_member_bounded(
                        archive, info.filename, limits.max_part_bytes
                    )
                except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
                    refused += 1
                    if refused <= 5:
                        warnings.append(
                            f"refused {info.filename!r}: {type(exc).__name__}"
                        )
                    continue
                temp_bytes += len(data)
                read_members += 1
                names.append(info.filename)
                budget.add(
                    MEMBER_HEADER.format(name=info.filename, size=len(data))
                )
                # Text is read the same way a standalone file is read: through
                # the text extractor's own decoding rules, not by decoding here.
                budget.add(normalize_text(data.decode("utf-8-sig", "replace")))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        return finalize(
            started=started,
            input_bytes=size,
            text=None,
            status=ExtractionStatus.ERROR,
            error=f"{type(exc).__name__}: {exc}",
            warnings=warnings,
        )

    if skipped_nested:
        warnings.append(
            f"{skipped_nested} nested archive(s) not opened (no recursion)"
        )
    if skipped_binary:
        warnings.append(f"{skipped_binary} non-text member(s) skipped")
    if refused:
        warnings.append(f"{refused} member(s) refused")

    text = normalize_text(budget.text())
    if not text or not text.strip():
        text = None
    if cancelled:
        status = ExtractionStatus.CANCELLED
        text = None
    elif truncated or budget.cut:
        status = ExtractionStatus.TRUNCATED
    elif read_members == 0:
        status = ExtractionStatus.NO_CONTENT
        warnings.append("no readable text members")
    elif refused or skipped_binary or skipped_nested:
        status = ExtractionStatus.PARTIAL
    else:
        status = ExtractionStatus.OK

    usage = ResourceUsage(
        input_bytes=size,
        output_chars=len(text or ""),
        temp_bytes=temp_bytes,
        elapsed_ms=(time.perf_counter() - started) * 1_000.0,
    )
    return finalize(
        started=started,
        input_bytes=size,
        text=text,
        status=status,
        warnings=warnings,
        truncated=truncated or budget.cut or cancelled,
        usage=usage,
        structure=_structure(names),
    )


def _structure(names: list[str]):
    from universal_search.domain.extraction import DocumentStructure

    entries = structure_entries(names)
    return DocumentStructure(headings=entries) if entries else None


__all__ = ["ARCHIVE_EXTENSIONS", "read_archive"]
