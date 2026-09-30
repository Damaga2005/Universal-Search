"""Phase 025: adversarial, limit and stress tests for the extraction contract.

Every fixture here is hostile in some way: empty text layers, encrypted or
malformed PDFs, broken Office archives, path traversal, decompression bombs,
DTD entity expansion, unusual encodings, huge inputs and cooperative
cancellation. The contract under test: a malformed or oversized document
can never take down the indexer, limits are enforced before unbounded
reads, and every outcome is explainable through status, warnings and
resource measurements.
"""

import functools
import io
import json
import struct
import tracemalloc
import time
import zipfile
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

from universal_search.domain.extraction import (
    CONTRACT_VERSION,
    DEFAULT_LIMITS,
    ExtractionLimits,
    ExtractionStatus,
)
from universal_search.extractors import extract, infos
from universal_search.extractors.base import CharBudget, sanitize_warning
from universal_search.extractors.text import MAX_CONTENT_CHARS
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.privacy import forget

from docfactories import make_docx, make_pptx


def write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


# -- adversarial fixture builders --------------------------------------------


def make_pdf_pages(page_texts: list[str], title: str | None = None) -> bytes:
    """Multi-page PDF with one text line per page and a correct xref."""
    count = len(page_texts)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{i} 0 R" for i in range(3, 3 + count))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode())
    font_num = 3 + count
    for i in range(count):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 " + str(font_num).encode() + b" 0 R >> >> "
            b"/Contents " + str(4 + count + i).encode() + b" 0 R >>"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for text in page_texts:
        escaped = (
            text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        )
        content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode(
            "latin-1", "replace"
        )
        objects.append(
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
            + content + b"\nendstream"
        )
    trailer_extra = b""
    if title is not None:
        info_num = len(objects) + 1
        objects.append(
            b"<< /Title (" + title.encode("latin-1", "replace") + b") >>"
        )
        trailer_extra = b" /Info " + str(info_num).encode() + b" 0 R"
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R".encode()
        + trailer_extra
        + b" >>\n"
        + f"startxref\n{xref_offset}\n%%EOF".encode()
    )
    return bytes(out)


def make_image_only_pdf() -> bytes:
    """A one-page PDF whose page carries an image XObject and no text."""
    content = b"q 100 0 0 100 72 72 cm /Im1 Do Q"
    image = b"\x00" * 16
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /XObject << /Im1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        (
            b"<< /Type /XObject /Subtype /Image /Width 4 /Height 4 "
            b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 16 >>\nstream\n"
            + image + b"\nendstream"
        ),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    ).encode()
    return bytes(out)


def make_encrypted_pdf(password: str, owner: str | None = None) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt(password, owner)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_bomb_zip(member: str, claimed_size: int, payload: bytes = b"<x/>") -> bytes:
    """One-member zip whose central directory claims ``claimed_size`` bytes.

    The stored payload is tiny; only the header fields lie. A reader that
    trusts ``file_size`` before reading would try to materialize the claimed
    size — the extractor must reject the member from the header alone.
    """
    name = member.encode()
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    local = (
        struct.pack(
            "<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, crc,
            len(payload), claimed_size, len(name), 0,
        ) + name + payload
    )
    central = (
        struct.pack(
            "<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0, 0, 0, crc,
            len(payload), claimed_size, len(name), 0, 0, 0, 0, 0, 0,
        ) + name
    )
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)
    return local + central + end


def make_ratio_bomb_zip(member: str, claimed_size: int = 10 * 1024 * 1024) -> bytes:
    """Zip whose member decompresses to ~1000x its compressed size."""
    name = member.encode()
    compressed = zlib.compress(b"\x00" * claimed_size)
    crc = zlib.crc32(b"\x00" * claimed_size) & 0xFFFFFFFF
    local = (
        struct.pack(
            "<IHHHHHIIIHH", 0x04034B50, 20, 0, 8, 0, 0, crc,
            len(compressed), claimed_size, len(name), 0,
        ) + name + compressed
    )
    central = (
        struct.pack(
            "<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 8, 0, 0, crc,
            len(compressed), claimed_size, len(name), 0, 0, 0, 0, 0, 0,
        ) + name
    )
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)
    return local + central + end


