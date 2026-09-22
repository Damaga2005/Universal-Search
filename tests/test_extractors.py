import zipfile
from pathlib import Path

from universal_search.domain.extraction import ExtractionResult
from universal_search.extractors import (
    MAX_CONTENT_CHARS,
    SUPPORTED_EXTENSIONS,
    extract,
    supports,
)
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine

from docfactories import make_docx, make_pdf, make_pptx, make_xlsx


def write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


# -- one test per supported format ------------------------------------------

def test_txt_extractor(tmp_path: Path) -> None:
    path = write(tmp_path / "notes.txt", "plain text about capacitors")
    assert extract(path).text == "plain text about capacitors"


def test_md_extractor(tmp_path: Path) -> None:
    path = write(tmp_path / "notes.md", "# Título\ncontenido en markdown")
    assert "contenido en markdown" in (extract(path).text or "")


def test_csv_extractor(tmp_path: Path) -> None:
    path = write(tmp_path / "data.csv", "a,b\n1,reglaje\n")
    assert "reglaje" in (extract(path).text or "")


def test_json_extractor(tmp_path: Path) -> None:
    path = write(tmp_path / "config.json", '{"clave": "almacenamiento"}')
    assert "almacenamiento" in (extract(path).text or "")


def test_xml_extractor(tmp_path: Path) -> None:
    path = write(tmp_path / "libro.xml", "<libro><titulo>Redes</titulo></libro>")
    assert "Redes" in (extract(path).text or "")


def test_source_code_extractor(tmp_path: Path) -> None:
    path = write(tmp_path / "app.py", "def calcular_impedancia():\n    return 42\n")
    assert "calcular_impedancia" in (extract(path).text or "")


def test_pdf_extractor(tmp_path: Path) -> None:
    path = write(tmp_path / "informe.pdf", make_pdf("BJT biasing class A amplifier"))
    result = extract(path)
    assert result.error is None
    assert "BJT" in (result.text or "")
    assert "amplifier" in (result.text or "")


def test_docx_extractor(tmp_path: Path) -> None:
    path = write(tmp_path / "apuntes.docx", make_docx("Ebers-Moll model derivación"))
    result = extract(path)
    assert result.error is None
    assert "Ebers-Moll" in (result.text or "")
    assert "derivación" in (result.text or "")


def test_xlsx_extractor(tmp_path: Path) -> None:
    path = write(
        tmp_path / "datos.xlsx",
        make_xlsx([["materia", "nota"], ["Seguridad", "9.5"]], sheet_name="Notas"),
    )
    result = extract(path)
    assert result.error is None
    assert "Seguridad" in (result.text or "")
    assert "9.5" in (result.text or "")
    assert "Notas" in (result.text or "")  # sheet names carry context


def test_pptx_extractor(tmp_path: Path) -> None:
    path = write(tmp_path / "clase.pptx", make_pptx("Filas y columnas de una matriz"))
    result = extract(path)
    assert result.error is None
    assert "Filas y columnas" in (result.text or "")


# -- registry ----------------------------------------------------------------

def test_every_target_format_is_registered() -> None:
    for extension in (
        ".txt", ".md", ".csv", ".json", ".xml", ".py",
        ".pdf", ".docx", ".xlsx", ".pptx",
    ):
        assert supports(extension), extension
        assert extension in SUPPORTED_EXTENSIONS


