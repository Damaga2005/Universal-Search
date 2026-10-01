# 050 — Universal Search 3.x Product Gate

## Objective
Perform the complete product-level evidence gate after phases 041–049.

This phase is a gate, not a feature sprint and not a subjective score.

## Audit areas
Review:

- product UX;
- interactive search experience;
- settings/configuration;
- local learning;
- search quality and relevance;
- indexing scalability;
- storage lifecycle;
- Windows/environment support;
- distribution and installation;
- privacy/security;
- diagnostics/recovery;
- Windows integration;
- accessibility;
- documentation;
- release reproducibility.

The gate must distinguish inherited capabilities from new 041–049 deliverables.

## End-to-end scenario
Validate on a clean supported Windows environment:

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
12. modify a source file;
13. background update;
14. restart;
15. maintenance;
16. upgrade;
17. repair if supported;
18. uninstall.

## Search gate
Use the fixed versioned corpus and queries.

Measure:

- Precision@1/5/10;
- Recall@5/10;
- MRR;
- exact-match accuracy;
- filter correctness;
- zero-result behavior;
- interactive latency.

## Performance gate
Compare with official historical benchmarks.

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
- interrupted maintenance;
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
- authenticity claims;
- absence of document upload.

## Gate result
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
- blocker/release-limitation classification.

Do not produce subjective rankings, scores or overall quality ratings.

The result must be an evidence-based release state: all required gates pass, or the release is held with documented blockers/limitations.

## Acceptance
Every required area has evidence. Any failure is classified as blocker, release limitation, acceptable debt or future work, with supporting evidence. The repository is left clean after validation.

## Ready-to-copy implementation prompt
Implement Phase 050 — Universal Search 3.x Product Gate. Do not begin by adding features. Audit the complete product after phases 041–049 using a clean supported Windows environment, a fixed versioned search corpus and reproducible performance measurements. Validate UX, search quality, settings, learning, scalability, storage, environment support, installation, privacy/security, recovery, accessibility and documentation. Produce an evidence-based gate report with exact commands and results. Fix only clearly in-scope blockers, rerun affected validation and leave the repository clean. Do not push unless explicitly instructed.
