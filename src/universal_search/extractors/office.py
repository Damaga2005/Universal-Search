"""OOXML (DOCX / XLSX / PPTX) extraction using only the standard library.

These packages are ZIP archives of XML parts; we read the parts that carry
visible text and ignore everything else. Phase 025 hardening:

* the member count is checked before anything is read;
* every member is vetted from the central-directory header before reading —
  traversal names, declared sizes above the per-part cap and implausible
  decompression ratios are rejected without touching the bytes;
* member reads are streamed in chunks and stop at the per-part limit, so a
  lying header cannot materialize a bomb;
* DTD entity declarations are rejected before parsing (billion laughs);
* sharedStrings is stream-parsed (``iterparse`` with elements cleared as
  they close) and stopped at the character budget, so a large part can
  neither materialize a huge element tree nor grow an unbounded list;
* a malformed *optional* part costs a warning and the rest of the document;
  only a malformed *required* part fails the extraction;
* an empty text layer is reported as ``no_content``, never an empty
  success; headings, sheet names, slide text and core title are preserved
  as bounded structure; time, sheet and slide limits truncate visibly.
"""

import re
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

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
    has_entity_declaration,
    input_size,
    member_problem,
    parse_xml_part,
    read_member_bounded,
    sanitize_text_entry,
    structure_entries,
    time_limit_warning,
    timed_out,
)
from universal_search.extractors.text import normalize_text


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
DC_NS = "{http://purl.org/dc/elements/1.1/}"

SLIDE_NAME = re.compile(r"ppt/slides/slide(\d+)\.xml$")
SHEET_NAME = re.compile(r"xl/worksheets/sheet(\d+)\.xml$")


def _name_problem(name: str) -> str | None:
    """Traversal-style member names, flagged without reading anything."""
    if (
        not name
        or "\x00" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
        or any(part in ("", ".", "..") for part in name.split("/"))
    ):
        return f"unsafe member name: {name!r}"
    return None


def _skip_doctype(data: bytes, start: int) -> int:
    """Index just past the DOCTYPE declaration, or -1 when unterminated.

    Handles the internal subset (``[...]``) where ``>`` can appear inside
    quoted entity values.
    """
    i = start + 9  # past "<!DOCTYPE"
    n = len(data)
    depth = 0
    quote = b""
    while i < n:
        ch = data[i:i + 1]
        if quote:
            if ch == quote:
                quote = b""
        elif ch in (b'"', b"'"):
            quote = ch
        elif ch == b"[":
            depth += 1
        elif ch == b"]":
            depth -= 1
        elif ch == b">" and depth <= 0:
            return i + 1
        i += 1
    return -1


def _root_element_started(data: bytes) -> bool:
    """True when ``data`` contains the start of the root element.

    Skips the XML declaration, comments, processing instructions and the
    DOCTYPE declaration. A DOCTYPE must precede the root element, so once
    the root has started the whole DTD is in hand and the entity scan is
    complete; if the root has not started, a DOCTYPE can still hide past
    the scanned bytes.
    """
    i = 0
    n = len(data)
    while i < n:
        lt = data.find(b"<", i)
        if lt < 0:
            return False
        if data.startswith(b"<?", lt):
            nxt = data.find(b"?>", lt + 2)
            if nxt < 0:
                return False
            i = nxt + 2
        elif data.startswith(b"<!--", lt):
            nxt = data.find(b"-->", lt + 4)
            if nxt < 0:
                return False
            i = nxt + 3
        elif data[lt:lt + 9].lower() == b"<!doctype":
            nxt = _skip_doctype(data, lt)
            if nxt < 0:
                return False
            i = nxt
        elif data.startswith(b"<!", lt):
            nxt = data.find(b">", lt + 2)
            if nxt < 0:
                return False
            i = nxt + 1
        else:
            return True
    return False


class _HeadSource:
    """File-like source that serves a pre-read head before the raw stream.

    The DTD-entity scan needs the first 64 KiB in hand before the parser
    sees any bytes, and streamed reads must count towards the per-part
    limit because the central directory is not trusted.
    """

    def __init__(self, handle, head: bytes, limit: int, name: str) -> None:
        self._handle = handle
        self._head = head
        self._limit = limit
        self._name = name
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        if self._head:
            chunk, self._head = self._head, b""
        else:
            chunk = self._handle.read(size)
        if chunk:
            self.bytes_read += len(chunk)
            if self.bytes_read > self._limit:
                raise ValueError(
                    f"part {self._name!r} exceeds the {self._limit}-byte "
                    f"part limit ({self.bytes_read} bytes read)"
                )
        return chunk


