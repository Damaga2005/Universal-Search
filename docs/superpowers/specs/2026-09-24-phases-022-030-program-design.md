# Phases 022-030 Program Design

Date: 2026-09-24
Status: Approved design

## Goal

Complete the local-first Universal Search roadmap from phase 022 through phase
030 in strict order. Each phase is an independently reviewable delivery with
its own tests, report, synchronized documentation and one `feat:` commit.

The program is intentionally evidence-first. A phase may document a measured
decision not to add a feature when the evidence does not justify its cost.

## Program boundaries

The following constraints apply to every phase:

- No cloud service, remote inference, telemetry, external API, Elasticsearch,
  Redis, Docker or runtime network client.
- Keep domain, query parsing, ranking, indexing, extraction, providers and
  database logic platform-independent.
- Keep Windows APIs behind `src/universal_search/platforms/`.
- Preserve the existing provider, extractor, query, ranking, privacy and
  database contracts unless a phase explicitly versions them.
- Add runtime dependencies only when a measured requirement justifies them and
  document the license, size and removal path.
- All derived data is local, versioned, rebuildable and removable.
- Search ranking remains authoritative and explainable. Optional derived layers
  never change exact-match, filter or query-operator semantics.
- Maintenance and repair operations never delete physical source files.
- Keep the existing manual update strategy and do not claim signing or
  verification that was not performed.

## Execution model

Phases execute sequentially:

1. 022 Related-document graph
2. 023 Indexing UX and control center
3. 024 Provider expansion
4. 025 Content extraction v2
5. 026 Evidence-first local semantic search
6. 027 Windows shell integration
7. 028 Observability and recovery
8. 029 Release engineering and CI
9. 030 Universal Search v2 quality gate

Each phase follows this delivery loop:

1. Audit the current implementation and the phase prompt.
2. Write failing deterministic tests.
3. Implement the smallest coherent change.
4. Run focused tests, the full suite and pyflakes.
5. Add a phase report with measured results and limitations.
6. Synchronize `README.md`, `docs/README.md`, `docs/ROADMAP.md`,
   `docs/ARCHITECTURE.md` and `CHANGELOG.md` where relevant.
7. Commit exactly one `feat:` commit for that phase.
8. Do not push.

Phase 030 is a gate, not a feature sprint. It may fix only clearly in-scope
blockers and must leave a complete evidence report.

## Phase designs

### Phase 022: Related-document graph

Add a versioned derived graph beside document intelligence. Nodes reference
canonical indexed documents; edges contain type, bounded weight, evidence and a
generation version. Candidate generation uses inverted terms and explicit
limits for terms, candidates, minimum similarity and stored edges; it never
uses an unbounded all-pairs comparison.

Signals include normalized terms, weighted keyword overlap, titles/headings,
phrases, directory relationship, safe explicit references, lexical similarity
and provider/context relationship. Sensitive personal attributes are excluded.
The graph is not consulted by normal query ranking. Updates and deletions
maintain affected edges, while a deterministic full rebuild remains available.
The related-documents surface shows ranked evidence rather than a large graph
visualization.

### Phase 023: Indexing UX and control center

Add an operational service and a separate control-center window. Reuse the
provider registry, indexer lifecycle, diagnostics and derived-data APIs. The
surface shows sources, availability, counts, supported types, scan state,
pending work, failures, inaccessible paths, exclusions, health, derived-data
state and storage use.

Actions are explicit and service-owned: add/remove source, rescan, pause/resume,
retry failures, rebuild FTS, rebuild derived metadata, rebuild relationships and
full rebuild. Each destructive action has a distinct confirmation and states
whether it removes indexed records, derived data or source files. Only the
last category is ever outside the application's control and is never implied
by ordinary source removal.

### Phase 024: Provider expansion

Formalize provider capabilities for enumeration, metadata, content, identity,
change detection, availability, errors, optional watching and optional
streaming. Identity is provider-namespaced and stable across ordinary metadata
changes.

Retain local and OneDrive providers. Add network/NAS and removable-source
support only through the same contract, with root validation, reparse/symlink
rules, permission failures, disconnected states and bounded enumeration. Slow
or failing providers are isolated from unrelated providers. Cancellation and
batching are explicit interfaces, and mixed-provider indexing is tested with
fake providers.

### Phase 025: Content extraction v2

