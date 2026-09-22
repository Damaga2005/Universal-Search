# Phase 003 — Document Extractors: Implementation Report

## What was implemented

- **Extractor subsystem** `src/universal_search/extractors/`:
  - `text.py` — TXT, Markdown, CSV, JSON, XML and source code (UTF-8-sig, `errors="replace"`, bounded at `MAX_CONTENT_CHARS = 2_000_000` characters, NUL/BOM normalization).
  - `pdf.py` — PDF via **pypdf** (lazy import); encrypted PDFs attempted with an empty password, broken pages skipped individually, output bounded.
  - `office.py` — DOCX / XLSX / PPTX parsed directly from their OOXML parts with `zipfile` + `xml.etree.ElementTree` (paragraph runs, shared strings + inline + numeric cells + sheet names, per-slide drawing text).
- **Registry**: `EXTRACTORS` maps extension → function; `extract(path)` never raises — unsupported formats return `(no text, no error)` *without opening the file*, and any unexpected exception is converted into an extraction error so an indexing run can never be stopped.
- **Pipeline wiring**: the provider now delegates content reading to the registry (`providers/local.read_local_content → extract`), keeping `Provider → Extractor → Document → Indexer` separation: discovery (`scan_local`) still yields metadata only.
- **Error accounting**: extraction failures increment `IndexStats.extraction_errors`; the file remains indexed by name/path so it stays findable.

## Technical decisions

1. **Exactly one new dependency: `pypdf>=6.0`** (pure Python, MIT, ~395 KB, no native build). PDF parsing by hand is unreliable against real-world fonts/streams.
2. OOXML formats are **stdlib-only** (they are ZIP+XML): python-docx / openpyxl / python-pptx were rejected — three heavy dependencies for what is ~150 lines of targeted XML reading.
3. Format detection is extension-based and case-insensitive; binaries are never sniffed as UTF-8 (verified by a test that makes `Path.open` raise for `.png`).
4. Normalization strips NUL and stray BOM characters; text-mode reading preserves Foundation behavior (universal newlines, UTF-8-sig) so all phase-001 tests pass unchanged.
5. Corrupt files report `error` instead of raising; `index_root` also has a defensive `except` around any reader (three layers of protection).

## Dependencies

| Added | Version | Why |
|---|---|---|
| `pypdf` | 6.19.0 | PDF text extraction (declared `pypdf>=6.0`) |
| `pyinstaller` | (optional extra `build`) | executable packaging for phase 005 |

## Limitations

- PDF extraction depends on the document's font encoding; scanned/image PDFs have no text layer (no OCR — out of scope by spec).
- XLSX numeric date cells appear as Excel serial numbers (styles are not interpreted).
- DOCX headers/footers/footnotes are not extracted (body document only).
- Extracted text is truncated at 2 M characters per file.

## Tests

`tests/test_extractors.py` (25 tests) + `tests/docfactories.py` (builders producing real, structurally valid PDF/DOCX/XLSX/PPTX bytes):
one test per format (TXT, MD, CSV, JSON, XML, source, PDF, DOCX, XLSX, PPTX); registry completeness; binary never opened; corrupt PDF/DOCX/XLSX/PPTX; extraction failure does not stop indexing; empty text/PDF/DOCX; Unicode+BOM; NUL stripping; accents in DOCX; huge-file truncation; end-to-end search finds unique terms inside PDF, DOCX, XLSX and PPTX.

## Acceptance criteria

- Representative fixtures for every format — `tests/docfactories.py` builds valid files for all ten formats.
- Search finds terms inside PDF/DOCX/XLSX/PPTX — `test_search_finds_terms_inside_binary_formats`.
- Extraction failures recorded without crashing — `test_extraction_failure_does_not_stop_indexing`.
- Existing text-file behavior unchanged — all 38 previous tests still pass.

## How to run

```bash
universal-search index <folder>      # binary formats now contribute content
.venv\Scripts\python -m pytest tests/test_extractors.py -v
```
