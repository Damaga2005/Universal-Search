# 050 — Universal Search v3 Product Gate

## Objective
Perform the second complete product-level quality gate after phases 041–049.

This phase is an evidence gate, not a feature sprint.

## Audit areas
Review:
- modern UX;
- search experience;
- settings;
- local learning;
- retrieval quality;
- indexing scalability;
- storage lifecycle;
- portability;
- installer/distribution;
- privacy/security;
- diagnostics/recovery;
- Windows integration;
- accessibility;
- documentation;
- release reproducibility.

## End-to-end scenario
Validate on a clean Windows environment:

1. install;
2. first launch;
3. configure sources;
4. initial indexing;
5. global search;
6. advanced query;
7. result selection;
8. open/reveal;
9. related documents;
10. settings;
11. change configuration;
12. modify source file;
13. background update;
14. restart;
15. maintenance;
16. upgrade;
17. uninstall.

## Search gate
Use fixed corpus and queries.

Measure:
- Precision@1/5/10;
- Recall@5/10;
- MRR;
- exact-match accuracy;
- filter correctness;
- zero-result behavior;
- interactive latency.

## Performance gate
Compare with historical benchmarks.

Measure:
- cold/warm search;
- p50/p95;
- indexing throughput;
- rescans;
- peak RAM;
- database size;
- derived-data cost;
- startup time.

## Reliability gate
Verify:
- no duplicate workers;
- clean shutdown;
- stale-state recovery;
- interrupted indexing;
- database migration;
- maintenance interruption;
- deterministic rebuild.

## Security/privacy gate
Test:
- malformed documents;
- path/reparse boundaries;
- SQL/FTS injection;
- logs/redaction;
- privacy forget;
- provider failure;
- installer artifacts;
- update authenticity;
- absence of document upload.

## Release decision
Produce:
- exact environment;
- exact commit;
- commands;
- evidence;
- metrics;
- regressions;
- limitations;
- deferred work;
- architectural debt;
- release recommendation based on explicit gates.

Do not produce subjective rankings or scores.

## Acceptance
The gate passes only when every area has evidence. Any failure is classified as blocker, release limitation, acceptable debt or future work, with supporting evidence.

## Ready-to-copy implementation prompt
Implement Phase 050 — Universal Search v3 Product Gate. Do not begin by adding features. Audit the complete product after phases 041–049 using a clean Windows environment, fixed search corpus and reproducible performance measurements. Validate modern UX, search quality, settings, learning, scalability, storage, portability, installation, privacy/security, recovery, accessibility and documentation. Produce an evidence-based gate report with exact commands and results. Fix only clearly in-scope blockers, rerun affected validation and leave the repository clean. Do not push unless explicitly instructed.
