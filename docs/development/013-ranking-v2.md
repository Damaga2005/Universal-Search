# 013 — Search Quality & Ranking v2

## Objective

Make ranking measurable, explainable, deterministic and tunable from evidence rather than scattered hand-tuned boosts.

## Mandatory context

Read the current ranking implementation, `docs/RANKING.md`, search parser, FTS/database schema, document model and all ranking tests/reports through Phase 012.

## Signals

Evaluate and combine:

- exact filename
- filename token overlap
- path overlap
- exact phrase
- term frequency
- document-length normalisation
- proximity
- BM25/FTS relevance
- extension/type
- source
- recency
- query structure
- filter matches

If personal usage signals already exist, keep them optional and separate from textual relevance.

## Explainability

Each result must be able to expose an inspectable score explanation containing, where applicable:

- total score
- filename contribution
- path contribution
- content contribution
- phrase match
- proximity
- type/source contribution
- recency
- optional personal signal

Normal UI does not need to show every component.

## Evaluation corpus

Create a synthetic corpus and labelled queries covering:

- `BJT`
- `Ebers Moll`
- `CMOS`
- `MUX`
- exact filename match
- content-heavy match
- path-only match
- accidental generic path match
- exact phrase
- unrelated documents

Do not commit private user documents.

## Metrics

Implement evaluation helpers for:

- Precision@K
- Recall@K
- MRR

Use measurements to guide changes. Do not present a subjective global winner.

## Robustness

Ranking must be deterministic, handle missing metadata and long documents, prevent path noise from dominating content, and maintain stable tie-breaking.

Centralise weights/configuration.

## Tests and acceptance

Add unit tests per signal, interaction tests, deterministic-order tests, metric tests and regression fixtures.

Acceptance:

- ranking is measurable and inspectable
- evaluation corpus exists
- tie-breaking is deterministic
- changes have regression coverage
- existing search remains functional
- no external AI/API is required

## Ready-to-copy implementation prompt

Implement Phase 013 — Search Quality & Ranking v2. Audit ranking, create a deterministic evaluation corpus, expose score explanations, centralise configuration, implement measurable signals and Precision@K/Recall@K/MRR evaluation, add regressions, update `docs/RANKING.md` and architecture docs, run all tests, and do not push.