def make_docx_bytes(body_xml: str, extra_members: dict[str, bytes] | None = None) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + body_xml + "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    parts = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        "word/document.xml": document,
    }
    if extra_members:
        parts.update(extra_members)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def make_xlsx_sheets(sheet_texts: list[str]) -> bytes:
    """XLSX with one worksheet per entry, each holding one shared string."""
    shared = "".join(f"<si><t>{escape(text)}</t></si>" for text in sheet_texts)
    strings = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(sheet_texts)}" uniqueCount="{len(sheet_texts)}">'
        + shared + "</sst>"
    )
    sheets = "".join(
        f'<sheet name="Hoja{i + 1}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i in range(len(sheet_texts))
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(sheet_texts))
        )
        + "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(len(sheet_texts))
        )
        + "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )
    parts = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": workbook_rels,
        "xl/sharedStrings.xml": strings,
    }
    for i, text in enumerate(sheet_texts):
        worksheet = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData><row r="1"><c r="A1" t="s"><v>{i}</v></c></row></sheetData></worksheet>'
        )
        parts[f"xl/worksheets/sheet{i + 1}.xml"] = worksheet
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def extraction_row(database: SearchDatabase, name: str) -> dict[str, object] | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT i.extraction_status AS status, i.extraction_warnings AS warnings,"
            " i.extraction_truncated AS truncated, i.extraction_contract AS contract"
            " FROM document_intelligence AS i"
            " JOIN documents AS d ON d.id = i.document_id"
            " WHERE d.name = ?",
            (name,),
        ).fetchone()
    if row is None:
        return None
    return {
        "status": row["status"],
        "warnings": json.loads(row["warnings"] or "[]"),
        "truncated": bool(row["truncated"]),
        "contract": row["contract"],
    }


# -- the versioned contract ---------------------------------------------------


def test_successful_extraction_carries_the_versioned_contract(tmp_path: Path) -> None:
    path = write(tmp_path / "notes.txt", "plain text about capacitors")
    result = extract(path)
    assert result.error is None
    assert result.contract_version == CONTRACT_VERSION
    assert result.status == ExtractionStatus.OK
    assert result.warnings == ()
    assert result.truncated is False
    assert result.resource_usage is not None
    assert result.resource_usage.input_bytes == path.stat().st_size
    assert result.resource_usage.output_chars == len(result.text or "")
    assert result.resource_usage.elapsed_ms >= 0


def test_registry_declares_limits_and_contract_version() -> None:
    described = infos()
    assert [info.key for info in described] == [
        "text", "pdf", "office", "mail", "archive"
    ]
    for info in described:
        payload = info.as_dict()
        assert payload["contract_version"] == CONTRACT_VERSION
        assert payload["limits"]["max_chars"] == MAX_CONTENT_CHARS
        assert payload["limits"]["max_input_bytes"] > 0


def test_extract_passes_limits_through_to_extractors(tmp_path: Path) -> None:
    path = write(tmp_path / "notes.txt", "0123456789ABCDEFGHIJ")
    result = extract(path, limits=ExtractionLimits(max_chars=10))
    assert result.text == "0123456789"
    assert result.truncated is True
    assert result.status == ExtractionStatus.TRUNCATED
    assert result.warnings  # truncation is visible, never silent


def test_unsupported_extension_is_still_not_an_error(tmp_path: Path) -> None:
    path = write(tmp_path / "image.png", b"\x89PNG not text at all")
    result = extract(path)
    assert result.text is None
    assert result.error is None
    assert result.status == ExtractionStatus.OK


# -- text: bounds, encodings, structure ---------------------------------------


def test_huge_text_file_is_truncated_with_visible_diagnostics(tmp_path: Path) -> None:
    path = write(tmp_path / "huge.txt", "a" * (MAX_CONTENT_CHARS + 5_000))
    result = extract(path)
    assert result.text is not None
    assert len(result.text) == MAX_CONTENT_CHARS
    assert result.truncated is True
    assert result.status == ExtractionStatus.TRUNCATED
    assert any("character" in warning for warning in result.warnings)
    assert result.resource_usage is not None
    assert result.resource_usage.input_bytes == path.stat().st_size
    assert result.resource_usage.output_chars == MAX_CONTENT_CHARS


def test_input_byte_limit_is_enforced_before_reading(tmp_path: Path) -> None:
    path = write(tmp_path / "notes.txt", "x" * 500)
    result = extract(path, limits=ExtractionLimits(max_input_bytes=128))
    assert result.text is None
    assert result.error is not None
    assert "input" in result.error.lower()
    assert result.status == ExtractionStatus.ERROR


