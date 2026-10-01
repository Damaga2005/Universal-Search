# 036 — Indexing Performance v3

## Objective
Scale Universal Search to substantially larger personal corpora while preserving responsiveness and bounded resource usage.

## Evaluation
Benchmark:
- 1k documents;
- 10k;
- 100k where practical;
- mixed file sizes;
- many directories;
- multiple providers;
- large Office/PDF documents.

Measure:
- initial indexing;
- incremental indexing;
- rescans;
- deletion;
- extraction throughput;
- graph/derived-data work;
- peak RAM;
- database size;
- CPU;
- cancellation/recovery time.

## Architecture
Use measured bottlenecks to improve:
- batching;
- connection management;
- WAL/checkpoint behavior;
- extraction scheduling;
- derived-data scheduling;
- provider concurrency;
- backpressure.

Do not add concurrency blindly.

## Acceptance
Performance remains predictable as corpus size grows, with explicit resource bounds and no correctness regressions.

## Ready-to-copy implementation prompt
Implement Phase 036 — Indexing Performance v3. Build a reproducible large-corpus benchmark and optimize only measured bottlenecks across indexing, extraction, providers and derived data. Measure CPU, RAM, database growth, throughput, cancellation and recovery. Preserve deterministic results and bounded resource use. Compare against previous official benchmarks and document every material optimization. Do not introduce external infrastructure. Do not push unless explicitly instructed.
