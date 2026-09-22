"""OOXML (DOCX / XLSX / PPTX) extraction using only the standard library.

These packages are ZIP archives of XML parts; we read the parts that carry
visible text and ignore everything else. Any structural failure is reported
as an extraction error instead of raised.
"""

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from universal_search.domain.extraction import ExtractionResult
from universal_search.extractors.text import MAX_CONTENT_CHARS, normalize_text


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

SLIDE_NAME = re.compile(r"ppt/slides/slide(\d+)\.xml$")
SHEET_NAME = re.compile(r"xl/worksheets/sheet(\d+)\.xml$")


def _read_part(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(archive.read(name))
    except KeyError:
        raise ValueError(f"missing part: {name}") from None
    except ElementTree.ParseError as exc:
        raise ValueError(f"malformed XML in {name}: {exc}") from None


def _bounded(text: str) -> str:
    return normalize_text(text)[:MAX_CONTENT_CHARS]


def read_docx(path: Path) -> ExtractionResult:
    try:
        with zipfile.ZipFile(path) as archive:
            root = _read_part(archive, "word/document.xml")
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        return ExtractionResult(error=f"{type(exc).__name__}: {exc}")
    paragraphs = []
    for paragraph in root.iter(f"{W_NS}p"):
        runs = (node.text or "" for node in paragraph.iter(f"{W_NS}t"))
        paragraphs.append("".join(runs))
    return ExtractionResult(text=_bounded("\n".join(paragraphs)))


def read_xlsx(path: Path) -> ExtractionResult:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                strings_root = _read_part(archive, "xl/sharedStrings.xml")
                for item in strings_root.iter(f"{S_NS}si"):
                    shared.append(
                        "".join(node.text or "" for node in item.iter(f"{S_NS}t"))
                    )
            sheet_titles: list[str] = []
            if "xl/workbook.xml" in names:
                workbook_root = _read_part(archive, "xl/workbook.xml")
                sheet_titles = [
                    element.get("name", "")
                    for element in workbook_root.iter(f"{S_NS}sheet")
                    if element.get("name")
                ]
            parts: list[str] = []
            if sheet_titles:
                parts.append(" ".join(sheet_titles))
            sheets = [
                name
                for name in names
                if SHEET_NAME.fullmatch(name)
            ]
            sheets.sort(key=lambda name: int(SHEET_NAME.fullmatch(name).group(1)))
            for sheet in sheets:
                sheet_root = _read_part(archive, sheet)
                for cell in sheet_root.iter(f"{S_NS}c"):
                    cell_type = cell.get("t")
                    value = cell.find(f"{S_NS}v")
                    if cell_type == "s" and value is not None and value.text is not None:
                        try:
                            index = int(value.text)
                        except ValueError:
                            continue
                        if 0 <= index < len(shared):
                            parts.append(shared[index])
                    elif cell_type == "inlineStr":
                        parts.append(
                            "".join(node.text or "" for node in cell.iter(f"{S_NS}t"))
                        )
                    elif value is not None and value.text:
                        parts.append(value.text)
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        return ExtractionResult(error=f"{type(exc).__name__}: {exc}")
    return ExtractionResult(text=_bounded("\n".join(parts)))


def read_pptx(path: Path) -> ExtractionResult:
    try:
        with zipfile.ZipFile(path) as archive:
            slides = [name for name in archive.namelist() if SLIDE_NAME.fullmatch(name)]
            slides.sort(key=lambda name: int(SLIDE_NAME.fullmatch(name).group(1)))
            parts: list[str] = []
            for slide in slides:
                slide_root = _read_part(archive, slide)
                texts = [
                    node.text or ""
                    for node in slide_root.iter(f"{A_NS}t")
                ]
                parts.append(" ".join(texts))
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        return ExtractionResult(error=f"{type(exc).__name__}: {exc}")
    return ExtractionResult(text=_bounded("\n".join(parts)))