class _Archive:
    """One OOXML archive with every read bounded and vetted."""

    def __init__(self, path: Path, limits: ExtractionLimits) -> None:
        self.path = path
        self.limits = limits
        self.warnings: list[str] = []
        self.failures = 0
        self.temp_bytes = 0

    def open(self) -> zipfile.ZipFile:
        try:
            return zipfile.ZipFile(self.path)
        except zipfile.BadZipFile:
            raise ValueError("not a valid ZIP archive") from None

    def vet_members(self, archive: zipfile.ZipFile) -> None:
        """Count cap plus a name sweep, before any part is read."""
        infos = archive.infolist()
        if len(infos) > self.limits.max_zip_members:
            raise ValueError(
                f"too many ZIP members: {len(infos)} "
                f"(limit {self.limits.max_zip_members})"
            )
        for info in infos:
            problem = _name_problem(info.filename)
            if problem is not None:
                self.warnings.append(problem)

    def read(self, archive: zipfile.ZipFile, name: str, *, required: bool) -> ElementTree.Element | None:
        """Read, vet and parse one XML part.

        Returns None for optional parts that cannot be read; raises
        ValueError for required ones.
        """
        try:
            info = archive.getinfo(name)
        except KeyError:
            if required:
                raise ValueError(f"missing part: {name}") from None
            return None
        problem = member_problem(info, self.limits)
        if problem is not None:
            self.warnings.append(problem)
            self.failures += 1
            if required:
                raise ValueError(problem)
            return None
        data = read_member_bounded(archive, name, self.limits.max_part_bytes)
        self.temp_bytes += len(data)
        try:
            return parse_xml_part(data, name)
        except ValueError as exc:
            self.warnings.append(str(exc))
            self.failures += 1
            if required:
                raise
            return None

    def read_shared_strings(
        self, archive: zipfile.ZipFile, name: str
    ) -> tuple[list[str], bool] | None:
        """Stream-parse ``xl/sharedStrings.xml`` within the character budget.

        Returns ``(strings, capped)`` — the collected shared strings and
        whether the character budget cut the list short — or None when the
        part is unusable. The element tree is never materialized whole:
        each ``<si>`` is cleared as it closes and the scan stops once the
        budget's worth of strings is kept, because the rest of the part
        could never be output anyway.
        """
        try:
            info = archive.getinfo(name)
        except KeyError:
            return None
        problem = member_problem(info, self.limits)
        if problem is not None:
            self.warnings.append(problem)
            self.failures += 1
            return None
        shared: list[str] = []
        total = 0
        capped = False
        source: _HeadSource | None = None
        try:
            with archive.open(name) as handle:
                head = handle.read(65_536)
                if has_entity_declaration(head):
                    self.warnings.append(
                        f"DTD entities are not allowed in {name}"
                    )
                    self.failures += 1
                    return None
                if not _root_element_started(head) and len(head) == 65_536:
                    # The root has not started within the scanned head and
                    # more bytes may follow, so a DOCTYPE with entity
                    # declarations can hide past the head. Reject the part
                    # (it is optional; the rest of the workbook still reads).
                    self.warnings.append(
                        f"no root element within the first 64 KiB in {name}; "
                        f"part rejected"
                    )
                    self.failures += 1
                    return None
                source = _HeadSource(
                    handle, head, self.limits.max_part_bytes, name
                )
                context = ElementTree.iterparse(
                    source, events=("start", "end")
                )
                _, root = next(context)
                for event, element in context:
                    if event != "end" or element.tag != f"{S_NS}si":
                        continue
                    room = self.limits.max_chars - total
                    if room <= 0:
                        capped = True
                        break
                    text = "".join(
                        node.text or "" for node in element.iter(f"{S_NS}t")
                    )
                    kept = text[:room]
                    shared.append(kept)
                    total += len(kept)
                    element.clear()
                    root.clear()
        except (OSError, ElementTree.ParseError) as exc:
            self.warnings.append(f"malformed XML in {name}: {exc}")
            self.failures += 1
        finally:
            if source is not None:
                self.temp_bytes += source.bytes_read
        return shared, capped


def _finish(
    archive: _Archive,
    *,
    started: float,
    input_bytes: int,
    text: str,
    truncated: bool,
    structure: DocumentStructure | None,
    extra_usage: dict[str, int] | None = None,
) -> ExtractionResult:
    warnings = archive.warnings
    if truncated:
        status = ExtractionStatus.TRUNCATED
    elif not text or not text.strip():
        # A whitespace-only text layer is no content, not empty success.
        status = ExtractionStatus.PARTIAL if archive.failures else ExtractionStatus.NO_CONTENT
        warnings = [*warnings, "no extractable text"]
        text = None
    elif archive.failures:
        status = ExtractionStatus.PARTIAL
    else:
        status = ExtractionStatus.OK
    usage = ResourceUsage(
        input_bytes=input_bytes,
        output_chars=len(text or ""),
        temp_bytes=archive.temp_bytes,
        elapsed_ms=(time.perf_counter() - started) * 1_000.0,
        **(extra_usage or {}),
    )
    return finalize(
        started=started,
        input_bytes=input_bytes,
        text=text,
        status=status,
        warnings=warnings,
        truncated=truncated,
        structure=structure,
        usage=usage,
    )