def test_unusual_encodings_never_raise(tmp_path: Path) -> None:
    utf16 = tmp_path / "utf16.md"
    utf16.write_bytes("señal ñ".encode("utf-16"))
    result = extract(utf16)
    assert result.error is None  # replacement, not a failure
    assert result.text is not None

    latin1 = tmp_path / "latin1.md"
    latin1.write_bytes(b"se\xf1al \xe9")
    result = extract(latin1)
    assert result.error is None
    assert result.text is not None

    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"ok\xff\xfeok")
    result = extract(invalid)
    assert result.error is None


def test_markdown_headings_are_preserved_as_structure(tmp_path: Path) -> None:
    path = write(tmp_path / "notes.md", "# Titulo\n## Subtitulo\nbody text")
    result = extract(path)
    assert result.structure is not None
    assert result.structure.headings == ("Titulo", "Subtitulo")


def test_crlf_text_is_preserved(tmp_path: Path) -> None:
    # Text mode has always applied universal newlines on read; the
    # contract keeps that behavior instead of changing it.
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"line1\r\nline2\r\n")
    assert extract(path).text == "line1\nline2\n"


def test_empty_text_file_stays_explicit_empty_success(tmp_path: Path) -> None:
    path = write(tmp_path / "empty.txt", b"")
    result = extract(path)
    assert result.text == ""
    assert result.error is None
    assert result.status == ExtractionStatus.OK


# -- PDF: empty, encrypted, malformed, bounded ---------------------------------


def test_image_only_pdf_is_no_content_not_empty_success(tmp_path: Path) -> None:
    path = write(tmp_path / "scan.pdf", make_image_only_pdf())
    result = extract(path)
    assert result.error is None
    assert result.text is None
    assert result.status == ExtractionStatus.NO_CONTENT
    assert result.warnings  # the reason is stated, not implicit


def test_encrypted_pdf_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "secret.pdf", make_encrypted_pdf("s3cret"))
    result = extract(path)
    assert result.text is None
    assert result.error is not None
    assert "encrypted" in result.error.lower()
    assert result.status == ExtractionStatus.ERROR


def test_owner_only_encrypted_pdf_warns_but_reads(tmp_path: Path) -> None:
    path = write(tmp_path / "owner.pdf", make_encrypted_pdf("", "owner-pw"))
    result = extract(path)
    assert result.error is None
    assert any("encrypted" in warning.lower() for warning in result.warnings)


def test_malformed_pdf_reports_error_with_usage(tmp_path: Path) -> None:
    path = write(tmp_path / "broken.pdf", b"%PDF-1.4\nnot really a pdf \x00")
    result = extract(path)
    assert result.text is None
    assert result.error is not None
    assert result.status == ExtractionStatus.ERROR
    assert result.resource_usage is not None
    assert result.resource_usage.input_bytes == path.stat().st_size


def test_pdf_page_limit_truncates_with_warning(tmp_path: Path) -> None:
    path = write(tmp_path / "many.pdf", make_pdf_pages([f"page {i}" for i in range(50)]))
    result = extract(path, limits=ExtractionLimits(max_pages=10))
    assert result.error is None
    assert result.truncated is True
    assert result.status == ExtractionStatus.TRUNCATED
    assert any("page" in warning for warning in result.warnings)
    assert result.resource_usage is not None
    assert result.resource_usage.pages == 10
    assert "page 0" in (result.text or "")
    assert "page 10" not in (result.text or "")


def test_pdf_character_limit_truncates_mid_document(tmp_path: Path) -> None:
    path = write(tmp_path / "long.pdf", make_pdf_pages(["x" * 500] * 10))
    result = extract(path, limits=ExtractionLimits(max_chars=100))
    assert result.text is not None
    assert len(result.text) <= 100
    assert result.truncated is True
    assert result.status == ExtractionStatus.TRUNCATED


def test_pdf_time_limit_stops_extraction(tmp_path: Path) -> None:
    path = write(tmp_path / "slow.pdf", make_pdf_pages([f"page {i}" for i in range(2_000)]))
    # A tiny budget guarantees the time limit trips before completion,
    # regardless of machine speed or import overhead.
    result = extract(path, limits=ExtractionLimits(max_seconds=0.01))
    assert result.error is None
    assert result.truncated is True
    assert result.status == ExtractionStatus.TRUNCATED
    assert any("time" in warning.lower() for warning in result.warnings)
    assert result.resource_usage is not None
    assert result.resource_usage.pages < 2_000


def test_pdf_title_is_preserved_as_structure(tmp_path: Path) -> None:
    path = write(tmp_path / "titled.pdf", make_pdf_pages(["hello world"], title="Mi Documento"))
    result = extract(path)
    assert result.error is None
    assert result.structure is not None
    assert result.structure.title == "Mi Documento"


