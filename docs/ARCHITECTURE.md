# Architecture

Universal Search separates providers, extraction, indexing, ranking and presentation.

The first provider is the local filesystem. OneDrive will be added without changing the search engine.

SQLite stores metadata and SQLite FTS5 stores searchable text. The ranking layer will later combine filename, content, proximity, source/context and optional local usage signals.