def _cancelled_result(archive: _Archive, *, started: float, input_bytes: int) -> ExtractionResult:
    return finalize(
        started=started,
        input_bytes=input_bytes,
        text=None,
        status=ExtractionStatus.CANCELLED,
        truncated=True,
        warnings=(*archive.warnings, "extraction cancelled"),
        usage=ResourceUsage(
            input_bytes=input_bytes,
            elapsed_ms=(time.perf_counter() - started) * 1_000.0,
            temp_bytes=archive.temp_bytes,
        ),
    )


def read_docx(
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
            started=started, input_bytes=size, text=None,
            status=ExtractionStatus.ERROR, error=over_limit,
        )
    if check_cancel(cancel):
        return finalize(
            started=started, input_bytes=size, text=None,
            status=ExtractionStatus.CANCELLED, truncated=True,
            warnings=("extraction cancelled",),
        )
    archive = _Archive(path, limits)
    truncated = False
    try:
        with archive.open() as root_archive:
            archive.vet_members(root_archive)
            root = archive.read(root_archive, "word/document.xml", required=True)
            budget = CharBudget(limits.max_chars)
            headings: list[str] = []
            for paragraph_index, paragraph in enumerate(root.iter(f"{W_NS}p")):
                style_node = paragraph.find(f"{W_NS}pPr/{W_NS}pStyle")
                style = style_node.get(f"{W_NS}val") if style_node is not None else None
                if style and style.lower().startswith("heading"):
                    heading = "".join(
                        node.text or "" for node in paragraph.iter(f"{W_NS}t")
                    )
                    if len(headings) < 64 and heading.strip():
                        headings.append(heading)
                runs = (node.text or "" for node in paragraph.iter(f"{W_NS}t"))
                budget.add("".join(runs) + "\n")
                if check_cancel(cancel):
                    return _cancelled_result(archive, started=started, input_bytes=size)
                if timed_out(started, limits):
                    truncated = True
                    archive.warnings.append(
                        time_limit_warning(limits, "paragraph", paragraph_index + 1)
                    )
                    break
                if budget.cut:
                    # Text was dropped: stop reading instead of joining
                    # runs for the rest of the document.
                    truncated = True
                    break
            title = None
            core = archive.read(root_archive, "docProps/core.xml", required=False)
            if core is not None:
                title_node = core.find(f"{DC_NS}title")
                if title_node is not None and title_node.text:
                    title = title_node.text
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        return finalize(
            started=started, input_bytes=size, text=None,
            status=ExtractionStatus.ERROR, error=f"{type(exc).__name__}: {exc}",
            warnings=archive.warnings,
        )
    text = normalize_text(budget.text())
    structure = None
    if headings or title:
        structure = DocumentStructure(
            title=sanitize_text_entry(title) if title else None,
            headings=structure_entries(headings),
        )
    return _finish(
        archive, started=started, input_bytes=size, text=text,
        truncated=truncated or budget.cut, structure=structure,
    )


