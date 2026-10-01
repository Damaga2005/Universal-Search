# 046 — Indexing Scalability & Performance

## Objective
Scale Universal Search to substantially larger personal corpora while preserving correctness, responsiveness and bounded resource usage.

This phase extends the reproducible performance foundations from phases 011 and 038; it is not a second general performance audit.

## Evaluation
Benchmark, where practical:

- 1k documents;
- 10k;
- 100k;
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
- provider throughput;
- graph/derived-data work;
- peak RAM;
- database size;
- CPU;
- cancellation;
- recovery time.

## Architecture
Optimize only measured bottlenecks, including where evidence supports it:

- batching;
- database connection management;
- WAL/checkpoint behavior;
- extraction scheduling;
- derived-data scheduling;
- provider concurrency;
- backpressure.

Do not add concurrency blindly.

## Correctness
Every optimization must preserve:

- deterministic indexing outcomes;
- provider isolation;
- cancellation semantics;
- recovery behavior;
- search correctness;
- privacy guarantees.

## Acceptance
Performance remains predictable as corpus size grows, with explicit resource bounds and no correctness regressions.

## Ready-to-copy implementation prompt
Implement Phase 046 — Indexing Scalability & Performance. Extend the existing reproducible benchmark to larger corpora and optimize only measured bottlenecks across indexing, extraction, providers and derived data. Measure CPU, RAM, database growth, throughput, cancellation and recovery. Preserve deterministic results and bounded resource use. Compare against the official historical benchmarks and document every material optimization. Do not introduce external infrastructure. Do not push unless explicitly instructed.