def test_pdf_cancellation_returns_cancelled_status(tmp_path: Path) -> None:
    path = write(tmp_path / "big.pdf", make_pdf_pages([f"page {i}" for i in range(100)]))
    result = extract(path, cancel=lambda: True)
    assert result.status == ExtractionStatus.CANCELLED
    assert result.text is None
    assert result.truncated is True
    assert any("cancel" in warning.lower() for warning in result.warnings)


# -- Office: archives, bombs, traversal, empty layers ---------------------------


def test_docx_with_empty_body_is_no_content(tmp_path: Path) -> None:
    path = write(tmp_path / "empty.docx", make_docx(""))
    result = extract(path)
    assert result.error is None
    assert result.text is None
    assert result.status == ExtractionStatus.NO_CONTENT


def test_pptx_with_empty_slide_is_no_content(tmp_path: Path) -> None:
    path = write(tmp_path / "empty.pptx", make_pptx(""))
    result = extract(path)
    assert result.error is None
    assert result.text is None
    assert result.status == ExtractionStatus.NO_CONTENT


def test_xlsx_with_no_cells_is_no_content(tmp_path: Path) -> None:
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData></sheetData></worksheet>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    path = write(tmp_path / "empty.xlsx", buffer.getvalue())
    result = extract(path)
    assert result.error is None
    assert result.text is None
    assert result.status == ExtractionStatus.NO_CONTENT


def test_docx_headings_and_title_are_preserved(tmp_path: Path) -> None:
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Titulo principal</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Subtitulo</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>cuerpo del documento</w:t></w:r></w:p>"
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Mi Documento</dc:title></cp:coreProperties>"
    )
    path = write(
        tmp_path / "structured.docx",
        make_docx_bytes(body, {"docProps/core.xml": core}),
    )
    result = extract(path)
    assert result.error is None
    assert "cuerpo del documento" in (result.text or "")
    assert result.structure is not None
    assert result.structure.headings == ("Titulo principal", "Subtitulo")
    assert result.structure.title == "Mi Documento"


def test_docx_stops_reading_paragraphs_once_the_budget_is_spent(
    tmp_path: Path, monkeypatch
) -> None:
    """The paragraph loop must stop once the character budget is spent.

    A document with thousands of paragraphs and a budget that covers only
    the first ones must not keep iterating (and joining runs) afterwards.
    """
    body = "".join(
        "<w:p><w:r><w:t>" + "x" * 60 + "</w:t></w:r></w:p>"
        for _ in range(3_000)
    )
    path = write(tmp_path / "long.docx", make_docx_bytes(body))
    adds: list[str] = []

    class CountingBudget(CharBudget):
        def add(self, text: str) -> bool:
            adds.append(text)
            return super().add(text)

    monkeypatch.setattr("universal_search.extractors.office.CharBudget", CountingBudget)
    result = extract(path, limits=ExtractionLimits(max_chars=100))

    assert result.error is None
    assert result.truncated is True
    assert result.text is not None
    assert len(result.text) <= 100
    # 3000 paragraphs of 61 chars: the budget covers ~2 adds, not 3000.
    assert len(adds) <= 3


def test_xlsx_sheet_names_are_preserved(tmp_path: Path) -> None:
    path = write(tmp_path / "sheets.xlsx", make_xlsx_sheets(["dato uno", "dato dos"]))
    result = extract(path)
    assert result.error is None
    assert "dato uno" in (result.text or "")
    assert "dato dos" in (result.text or "")
    assert result.structure is not None
    assert result.structure.sheets == ("Hoja1", "Hoja2")


def test_pptx_slide_text_is_preserved(tmp_path: Path) -> None:
    path = write(tmp_path / "clase.pptx", make_pptx("Filas y columnas"))
    result = extract(path)
    assert result.error is None
    assert result.structure is not None
    assert result.structure.slides == ("Filas y columnas",)


