# 030 — Universal Search v2 Quality Gate

## Objective
Perform the first complete product-level quality gate after the 020–029 roadmap block.

This is an evidence gate, not a feature sprint. Do not begin by adding unrelated features.

## Audit areas
Review:
- search correctness
- ranking
- query language
- indexing correctness
- incremental updates
- providers
- extraction
- document intelligence
- related-document graph
- background indexing
- tray
- Windows integration
- privacy/security
- migrations
- diagnostics/recovery
- packaging
- CI
- performance
- accessibility
- documentation

## End-to-end acceptance
Validate:
1. clean install
2. first launch
3. source configuration
4. initial index
5. global search
6. advanced query
7. ranking
8. snippet/open/reveal
9. file modification
10. file deletion
11. background update
12. restart
13. migration
14. diagnostics
15. uninstall

Use a fixed synthetic corpus containing exact filenames, technical PDFs, Markdown, source code, BJT/Ebers-Moll, CMOS, MUX, unrelated files, duplicates, malformed documents and inaccessible sources.

## Performance
Record cold/warm search, p50, p95, worst-case, indexing throughput, rescans, memory, database size and derived-data/graph costs. Compare with the previous official benchmark. Use measured historical thresholds as regression gates; do not invent unsupported SLOs.

## Search quality
Measure Precision@K, Recall@K, MRR, exact-match correctness, filters and parser correctness on fixed queries. Record regressions explicitly.

## Security
Test malformed files, traversal, reparse/symlink boundaries, FTS/SQL injection attempts, corrupt DB, interrupted migration, malicious archive-like input, log redaction, provider failures and permission boundaries.

## Reliability
Verify no duplicate workers, clean shutdown, crash recovery, stale-lock recovery, interrupted-index recovery, deterministic rebuilds and zero modification/deletion of source files by index maintenance.

## Final report
Produce an evidence-based report with:
- implemented capabilities
- exact commands
- measured results
- regressions
- known limitations
- architectural debt
- deferred items
- reproducibility instructions
- next roadmap candidates

Classify failures as blocker, release limitation, acceptable debt or future work, with evidence.

## Acceptance
The gate is complete only when each area has evidence rather than implementation claims. Fix blockers that are clearly in scope, rerun affected validation and leave the repository clean.

## Ready-to-copy implementation prompt
Implement Phase 030 — Universal Search v2 Quality Gate. Do not start by adding features. Audit the complete repository and run reproducible end-to-end validation using fixed corpora and historical benchmarks. Verify search quality, indexing, providers, extraction, intelligence, graph, Windows integration, privacy/security, recovery, migrations, packaging, accessibility and performance. Produce a detailed gate report with commands, exact results, regressions and limitations. Fix clearly in-scope blockers, rerun affected validation and leave the repository clean. Do not push unless explicitly instructed.
