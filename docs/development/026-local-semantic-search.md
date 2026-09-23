# 026 — Local Semantic Search Without Cloud AI

## Objective
Evaluate whether an optional local semantic retrieval layer materially improves search quality. This is an evidence-first phase, not a requirement to add embeddings.

## Baseline first
Build a fixed representative corpus and measure the current lexical engine using Precision@K, Recall@K and MRR. Identify concrete synonym/paraphrase failures.

If lexical search already satisfies the product goals, document that result and do not add semantic complexity.

## Architecture if justified
Isolate:
- embedding model/provider
- vector storage
- candidate generation
- similarity
- hybrid ranking

The application must continue to work with semantic search disabled.

## Locality
Any model and index must be local, versioned, removable and covered by licensing documentation. No remote inference, document upload or telemetry.

## Ranking
Semantic similarity is only one signal. Exact filenames, exact phrases, filters and explicit operators must remain authoritative.

## Resource constraints
Measure CPU, RAM, disk and indexing cost. Support disabling semantic indexing. Version model, preprocessing and vector dimensions; incompatible derived data must be rebuilt safely.

## Tests
Compare lexical-only and hybrid retrieval on the same corpus. Cover exact matches, synonyms, paraphrases, technical terms, short queries, filters, malformed content and disabled mode.

## Acceptance
Either ship a measured local semantic layer or document evidence that its complexity/cost is not justified.

## Ready-to-copy implementation prompt
Implement Phase 026 — Local Semantic Search Without Cloud AI. Start with reproducible evaluation of the existing lexical engine and only add a local semantic layer if measured gains justify its resource and maintenance cost. Keep it optional, local, versioned and removable. Compare hybrid and lexical retrieval on a fixed corpus, preserve exact-match and query-operator behavior, document the decision with numbers, and add regression tests. Do not use external APIs or upload document content. Do not push unless explicitly instructed.
