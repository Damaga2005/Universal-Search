# 045 — Search Quality v3

## Objective
Improve retrieval quality using evidence from real failure cases rather than adding arbitrary ranking signals.

## Evaluation
Expand the fixed evaluation corpus with:
- technical university documents;
- code;
- PDFs;
- Office documents;
- duplicated material;
- short documents;
- long documents;
- filenames with abbreviations;
- multilingual documents where supported.

Measure:
- Precision@1/5/10;
- Recall@5/10;
- MRR;
- exact-match accuracy;
- filter accuracy;
- zero-result accuracy.

## Error analysis
Classify misses:
- lexical mismatch;
- morphology;
- phrase handling;
- filename/path;
- extraction;
- ranking;
- filtering;
- semantic retrieval;
- stale index.

Every ranking change must correspond to an observed failure class.

## Regression control
Maintain a fixed baseline and prevent improvements in one class from silently degrading another.

## Acceptance
Search quality improves on documented failure cases without sacrificing exact-match correctness.

## Ready-to-copy implementation prompt
Implement Phase 045 — Search Quality v3. Perform evidence-driven retrieval improvement using a fixed evaluation corpus and explicit error taxonomy. Do not add ranking signals without measuring their effect. Report Precision@K, Recall@K, MRR, exact/filter correctness and zero-result behavior before and after each material change. Preserve exact-match guarantees and keep the evaluation reproducible. Do not push unless explicitly instructed.