def test_too_many_zip_members_is_rejected(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for i in range(DEFAULT_LIMITS.max_zip_members + 500):
            archive.writestr(f"parts/file{i:05d}.xml", "<x/>")
        archive.writestr("word/document.xml", "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>real</w:t></w:r></w:p></w:body></w:document>")
    path = write(tmp_path / "fat.docx", buffer.getvalue())
    started = time.perf_counter()
    result = extract(path)
    elapsed = time.perf_counter() - started
    assert result.text is None
    assert result.error is not None
    assert "member" in result.error.lower()
    assert elapsed < 5  # rejected from the header, not after reading everything


def test_zip_member_claiming_huge_size_is_rejected_from_the_header(tmp_path: Path) -> None:
    # 2 GiB: past the 16 MiB part cap and still a valid 32-bit ZIP size field.
    path = write(tmp_path / "bomb.docx", make_bomb_zip("word/document.xml", 2**31))
    result = extract(path)
    assert result.text is None
    assert result.error is not None
    assert result.status == ExtractionStatus.ERROR
    assert any("word/document.xml" in warning for warning in result.warnings)


def test_decompression_bomb_ratio_is_rejected_before_reading(tmp_path: Path) -> None:
    path = write(tmp_path / "ratio.docx", make_ratio_bomb_zip("word/document.xml"))
    result = extract(path)
    assert result.text is None
    assert result.error is not None
    assert any("expansion" in warning or "exceeds" in warning for warning in result.warnings)


def test_dtd_entity_expansion_is_rejected(tmp_path: Path) -> None:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<!DOCTYPE w:document [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&c;</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    path = write(tmp_path / "entities.docx", buffer.getvalue())
    started = time.perf_counter()
    result = extract(path)
    elapsed = time.perf_counter() - started
    assert result.text is None
    assert result.error is not None
    assert "entit" in result.error.lower()
    assert elapsed < 5  # rejected before parsing, never expanded


def test_traversal_member_names_are_flagged_and_skipped(tmp_path: Path) -> None:
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>contenido real</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("../evil.xml", "<x/>")
        archive.writestr("/etc/evil.xml", "<x/>")
        archive.writestr("C:/evil.xml", "<x/>")
        archive.writestr("....//word/document.xml", "<x/>")
    path = write(tmp_path / "traversal.docx", buffer.getvalue())
    result = extract(path)
    assert result.error is None
    assert "contenido real" in (result.text or "")
    assert any("unsafe" in warning.lower() for warning in result.warnings)


def test_malformed_optional_part_warns_but_continues(tmp_path: Path) -> None:
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1">'
        '<c r="A1" t="inlineStr"><is><t>valor directo</t></is></c>'
        '<c r="B1"><v>9.5</v></c>'
        "</row></sheetData></worksheet>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        archive.writestr("xl/sharedStrings.xml", "<sst><broken></sst>")
    path = write(tmp_path / "badshared.xlsx", buffer.getvalue())
    result = extract(path)
    assert result.error is None
    assert "valor directo" in (result.text or "")
    assert "9.5" in (result.text or "")
    assert any("sharedStrings" in warning for warning in result.warnings)
    assert result.status == ExtractionStatus.PARTIAL


def test_xlsx_huge_shared_strings_part_stays_bounded(tmp_path: Path) -> None:
    """A 16 MiB sharedStrings part must not blow up memory or work.

    The part holds ~12M characters of shared text — six times the
    2M-character budget. The extractor streams the part, keeps at most
    the budget's worth of strings and stops, so peak memory and work stay
    bounded instead of scaling with the part.
    """
    entries = 174_000
    entry_text = "x" * 80  # ~15.9 MiB part, ~13.9M characters of shared text
    strings = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{entries}" uniqueCount="{entries}">'
        + "".join(f"<si><t>{entry_text}</t></si>" for _ in range(entries))
        + "</sst>"
    )
    sheet_cells = "".join(
        f'<c r="A{i + 1}" t="s"><v>{i}</v></c>' for i in range(5_000)
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData><row r=\"1\">{sheet_cells}</row></sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Datos" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in {
            "[Content_Types].xml": content_types,
            "_rels/.rels": root_rels,
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": workbook_rels,
            "xl/sharedStrings.xml": strings,
            "xl/worksheets/sheet1.xml": worksheet,
        }.items():
            archive.writestr(name, data)
    path = write(tmp_path / "fat.xlsx", buffer.getvalue())

    tracemalloc.start()
    try:
        started = time.perf_counter()
        result = extract(path)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.error is None
    assert result.truncated is True
    assert result.status == ExtractionStatus.TRUNCATED
    assert any("shared" in warning.lower() for warning in result.warnings)
    assert result.text is not None
    assert len(result.text) <= MAX_CONTENT_CHARS
    assert elapsed < 10
    # Parsing the whole 14.4 MiB part into a tree plus an uncapped list
    # would peak far above this; streaming with a budget stays flat.
    assert peak < 64 * 1024 * 1024, f"peak {peak / 1024 / 1024:.1f} MiB"


def test_shared_strings_dtd_hidden_past_the_head_is_rejected(tmp_path: Path) -> None:
    """A DOCTYPE padded past the 64 KiB head must not reach the parser.

    The entity scan only sees the first 64 KiB, so a part that hides its
    DOCTYPE behind a large comment can smuggle entity declarations past
    the scan and drive a large transient allocation. The extractor must
    reject the part when the root element has not started within the
    scanned head and more bytes may follow.
    """
    levels = 8  # lol8 = 10^8 = ~100 MB of "lol" if ever expanded
    entities = ['<!ENTITY lol "lol">']
    for i in range(1, levels + 1):
        ref = "&lol;" * 10 if i == 1 else f"&lol{i - 1};" * 10
        entities.append(f'<!ENTITY lol{i} "{ref}">')
    doctype = "<!DOCTYPE sst [" + "".join(entities) + "]>"
    padding = b"<!--" + b"x" * 70_000 + b"-->"  # pushes the DOCTYPE past 64 KiB
    shared = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        + padding
        + doctype.encode()
        + b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + f"<si><t>&lol{levels};</t></si>".encode()
        + b"</sst>"
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Datos" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in {
            "[Content_Types].xml": content_types,
            "_rels/.rels": root_rels,
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": workbook_rels,
            "xl/sharedStrings.xml": shared,
            "xl/worksheets/sheet1.xml": worksheet,
        }.items():
            archive.writestr(name, data)
    path = write(tmp_path / "hidden_dtd.xlsx", buffer.getvalue())

    tracemalloc.start()
    try:
        started = time.perf_counter()
        result = extract(path)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.error is None
    # The part is rejected before the parser ever sees the DOCTYPE: a
    # bounded warning, not a large allocation.
    assert any("no root element" in warning for warning in result.warnings)
    assert elapsed < 5
    assert peak < 64 * 1024 * 1024, f"peak {peak / 1024 / 1024:.1f} MiB"


def test_exact_fit_budget_is_not_flagged_truncated(tmp_path: Path) -> None:
    """A document that exactly fills the budget is not truncated.

    The budget keeps the first ``max_chars`` characters; when the content
    fits exactly, nothing was cut, so the result must not be flagged
    truncated.
    """
    body = "<w:p><w:r><w:t>" + "x" * 99 + "</w:t></w:r></w:p>"
    path = write(tmp_path / "exact.docx", make_docx_bytes(body))
    result = extract(path, limits=ExtractionLimits(max_chars=100))
    assert result.error is None
    assert result.truncated is False
    assert result.status == ExtractionStatus.OK
    assert result.text is not None
    assert "x" * 99 in result.text


def test_xlsx_exact_fit_budget_is_not_flagged_truncated(tmp_path: Path) -> None:
    """An XLSX that exactly fills the budget is not truncated.

    Sheet titles and cell values go into the budget one at a time; when
    the content fits exactly, nothing was cut, so the result must not be
    flagged truncated.
    """
    # Sheet title "Hoja1\\n" (6) + cell "hello world\\n" (12) = 18 chars.
    path = write(tmp_path / "exact.xlsx", make_xlsx_sheets(["hello world"]))
    result = extract(path, limits=ExtractionLimits(max_chars=18))
    assert result.error is None
    assert result.truncated is False
    assert result.status == ExtractionStatus.OK
    assert result.text is not None
    assert "hello world" in result.text


def test_pptx_exact_fit_budget_is_not_flagged_truncated(tmp_path: Path) -> None:
    """A PPTX that exactly fills the budget is not truncated.

    Slide text goes into the budget; when the content fits exactly,
    nothing was cut, so the result must not be flagged truncated.
    """
    # Slide text "hello world\\n" = 12 chars.
    path = write(tmp_path / "exact.pptx", make_pptx("hello world"))
    result = extract(path, limits=ExtractionLimits(max_chars=12))
    assert result.error is None
    assert result.truncated is False
    assert result.status == ExtractionStatus.OK
    assert result.text is not None
    assert "hello world" in result.text


def test_xlsx_stops_parsing_remaining_sheets_once_budget_exhausted(
    tmp_path: Path, monkeypatch
) -> None:
    """Once the character budget is spent, later sheets are not parsed.

    A workbook with several sheets where the first sheet's content fills
    the budget must not keep reading (and parsing) the remaining sheets.
    """
    from universal_search.extractors.office import _Archive

    path = write(tmp_path / "sheets.xlsx", make_xlsx_sheets(["hello world", "goodbye world"]))
    reads: list[str] = []
    original_read = _Archive.read

    def counting_read(self, archive, name, *, required):
        reads.append(name)
        return original_read(self, archive, name, required=required)

    monkeypatch.setattr(_Archive, "read", counting_read)
    result = extract(path, limits=ExtractionLimits(max_chars=20))

    assert result.error is None
    sheet_reads = [name for name in reads if name.startswith("xl/worksheets/")]
    # The budget is spent by sheet 1; sheet 2 must never be opened.
    assert sheet_reads == ["xl/worksheets/sheet1.xml"]


def test_malformed_sheet_is_skipped_and_reported(tmp_path: Path) -> None:
    good = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>hoja buena</t></is></c></row></sheetData></worksheet>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", good)
        archive.writestr("xl/worksheets/sheet2.xml", "<worksheet><sheetData><row>")
    path = write(tmp_path / "badsheet.xlsx", buffer.getvalue())
    result = extract(path)
    assert result.error is None
    assert "hoja buena" in (result.text or "")
    assert any("sheet2" in warning for warning in result.warnings)
    assert result.status == ExtractionStatus.PARTIAL


def test_sheet_limit_truncates_with_warning(tmp_path: Path) -> None:
    path = write(tmp_path / "many.xlsx", make_xlsx_sheets(["uno", "dos", "tres"]))
    result = extract(path, limits=ExtractionLimits(max_sheets=2))
    assert result.error is None
    assert result.truncated is True
    assert result.status == ExtractionStatus.TRUNCATED
    assert any("sheet" in warning.lower() for warning in result.warnings)
    assert result.resource_usage is not None
    assert result.resource_usage.sheets == 2


def test_slide_limit_truncates_with_warning(tmp_path: Path) -> None:
    slides = {
        "ppt/slides/slide1.xml": (
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>uno</a:t></a:r></a:p>"
            "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        ),
        "ppt/slides/slide2.xml": (
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>dos</a:t></a:r></a:p>"
            "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in slides.items():
            archive.writestr(name, data)
    path = write(tmp_path / "many.pptx", buffer.getvalue())
    result = extract(path, limits=ExtractionLimits(max_slides=1))
    assert result.error is None
    assert result.truncated is True
    assert result.status == ExtractionStatus.TRUNCATED
    assert "uno" in (result.text or "")
    assert "dos" not in (result.text or "")


def test_office_cancellation_returns_cancelled_status(tmp_path: Path) -> None:
    path = write(tmp_path / "notes.docx", make_docx("contenido"))
    result = extract(path, cancel=lambda: True)
    assert result.status == ExtractionStatus.CANCELLED
    assert result.text is None
    assert result.truncated is True


# -- determinism ----------------------------------------------------------------


def test_warnings_are_deterministic_for_the_same_input(tmp_path: Path) -> None:
    path = write(tmp_path / "broken.docx", b"PK\x03\x04 this is not a zip")
    first = extract(path)
    second = extract(path)
    assert first.error == second.error
    assert first.status == second.status
    assert first.warnings == second.warnings

    pdf_path = write(tmp_path / "many.pdf", make_pdf_pages([f"page {i}" for i in range(50)]))
    limits = ExtractionLimits(max_pages=7)
    first = extract(pdf_path, limits=limits)
    second = extract(pdf_path, limits=limits)
    assert first.warnings == second.warnings
    assert first.truncated == second.truncated
    assert first.resource_usage is not None and second.resource_usage is not None
    assert first.resource_usage.pages == second.resource_usage.pages


def test_warnings_are_sanitized_and_bounded() -> None:
    cleaned = sanitize_warning("bad\nwarning\x00" + "x" * 500)
    assert "\n" not in cleaned
    assert "\x00" not in cleaned
    assert len(cleaned) <= 300


# -- indexer integration: continuation and diagnostics --------------------------


def test_one_failure_never_stops_the_pass_and_diagnostics_are_persisted(
    tmp_path: Path,
) -> None:
    root = tmp_path / "files"
    write(root / "broken.pdf", b"%PDF-1.4 garbage \x00\x01")
    write(root / "good.md", "contenido localizable")
    write(root / "secret.pdf", make_encrypted_pdf("s3cret"))
    write(root / "scan.pdf", make_image_only_pdf())
    write(root / "notes.docx", make_docx("texto del documento"))
    database = SearchDatabase(tmp_path / "index" / "search.db")

    stats = Indexer(database).index_root(root)

    assert stats.created == 5
    assert stats.extraction_errors == 2  # broken.pdf + secret.pdf
    assert stats.errors == 0

    engine = SearchEngine(database)
    assert [r.name for r in engine.search("contenido")] == ["good.md"]
    assert [r.name for r in engine.search("documento")] == ["notes.docx"]

    broken = extraction_row(database, "broken.pdf")
    assert broken is not None
    assert broken["status"] == ExtractionStatus.ERROR
    assert broken["warnings"]

    encrypted = extraction_row(database, "secret.pdf")
    assert encrypted is not None
    assert encrypted["status"] == ExtractionStatus.ERROR

    scan = extraction_row(database, "scan.pdf")
    assert scan is not None
    assert scan["status"] == ExtractionStatus.NO_CONTENT
    assert scan["warnings"]

    good = extraction_row(database, "good.md")
    assert good is not None
    assert good["status"] == ExtractionStatus.OK
    assert good["truncated"] is False
    assert good["contract"] == CONTRACT_VERSION


def test_extraction_diagnostics_are_deleted_by_privacy_forget(tmp_path: Path) -> None:
    root = tmp_path / "files"
    path = write(root / "secret.pdf", make_encrypted_pdf("s3cret"))
    database = SearchDatabase(tmp_path / "index" / "search.db")
    Indexer(database).index_root(root)
    assert extraction_row(database, "secret.pdf") is not None

    result = forget(database, path)

    assert result.documents == 1
    assert extraction_row(database, "secret.pdf") is None


def test_cancelled_extractions_are_recorded_and_the_pass_continues(tmp_path: Path) -> None:
    root = tmp_path / "files"
    write(root / "a.md", "contenido alpha")
    write(root / "b.md", "contenido beta")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    reader = functools.partial(extract, cancel=lambda: True)

    stats = Indexer(database).index_root(root, read_content=reader)

    assert stats.created == 2
    assert stats.errors == 0
    for name in ("a.md", "b.md"):
        row = extraction_row(database, name)
        assert row is not None
        assert row["status"] == ExtractionStatus.CANCELLED


def test_reindex_after_content_change_refreshes_diagnostics(tmp_path: Path) -> None:
    root = tmp_path / "files"
    path = write(root / "notes.md", "contenido inicial")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    indexer = Indexer(database)
    indexer.index_root(root)
    assert extraction_row(database, "notes.md")["status"] == ExtractionStatus.OK

    path.write_text("contenido ampliado", encoding="utf-8")
    stats = indexer.index_root(root)

    assert stats.updated == 1
    row = extraction_row(database, "notes.md")
    assert row is not None
    assert row["status"] == ExtractionStatus.OK


# -- stress ----------------------------------------------------------------------


def test_stress_large_text_file(tmp_path: Path) -> None:
    path = write(tmp_path / "big.txt", "abcde" * 700_000)  # 3.5 MB
    started = time.perf_counter()
    result = extract(path)
    elapsed = time.perf_counter() - started
    assert result.text is not None
    assert len(result.text) == MAX_CONTENT_CHARS
    assert result.truncated is True
    assert elapsed < 10


def test_stress_many_page_pdf(tmp_path: Path) -> None:
    path = write(tmp_path / "book.pdf", make_pdf_pages([f"pagina {i}" for i in range(2_000)]))
    started = time.perf_counter()
    result = extract(path)
    elapsed = time.perf_counter() - started
    assert result.error is None
    assert result.resource_usage is not None
    assert result.resource_usage.pages <= DEFAULT_LIMITS.max_pages
    assert elapsed < 30


def test_stress_indexer_over_mixed_corpus(tmp_path: Path) -> None:
    root = tmp_path / "files"
    for i in range(60):
        write(root / f"doc{i}.md", f"contenido unico {i} transistor")
        write(root / f"page{i}.pdf", make_pdf_pages([f"pdf {i} osciloscopio"]))
        write(root / f"sheet{i}.xlsx", make_xlsx_sheets([f"dato {i} zener"]))
        write(root / f"broken{i}.pdf", b"%PDF-1.4 broken \x00")
    database = SearchDatabase(tmp_path / "index" / "search.db")

    stats = Indexer(database).index_root(root)

    assert stats.created == 240
    assert stats.extraction_errors == 60
    assert stats.errors == 0
    engine = SearchEngine(database)
    assert len(engine.search("transistor", limit=100)) == 60
    assert len(engine.search("osciloscopio", limit=100)) == 60
    assert len(engine.search("zener", limit=100)) == 60