Introduce a versioned extraction result contract with supported type, text,
metadata, status, warnings, structure, resource usage and truncation state.
Harden text-like formats, source code, PDF and Office extractors against empty
text layers, malformed/encrypted input, unusual encodings, large files and
unsafe archives.

Enforce explicit limits for input bytes, extracted characters, pages, sheets,
slides, time and temporary storage. Traversal, decompression bombs and
excessive memory use fail safely and diagnostically. The indexer continues
when one document fails. OCR is not added.

### Phase 026: Evidence-first local semantic search

Start with a fixed representative corpus and measure the lexical baseline with
Precision@K, Recall@K, MRR, exact-match correctness and concrete synonym and
paraphrase failures.

If the measured gain from a local semantic layer is not material relative to
CPU, RAM, disk, indexing cost and maintenance, ship the measurement and
decision without embeddings. If justified, isolate model/provider, vector
storage, candidate generation, similarity and hybrid ranking behind optional
interfaces. Any model and vector index remain local, versioned, removable and
licensed. Lexical exact matches and query operators remain authoritative.

### Phase 027: Windows shell integration

Extend the platform adapter and packaging for configurable global invocation,
Start Menu and optional shortcuts, Explorer actions, open/reveal/copy path,
single-instance search, startup and DPI-aware behavior. Use per-user entries
where possible and make installation/uninstallation reversible.

`Ctrl+Space` is not a default when registration can interfere with common
Windows applications or IME. Shell commands call the existing core services;
they do not duplicate query, ranking or indexing logic. Tests cover injected
platform calls and real Windows smoke paths without requiring administrator
rights.

### Phase 028: Observability and recovery

Add structured local events with timestamp, severity, component, event ID and
sanitized error text. Rotation and size limits are mandatory. Default logs do
not contain document contents, credentials, access tokens or query text.

Provide self-tests for database read/write, FTS integrity, schema compatibility,
provider accessibility, extractor availability, worker coordination and storage
capacity. Recovery handles corrupt databases, incomplete migrations, interrupted
indexing, stale locks, worker crashes, provider disconnects, extractor failures
and disk-full conditions while preserving source files. A diagnostic export is
explicitly sanitized and describes its contents before creation.

### Phase 029: Release engineering and CI

Keep one authoritative version source for the package, executable, installer
and compatibility metadata. CI gates dependency installation, tests, warnings,
static checks, migrations, benchmark smoke, packaging, install/start/search
smoke and artifact validation. Windows-only checks run on Windows runners.

Document supported Python and Windows versions, dependency constraints, clean
build commands, artifact names, hashes, environment requirements, installer
and uninstaller behavior, and the absence of signing when applicable. Release
validation fails on regressions, drift, migration incompatibility, packaging
failure, startup/search failure or security failure.

### Phase 030: Universal Search v2 quality gate

Use a fixed synthetic corpus containing exact filenames, technical PDFs,
Markdown, source code, BJT/Ebers-Moll, CMOS, MUX, unrelated files, duplicates,
malformed documents and inaccessible sources. Run clean-install-style E2E
validation through first launch, source configuration, indexing, search,
advanced queries, open/reveal, modification/deletion, background update,
restart, migration, diagnostics and uninstall.

Record cold/warm search latency, p50/p95/worst case, indexing throughput,
rescan cost, memory, database size and derived-data/graph costs against the
previous official benchmark. Measure Precision@K, Recall@K, MRR, exact-match
correctness, filters and parser correctness. Run malformed-file, traversal,
reparse, SQL/FTS, corrupt-DB, interrupted-migration, archive-like, log-redaction,
provider-failure and permission tests. The final report classifies every
finding as blocker, release limitation, acceptable debt or future work.

## Cross-phase compatibility

- Existing query AST, filters, ranking weights, provider identities and database
  migrations remain covered by regression tests.
- New derived tables and schemas are additive and versioned; incompatible
  derived data is rebuilt rather than silently reinterpreted.
- CLI and GUI use shared services; no operational logic is duplicated in a
  window or tray.
- Privacy inventory covers every persisted operational file and export.
- Phase reports record exact commands, measured counts, hashes and limitations.

## Completion criteria

The program is complete when phases 022-029 each have a passing delivery gate,
phase 030 has an evidence-based report, all roadmap entries through 030 are
accurate, the repository is clean, and no push has been performed.
