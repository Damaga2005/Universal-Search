# Phase 022 - Related-document graph

Status: complete (fix rounds 1, 2 and 3 included)

## Scope delivered

Phase 022 adds a local, deterministic relationship graph beside document
intelligence. The graph is derived from canonical indexed documents and can be
deleted or rebuilt without changing the search index.

The implementation is in `src/universal_search/intelligence/graph.py`:

- `GraphStore.rebuild(documents)` performs an atomic deterministic rebuild.
- `GraphStore.related(document_id, limit=10)` reads stored edges only and
  returns `RelatedDocument` objects with evidence.
- `GraphStore.invalidate(document_ids)` marks durable dirty state and repairs
  only a bounded affected neighbourhood.
- `DocumentRecord` is the explicit boundary between indexed metadata, bounded
  analysis signals, and the graph.
- Graph schema version 2 and preprocessing version 2 are stored on every node,
  posting, and edge row, alongside a generation label and timestamps.

The existing phase-014 `related(database, reference)` adapter remains
compatible. `SearchEngine`, query parsing, and ranking were not changed and
never read graph tables.

## Relationship signals and recovery

Edges are stored as one canonical undirected row per pair. `edge_type` is the
strongest deterministic signal; `evidence` contains all non-zero bounded
signals. Signals include normalized terms, weighted keyword overlap,
title/heading overlap, phrase overlap, directory relationship, safe explicit
references in either direction, lexical similarity, and provider/context
relationship. No personal attributes, network calls, model inference, or cloud
data are used.

Canonical index writes mark `document_graph_metadata` dirty keys in the same
SQLite transaction as the canonical row. A failed post-commit invalidation
therefore cannot silently leave stale graph rows: `related()` and the service
repair dirty state before reading. Large dirty sets collapse to a bounded
`dirty:*` marker and defer a full rebuild. Re-extract and FTS repair update the
canonical content hash and mark graph state dirty; intelligence rebuild also
verifies the FTS content hash, not only stored metadata.

Incremental repair reads only changed ids and a capped candidate window from
bounded postings/old edges. It does not preprocess the entire corpus. A
version or preprocessing mismatch still takes the deterministic full-rebuild
path. FTS repair cursor-pages orphan and missing rows in explicit batches,
scrubs each batch once, and commits the complete repair atomically. Orphan
name, path, stem, id, direct references, reverse references, and dirty markers
are handled together without touching source files.

Direct and reverse `references:<document-id>` metadata is scrubbed when a
document is forgotten or deleted. The scrub is part of the same transaction as
canonical and graph deletion, and source files are never touched.

## Storage and bounds

The additive Phase 022 tables are `document_graph_nodes`,
`document_graph_terms`, `document_graph_edges`, and
`document_graph_metadata`. Posting lists and alias lookups are capped before
storage and before retrieval.

Alias, mention, and declared ids are merged through a fixed-size candidate
accumulator. The accumulator is bounded before sorting and before comparison;
no corpus-sized candidate `Counter` is constructed. Mention lookup work and
retained ids have separate hard caps.

| Bound | Value |
|---|---:|
| Graph schema version | 2 |
| Preprocessing version | 2 |
| Graph terms per document | 32 |
| Stored postings per term | 64 |
| Candidate documents per source | 64 |
| Aggregate candidate ids per source | 256 |
| Alias lookup windows per document | 2,048 |
| Orphan cleanup batch size | 128 |
| Stored edges incident to a document | 32 |
| Global stored-edge ceiling | 100,000 |
| References retained per document | 16 |
| Minimum edge similarity | 0.12 |
| Signal text sample | 200,000 characters |
| Phrase candidates examined | 512 |

Candidate generation uses bounded inverted terms/phrases, capped directory
hints, and capped safe aliases. A full rebuild compares at most
`documents * 64` ordinary pairs rather than `n * (n - 1) / 2`.

## Service and GUI

`SearchService.related()` accepts a document id, path, or name and returns the
ranked evidence list without exposing SQL to the window. The Tk window adds
`Diagnostic -> Documentos relacionados...`, but the lookup and first rebuild
run on a worker thread with a generation-aware queue; the main thread only
renders the small ranked list and first evidence signals. There is no large
graph visualization.

## TDD and fix-round evidence

The original RED command was run before the first implementation:

```text
.venv\Scripts\python -m pytest tests\test_related_graph.py -q -o addopts=
```

It failed during collection with the expected missing `DocumentRecord` import.
Fix round 1 added failing regressions for posting bounds, dirty-marker
recovery, hash invalidation, bounded incremental reads, symmetric references,
and asynchronous GUI lookup.

Fix round 2 added failing regressions before production edits. The graph test
first failed collection on the missing aggregate-cap constant; the FTS repair
test failed because the canonical hash stayed stale; privacy and ordinary
deletion tests failed because reverse reference metadata survived. The
candidate implementation was then changed to a fixed-size accumulator, the
FTS repair transaction was changed to update hashes and mark dirty state, and
deletion paths were changed to scrub direct and reverse references.