def test_unsupported_binary_is_never_opened(tmp_path: Path, monkeypatch) -> None:
    path = write(tmp_path / "image.png", b"\x89PNG not text at all")

    def explode(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("binary file must not be opened")

    monkeypatch.setattr(Path, "open", explode)
    result = extract(path)

    assert result == ExtractionResult()  # no text, no error


# -- corrupt files -----------------------------------------------------------

def test_corrupt_pdf_reports_error(tmp_path: Path) -> None:
    path = write(tmp_path / "broken.pdf", b"%PDF-1.4\nnot really a pdf structure \x00")
    result = extract(path)
    assert result.text is None
    assert result.error is not None


def test_corrupt_docx_reports_error(tmp_path: Path) -> None:
    path = write(tmp_path / "broken.docx", b"PK\x03\x04 this is not a zip")
    result = extract(path)
    assert result.text is None
    assert result.error is not None


def test_xlsx_with_malformed_xml_reports_error(tmp_path: Path) -> None:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", "<sst><broken></sst>")
    path = write(tmp_path / "broken.xlsx", buffer.getvalue())
    result = extract(path)
    assert result.text is None
    assert result.error is not None


def test_corrupt_pptx_reports_error(tmp_path: Path) -> None:
    path = write(tmp_path / "broken.pptx", b"not a zip archive at all")
    result = extract(path)
    assert result.text is None
    assert result.error is not None


def test_extraction_failure_does_not_stop_indexing(tmp_path: Path) -> None:
    root = tmp_path / "files"
    write(root / "broken.pdf", b"%PDF-1.4 garbage \x00\x01")
    write(root / "good.md", "contenido localizable")
    database = SearchDatabase(tmp_path / "index" / "search.db")

    stats = Indexer(database).index_root(root)

    assert stats.extraction_errors == 1
    assert stats.errors == 0
    assert stats.created == 2  # the corrupt file is still indexed by name
    engine = SearchEngine(database)
    assert len(engine.search("contenido")) == 1
    assert [r.name for r in engine.search("broken")] == ["broken.pdf"]


# -- empty files -------------------------------------------------------------

def test_empty_text_file_is_empty_string(tmp_path: Path) -> None:
    path = write(tmp_path / "empty.txt", b"")
    result = extract(path)
    assert result.text == ""
    assert result.error is None


def test_empty_pdf_reports_error(tmp_path: Path) -> None:
    path = write(tmp_path / "empty.pdf", b"")
    result = extract(path)
    assert result.text is None
    assert result.error is not None


def test_empty_docx_reports_error(tmp_path: Path) -> None:
    path = write(tmp_path / "empty.docx", b"")
    result = extract(path)
    assert result.text is None
    assert result.error is not None


# -- unicode / BOM -----------------------------------------------------------

def test_byte_order_mark_and_unicode_survive(tmp_path: Path) -> None:
    path = tmp_path / "unicode.md"
    path.write_bytes("\ufeff# Encabezado\nseñal ñ á é ¿? ✓".encode("utf-8"))
    result = extract(path)
    assert result.text == "# Encabezado\nseñal ñ á é ¿? ✓"


def test_nul_bytes_are_stripped_from_text(tmp_path: Path) -> None:
    path = tmp_path / "nul.txt"
    path.write_bytes(b"before\x00after")
    assert extract(path).text == "beforeafter"


def test_office_document_preserves_accents(tmp_path: Path) -> None:
    path = write(tmp_path / "acento.docx", make_docx("asignaciónación óptima"))
    assert "asignación" in (extract(path).text or "")


# -- bounds ------------------------------------------------------------------

def test_huge_text_file_is_truncated_not_loaded(tmp_path: Path) -> None:
    path = tmp_path / "huge.txt"
    path.write_text("a" * (MAX_CONTENT_CHARS + 5_000), encoding="utf-8")
    result = extract(path)
    assert result.text is not None
    assert len(result.text) == MAX_CONTENT_CHARS


# -- end-to-end: search finds terms inside binary documents -------------------

def test_search_finds_terms_inside_binary_formats(tmp_path: Path, row_count) -> None:
    root = tmp_path / "files"
    write(root / "tema.pdf", make_pdf("osciloscopio de laboratorio"))
    write(root / "manual.docx", make_docx("protocolo de calibración"))
    write(root / "notas.xlsx", make_xlsx([["ramas del diodo", "zener"]]))
    write(root / "clase.pptx", make_pptx("puente de Wheatstone"))
    database = SearchDatabase(tmp_path / "index" / "search.db")

    stats = Indexer(database).index_root(root)

    assert stats.created == 4
    assert stats.extraction_errors == 0
    assert row_count(database.path, "documents_fts") == 4
    engine = SearchEngine(database)
    assert [r.name for r in engine.search("osciloscopio")] == ["tema.pdf"]
    assert [r.name for r in engine.search("calibración")] == ["manual.docx"]
    assert [r.name for r in engine.search("zener")] == ["notas.xlsx"]
    assert [r.name for r in engine.search("Wheatstone")] == ["clase.pptx"]