def read_xlsx(
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
            started=started, input_bytes=size, text=None,
            status=ExtractionStatus.ERROR, error=over_limit,
        )
    if check_cancel(cancel):
        return finalize(
            started=started, input_bytes=size, text=None,
            status=ExtractionStatus.CANCELLED, truncated=True,
            warnings=("extraction cancelled",),
        )
    archive = _Archive(path, limits)
    truncated = False
    sheet_titles: list[str] = []
    budget = CharBudget(limits.max_chars)
    sheets_seen = 0
    try:
        with archive.open() as root_archive:
            archive.vet_members(root_archive)
            names = root_archive.namelist()
            shared: list[str] = []
            shared_capped = False
            if "xl/sharedStrings.xml" in names:
                streamed = archive.read_shared_strings(
                    root_archive, "xl/sharedStrings.xml"
                )
                if streamed is not None:
                    shared, shared_capped = streamed
            if shared_capped:
                truncated = True
                archive.warnings.append(
                    f"shared strings exceed the {limits.max_chars}-character "
                    f"budget; kept {len(shared)}"
                )
            if "xl/workbook.xml" in names:
                workbook_root = archive.read(
                    root_archive, "xl/workbook.xml", required=False
                )
                if workbook_root is not None:
                    # Titles go into the budget one at a time (never joined
                    # first) and the list stays bounded for the structure.
                    for element in workbook_root.iter(f"{S_NS}sheet"):
                        title = element.get("name")
                        if not title:
                            continue
                        budget.add(title + "\n")
                        if len(sheet_titles) < 64:
                            sheet_titles.append(title)
                        if budget.exhausted:
                            break
            sheets = sorted(
                (name for name in names if SHEET_NAME.fullmatch(name)),
                key=lambda name: int(SHEET_NAME.fullmatch(name).group(1)),
            )
            for sheet in sheets:
                if sheets_seen >= limits.max_sheets:
                    truncated = True
                    archive.warnings.append(
                        f"stopped at {limits.max_sheets} sheets of {len(sheets)}"
                    )
                    break
                if budget.exhausted:
                    # The budget is spent: stop parsing the remaining
                    # sheets instead of reading them for nothing.
                    if budget.cut:
                        truncated = True
                    break
                sheets_seen += 1
                if check_cancel(cancel):
                    return _cancelled_result(archive, started=started, input_bytes=size)
                if timed_out(started, limits):
                    truncated = True
                    archive.warnings.append(
                        time_limit_warning(limits, "sheet", sheets_seen)
                    )
                    break
                sheet_root = archive.read(root_archive, sheet, required=False)
                if sheet_root is None:
                    continue
                for cell in sheet_root.iter(f"{S_NS}c"):
                    cell_type = cell.get("t")
                    value = cell.find(f"{S_NS}v")
                    if cell_type == "s" and value is not None and value.text is not None:
                        try:
                            index = int(value.text)
                        except ValueError:
                            continue
                        if 0 <= index < len(shared):
                            budget.add(shared[index] + "\n")
                    elif cell_type == "inlineStr":
                        budget.add(
                            "".join(node.text or "" for node in cell.iter(f"{S_NS}t"))
                            + "\n"
                        )
                    elif value is not None and value.text:
                        budget.add(value.text + "\n")
                    if budget.cut:
                        truncated = True
                        break
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        return finalize(
            started=started, input_bytes=size, text=None,
            status=ExtractionStatus.ERROR, error=f"{type(exc).__name__}: {exc}",
            warnings=archive.warnings,
        )
    text = normalize_text(budget.text())
    structure = None
    if sheet_titles or sheets_seen:
        names = [
            title
            for title in sheet_titles[:64]
        ] or [f"sheet{i + 1}" for i in range(min(sheets_seen, 64))]
        structure = DocumentStructure(sheets=structure_entries(names))
    return _finish(
        archive, started=started, input_bytes=size, text=text,
        truncated=truncated or budget.cut, structure=structure,
        extra_usage={"sheets": sheets_seen},
    )


def read_pptx(
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
            started=started, input_bytes=size, text=None,
            status=ExtractionStatus.ERROR, error=over_limit,
        )
    if check_cancel(cancel):
        return finalize(
            started=started, input_bytes=size, text=None,
            status=ExtractionStatus.CANCELLED, truncated=True,
            warnings=("extraction cancelled",),
        )
    archive = _Archive(path, limits)
    truncated = False
    slide_texts: list[str] = []
    budget = CharBudget(limits.max_chars)
    slides_seen = 0
    try:
        with archive.open() as root_archive:
            archive.vet_members(root_archive)
            slides = sorted(
                (name for name in root_archive.namelist() if SLIDE_NAME.fullmatch(name)),
                key=lambda name: int(SLIDE_NAME.fullmatch(name).group(1)),
            )
            for slide in slides:
                if slides_seen >= limits.max_slides:
                    truncated = True
                    archive.warnings.append(
                        f"stopped at {limits.max_slides} slides of {len(slides)}"
                    )
                    break
                slides_seen += 1
                if check_cancel(cancel):
                    return _cancelled_result(archive, started=started, input_bytes=size)
                if timed_out(started, limits):
                    truncated = True
                    archive.warnings.append(
                        time_limit_warning(limits, "slide", slides_seen)
                    )
                    break
                slide_root = archive.read(root_archive, slide, required=False)
                if slide_root is None:
                    continue
                texts = [node.text or "" for node in slide_root.iter(f"{A_NS}t")]
                first = next((t.strip() for t in texts if t.strip()), "")
                if first and len(slide_texts) < 64:
                    slide_texts.append(first)
                budget.add(" ".join(texts) + "\n")
                if budget.exhausted:
                    if budget.cut:
                        truncated = True
                    break
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        return finalize(
            started=started, input_bytes=size, text=None,
            status=ExtractionStatus.ERROR, error=f"{type(exc).__name__}: {exc}",
            warnings=archive.warnings,
        )
    text = normalize_text(budget.text())
    structure = None
    if slide_texts:
        structure = DocumentStructure(slides=structure_entries(slide_texts))
    return _finish(
        archive, started=started, input_bytes=size, text=text,
        truncated=truncated, structure=structure,
        extra_usage={"slides": slides_seen},
    )
