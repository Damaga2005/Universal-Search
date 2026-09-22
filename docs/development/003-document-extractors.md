# Development Prompt 003 — Document Extractors

## Objective

Expand searchable content beyond plain text files.

## Target formats

First-class extraction targets:

- PDF
- DOCX
- XLSX
- PPTX
- TXT
- Markdown
- CSV
- JSON
- XML
- source code

## Architecture

Create an extractor abstraction:

    file -> extractor selection -> extracted text -> index

The provider discovers files. The extractor determines whether and how their content can be read.

## Requirements

- Extractors must be independently testable.
- Unsupported formats must fail gracefully.
- Extraction errors must not stop an indexing run.
- Large files must not cause uncontrolled memory growth.
- Metadata and extracted text must remain separate concepts.
- Preserve enough information to generate useful search snippets later.

## Constraints

Do not introduce OCR yet unless explicitly required by the implementation plan.

Do not add external cloud APIs.

## Acceptance criteria

- Representative fixtures exist for every supported document format.
- Search can find terms inside PDF, DOCX, XLSX and PPTX.
- Extraction failures are recorded without crashing the indexer.
- Existing text-file behavior remains unchanged.

## Prompt

Implement the document extractor subsystem. Inspect the current provider/indexer contracts first and preserve their separation. Add format-specific extractors, a registry/selection mechanism, tests with representative fixtures, graceful error handling and documentation. Do not implement ranking or GUI in this milestone.
