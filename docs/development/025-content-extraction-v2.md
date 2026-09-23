# 025 — Content Extraction v2

## Objective
Make extraction broader, safer and more diagnosable while preserving deterministic local processing.

## Formats
Audit and harden:
- TXT
- Markdown
- CSV
- JSON
- XML
- source code
- PDF
- DOCX
- XLSX
- PPTX

Evaluate additional formats only when search value is demonstrated.

## Extraction contract
Each extractor exposes supported types, text extraction, metadata, status, warnings, resource limits and deterministic output. Extraction failures must never crash indexing.

## PDF and Office
Handle empty text layers, malformed/encrypted PDFs, large PDFs and unusual encodings. Preserve useful title, heading, sheet and slide structure without duplicating enormous metadata.

## Resource and security limits
Define limits for bytes, extracted characters, pages/sheets/slides, time and temporary storage. Protect against malformed archives, decompression bombs, traversal and excessive memory use. Truncation must be visible in diagnostics.

Do not introduce OCR in this phase unless its value, resource model and security architecture are independently justified.

## Tests
Add malformed, adversarial and large fixtures. Test deterministic output, limits, warnings, failures and cancellation where supported.

## Acceptance
A malformed or oversized document cannot take down the indexer, and the user can determine why extraction was incomplete.

## Ready-to-copy implementation prompt
Implement Phase 025 — Content Extraction v2. Audit every extractor, establish a versioned extraction contract, improve PDF and Office robustness, preserve useful structure, add explicit resource limits and safe failure diagnostics, and build adversarial fixtures. Keep processing local and deterministic. Do not add OCR without evidence and a separate design justification. Run all tests plus extraction stress tests and document compatibility and limitations. Do not push unless explicitly instructed.
