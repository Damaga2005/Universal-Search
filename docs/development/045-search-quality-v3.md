# 045 — Search Quality & Relevance

## Objective
Improve retrieval quality only in response to reproducible failure cases discovered after the 041–044 product work.

This phase extends the existing evaluation and ranking infrastructure from phases 013, 030, 038 and 040. It must not become a second generic ranking redesign.

## Evaluation
Maintain a fixed, versioned evaluation corpus covering relevant Universal Search workloads:

- university/technical documents;
- code;
- PDFs and Office documents;
- duplicated material;
- short and long documents;
- filenames with abbreviations;
- multilingual documents where supported;
- realistic local search queries.

Measure:

- Precision@1/5/10;
- Recall@5/10;
- MRR;
- exact-match accuracy;
- filter accuracy;
- zero-result accuracy;
- interactive search latency where relevant.

## Error analysis
Classify misses as:

- lexical mismatch;
- morphology/fuzzy behavior;
- phrase handling;
- filename/path;
- extraction;
- ranking;
- filtering;
- semantic fallback;
- stale index;
- interaction/UI behavior.

Every material search-quality change must correspond to an observed failure class.

## Regression control
Maintain a fixed baseline and prevent improvements in one class from silently degrading another.

Exact filename and explicit query intent remain protected.

If no reproducible failure justifies a ranking change, do not change the ranking.

## Acceptance
Documented search failures are reduced without sacrificing exact-match correctness, filter correctness or deterministic behavior.

## Ready-to-copy implementation prompt
Implement Phase 045 — Search Quality & Relevance. Use the existing evaluation corpus and extend it only for justified missing cases. Classify real failures before changing retrieval or ranking. Report Precision@K, Recall@K, MRR, exact/filter correctness and zero-result behavior before and after every material change. Preserve exact-match guarantees and reproducibility. If evidence does not justify a ranking change, leave the ranking unchanged. Do not push unless explicitly instructed.
