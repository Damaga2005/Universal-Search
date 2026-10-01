# 044 — Local Learning v2

## Objective
Evolve the local usage-learning capability introduced earlier into a bounded, measurable and user-controlled ranking signal.

This is not a new AI system and not a recommendation engine.

## Signals
Evaluate only local signals with measurable value, such as:

- selected result;
- opened result;
- repeated query;
- source preference;
- document-type preference;
- recency.

All signals remain local and optional.

## Ranking contract
Learning is always secondary to explicit search intent.

Rules:

- exact filename/phrase matches remain dominant;
- explicit filters override learned preference;
- learning cannot permanently bury new documents;
- deterministic baseline ranking remains available;
- learning can be disabled;
- learning data can be inspected and deleted;
- a material learning-driven change should be explainable.

Do not introduce opaque embeddings, cloud profiles or remote telemetry.

## Cold start
Define deterministic behavior for users with no interaction history.

## Evaluation
Use synthetic interaction histories and the fixed retrieval corpus to measure:

- ranking changes;
- stability;
- cold start;
- forgetting;
- disabled mode;
- exact-match protection;
- whether learning actually improves repeated workflows.

No learning change ships without evidence of benefit and regression checks.

## Acceptance
Local learning improves repeated workflows where measurable, while remaining bounded, inspectable, reversible and subordinate to explicit search intent.

## Ready-to-copy implementation prompt
Implement Phase 044 — Local Learning v2. Audit the existing usage-learning signals and evolve them only where measurable benefit exists. Keep exact matches and explicit filters dominant, support disable/inspect/delete, define cold-start behavior and evaluate synthetic histories against the fixed corpus. No telemetry, cloud profile or opaque recommendation model. Do not push unless explicitly instructed.