Fix round 3 added failing orphan regressions before production edits. The
repair test first failed collection on the missing batch-cap constant. After
that, the alias regression demonstrated that name/path reverse references and
`dirty:<orphan_id>` survived, and the large-orphan regression demonstrated one
full scrub call per orphan. The repair path was changed to cursor paging, a
128-row batch cap, one scrub per batch, bulk graph/FTS deletion, and one final
transaction commit.

Final focused commands and results:

| Command | Result |
|---|---|
| `.venv\Scripts\python -m pytest tests\test_related_graph.py tests\test_intelligence.py tests\test_incremental.py -q -o addopts=` | **84 passed** |
| `.venv\Scripts\python -m pytest tests\test_diagnostics.py tests\test_privacy.py tests\test_reliability.py -q -o addopts=` | **68 passed, 1 skipped** |
| `.venv\Scripts\python -m pytest tests\test_gui.py tests\test_gui_services.py -q -o addopts=` | **37 passed** |
| `.venv\Scripts\python -m pytest tests\ -q -o addopts=` | **681 passed, 2 skipped** |
| `.venv\Scripts\python -m pyflakes src tests benchmarks evaluation` | exit 0, no output |

A direct schema-5 to schema-6 test verifies that canonical documents and FTS
rows survive while the graph tables/metadata are created.

## Measured graph size and performance

Measurement command, run three times on Windows with Python 3.14:

```text
.venv\Scripts\python %TEMP%\opencode\measure_phase022_graph.py
```

The deterministic corpus contained 1,000 documents. Results were:

- 1,000 nodes, 1,481 stored edges, 4,152 capped term postings.
- 4,149,248 total database/WAL/SHM bytes; 1,013 SQLite pages at 4,096 bytes.
- 64,000 candidates considered and 2,016 pair comparisons.
- 0 alias values read for the measurement corpus; alias-heavy behavior is
  covered by the dedicated aggregate-cap regression.
- Full rebuild: 1,695.936-2,164.538 ms across three runs.
- Related lookup samples: 11.843-17.661 ms; maximum observed 17.661 ms.
- Two high-ID sample documents had no retained neighbour because the bounded
  edge budget discarded their weakest ties; this is an explicit recall
  tradeoff, not an all-pairs fallback.

The measurement is evidence rather than an SLO. Rebuild is explicit; normal
indexing only marks dirty state and performs bounded repairs when a graph is
already present.

## Privacy and compatibility

The graph tables and metadata are declared in `privacy.py::INVENTORY` and
`docs/PRIVACY.md`. `intelligence clear`, `privacy forget`, index deletion, and
full database rebuild remove graph data. Reference metadata is rewritten or
removed for direct and reverse references in the same deletion transaction.
The existing intelligence API keeps its `name`, `path`, and `shared_terms` view
while adding the phase-022 `evidence` tuple. No runtime dependency was added.

## Self-review

- Confirmed posting storage, alias lookup, aggregate counters, candidate
  lists, and edge lists are all explicitly bounded.
- Confirmed alias/mention/declared ids never build or sort an unbounded
  candidate `Counter`.
- Confirmed failed canonical post-commit invalidation leaves a durable marker
  and a later related lookup repairs it.
- Confirmed intelligence rebuild, re-extract, and FTS repair react to actual
  content hashes and transactionally mark graph state dirty.
- Confirmed one-document invalidation reads a bounded candidate window and
  does not load the whole corpus.
- Confirmed higher-id explicit references are symmetric and survive
  incremental invalidation in either direction.
- Confirmed privacy forget and ordinary deletion scrub direct and reverse
  reference metadata without touching source files.
- Confirmed FTS orphan cleanup is cursor-paged, batch-capped, alias-aware,
  atomic, and removes orphan dirty markers in the same transaction.
- Confirmed GUI graph work runs off the Tk thread and stale generations are
  discarded.
- Confirmed graph tables are never referenced by `SearchEngine` or ranking.
- Confirmed SQL values for ids, paths, and special-character names are bound.
- Confirmed v5-to-v6 migration preserves canonical and FTS rows.
- Confirmed privacy inventory includes graph metadata and deletion paths.
- Confirmed pyflakes and the complete regression suite are clean.

## Concerns and limitations

1. Relationship scoring is lexical and explainable, not semantic. Paraphrases
   with no shared normalized signal remain unrelated by design.
2. Candidate, posting, aggregate, and per-node edge caps can omit weak
   neighbours in very large or highly clustered corpora.
3. Full graph rebuild is measured separately; users who do not request
   related documents do not pay this cost.
4. Reference reverse lookup is itself capped, so a very distant reference in
   a huge corpus may require a full rebuild for recall.
5. FTS repair scans reference metadata once per bounded batch rather than once
   per orphan; very large damaged tables can still take time to repair.
6. No semantic model, cloud inference, network client, or large graph
   visualization was added. Those remain out of scope for phase 022.
7. SQLite timestamps use wall-clock time; logical ordering, generation,
   version, score, and evidence are deterministic.

Commit: recorded in the task report (the task controller may squash the
fix commit into the final Phase 022 feature commit).
