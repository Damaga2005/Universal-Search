# 014 — Local Document Intelligence

## Objective

Add deterministic local document analysis that improves discovery and relationships without cloud AI, paid APIs or external NLP services.

This is not a chat-with-documents feature.

## Mandatory context

Read extractor architecture, index schema, ranking, provider model, Phase 008 personal-context work if present, and reports through Phase 013.

## Features

Build a modular analysis pipeline capable of deriving, when available:

- detected language
- title/headings
- meaningful terms
- keywords
- section boundaries
- lightweight term co-occurrence
- related-document candidates
- lexical similarity features

Do not infer sensitive personal attributes.

## Local-only

No OpenAI API, remote LLM, cloud NLP, telemetry or remote embedding API.

Local embeddings may be considered only if clearly justified; prefer deterministic lexical analysis initially.

## Related documents

Support local queries such as:

- documents related to this document
- documents with similar concepts

Keep document similarity distinct from query relevance.

## Storage

Derived metadata must be versioned, bounded, rebuildable and deletable.

## Tests

Cover language, headings, keywords, empty/short/long documents, Unicode, similar/unrelated documents, deterministic repeated analysis and corrupted extractor output.

## Acceptance

- core search works without intelligence metadata
- intelligence is rebuildable
- results are deterministic
- related-document functionality is tested
- derived data can be invalidated/rebuilt
- no external service is required

## Ready-to-copy implementation prompt

Implement Phase 014 — Local Document Intelligence. Inspect the actual extractor/index/ranking architecture and add a modular, local, deterministic, rebuildable analysis pipeline for language, headings, meaningful terms, co-occurrence and lexical document relationships. Integrate only where measurable, add tests/documentation, and do not add cloud AI. Do not push.
