# 011 — Performance & Scalability

## Objective

Convert the existing Universal Search core into a measurable, scalable local search engine capable of handling substantially larger document collections without sacrificing result quality or the provider-agnostic architecture.

## Mandatory context

Read before coding:

- `docs/ROADMAP.md`
- `docs/ARCHITECTURE.md`
- `docs/development/001-foundation.md` through `010-global-search.md`
- all existing phase reports
- current database, indexer, extractor, ranking, GUI and background-indexer implementations

Adapt to the actual repository; do not assume the original plan was implemented literally.

## Requirements

### Performance baselines

Create reproducible benchmarks for:

- initial indexing
- incremental indexing
- single-document update
- deletion reconciliation
- search latency
- startup/database open
- memory usage during indexing
- memory usage during search

Use deterministic synthetic datasets. Include profiles for 1,000, 10,000 and 100,000 documents. Keep very large benchmarks separate from ordinary CI if necessary.

### SQLite and FTS

Audit and optimise, based on evidence:

- indexes
- FTS5 configuration
- transaction boundaries
- batching
- WAL/checkpoint behaviour
- connection lifecycle
- database growth
- maintenance/vacuum strategy

Do not introduce a database server.

### Indexing

The indexer must:

- batch writes
- avoid loading the whole corpus into memory
- avoid duplicate work
- maintain bounded memory
- remain crash-safe
- preserve incremental behaviour

Introduce parallelism only when benchmarks demonstrate a benefit and SQLite contention remains controlled.

### Search

Measure and optimise cold and warm search for:

- common terms
- exact phrases
- multi-term queries
- large result sets
- no-result queries

Results must remain deterministic.

### Caching and metrics

Introduce caching only where justified. Every cache needs bounded size, explicit invalidation and tests.

Expose local metrics for indexing duration, files scanned/changed/skipped, extraction failures, DB writes, search latency and result counts.

No telemetry leaves the machine.

## Tests and acceptance

Add unit/integration tests for large batches, concurrent search/indexing, interrupted work, cache invalidation, deterministic results and resource handling.

Add a benchmark suite separate from normal unit tests.

Acceptance:

- all existing tests pass
- large collections do not cause unbounded memory growth
- incremental work is materially cheaper than full reindexing
- search latency is measured
- optimisations have evidence
- no external service is required

## Deliverables

Implementation, tests, benchmarks, performance report, and updated architecture/roadmap documentation.

## Constraints

No AI, cloud search, Elasticsearch, Redis or Docker. Do not optimise blindly.

## Ready-to-copy implementation prompt

Implement Phase 011 — Performance & Scalability. Inspect the actual repository first, read all existing specifications/reports, establish baselines, then optimise indexing, SQLite/FTS, search and resource usage. Add deterministic benchmarks and regression tests, preserve provider/extractor/ranking separation, run the complete test suite, document measured before/after results, update architecture/roadmap documentation, create a dedicated commit, and do not push.
