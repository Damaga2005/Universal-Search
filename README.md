# Universal Search

Local-first universal search for Windows.

Universal Search indexes local files and cloud-backed locations such as OneDrive, then provides fast full-text and metadata search from a lightweight desktop application.

## Principles

- Local-first and privacy-preserving.
- No AI or paid API required.
- Fast incremental indexing.
- Provider-agnostic architecture.
- Search ranking based on filename, content, context and later optional local usage signals.
- Windows desktop application with a background indexer.

## Initial scope

1. Local filesystem indexing.
2. SQLite + FTS5 search index.
3. Incremental updates.
4. Text/Markdown/JSON/CSV/XML extraction.
5. CLI for development and diagnostics.
6. Desktop GUI.
7. OneDrive providers.

## Status

Early architecture phase — v0.1.

## Usage

```bash
pip install -e .
universal-search index C:\Users\me\Documents
universal-search search "meeting notes"
```

The index is stored in `universal-search.db` by default; change it with `--database <path>`.
Search results show the source, path, file name and a content snippet.

## Development

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e . pytest
.venv\Scripts\python -m pytest
```

Development prompts live in `docs/development/`.
