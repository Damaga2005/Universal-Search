# Search Ranking

Universal Search ranks with an explicit, deterministic formula. There is no
AI, no embeddings, no network service and no personalization in this layer.

## Formula

```
score = Σ (weightᵢ × signalᵢ) / Σ (weightᵢ)        Σ weightᵢ = 14.0
```

Every signal is normalized to `[0, 1]`, therefore every score lies in
`[0, 1]`. Ties are broken by ascending path, so results are reproducible.

## Signals and weights

| # | Signal | Weight | Definition |
|---|--------|-------:|------------|
| 1 | `filename_exact` | 3.0 | Query equals the file name or its stem (case-folded) |
| 2 | `filename_tokens` | 2.0 | Fraction of query terms present as tokens of the name |
| 3 | `phrase_exact` | 2.0 | Query terms appear adjacent, in order, in the content |
| 4 | `term_freq` | 1.5 | Mean per-term frequency in content, saturating at 10 occurrences |
| 5 | `proximity` | 1.5 | Tightest window containing all terms: `terms / window`, capped at 1 |
| 6 | `bm25` | 2.0 | FTS5 relevance mapped monotonically: `b / (1 + b)` with `b = max(0, −rank)` |
| 7 | `path_match` | 0.8 | Fraction of query terms found in **parent directories** (terms of length ≥ 2 only) |
| 8 | `doc_type` | 0.5 | Extractable content = 1.0, metadata-only (binary) = 0.4 |
| 9 | `source` | 0.4 | Provider/context weight (`local`/`onedrive`/`other` = 1.0 today) |
| 10 | `recency` | 0.3 | `0.5 + 0.5·e^(−age_days/180)` — bounded to [0.5, 1.0] |
| 11 | `usage` | 0.0 | **Reserved** for future local usage signals; never collected |

## Why path weight is low

Path-only matches are common and accidental (a drive letter, a course
folder). Their weight (0.8) is far below filename and content signals, and
single-character terms are excluded entirely, so a document whose content is
actually relevant always outranks a document that merely lives under a
matching folder.

## Why recency is bounded

Recency contributes at most `0.3 × 1.0 = 0.3` and at least `0.3 × 0.5 = 0.15`
— a difference of `0.15 / 14 ≈ 0.011`. An old document with genuinely better
matches is never displaced by a newer weak one.

## Extension point

`Ranker.score(..., usage_boost=…)` accepts a usage signal in `[0, 1]`, but its
weight is `0.0`: no usage data is collected, stored or learned in phases
001–006. Future personalization must go through this hook, one explicit
setting at a time.

## Retrieval

Candidates come from FTS5 (`MATCH`, ordered by `bm25, path`) with a pool of
`max(limit × 5, 50)` rows; the composite score decides the final `limit`
results. Queries with FTS operators (`C++`, `foo:bar`, unbalanced quotes)
fall back to sanitized plain terms before retrieval; scoring always uses
`query_terms()`, which is operator-independent.
