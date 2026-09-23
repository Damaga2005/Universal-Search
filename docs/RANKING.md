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
| 11 | `usage` | 0.0 | **Optional** local usage learning; 0.0 until the user enables it (then 0.5) |
| 12 | `context` | 0.0 | **Optional** personal-context boost; 0.0 unless a context is active (then 1.0) |

All of it lives in one immutable value: `RankingWeights` in
`src/universal_search/index/ranking.py`, frozen, with `DEFAULT_WEIGHTS` as
the single source of truth. `RankingWeights.total` is the denominator, and
a test pins it to the documented 14.0, so a weight can only change on
purpose.

## Why path weight is low

Path-only matches are common and accidental (a drive letter, a course
folder). Their weight (0.8) is far below filename and content signals, and
single-character terms are excluded entirely, so a document whose content is
actually relevant always outranks a document that merely lives under a
matching folder.

This is now **measured, not asserted**: on the labelled corpus of phase 013,
the path-only document `cursos/BJT/calculo_matrices.txt` cannot displace any
content match for the query `BJT` until `path_match` grows from 0.8 to
**5.75** (7.2×). See "Measured headroom" below.

## Why recency is bounded

Recency contributes at most `0.3 × 1.0 = 0.3` and at least `0.3 × 0.5 = 0.15`
— a difference of `0.15 / 14 ≈ 0.011`. An old document with genuinely better
matches is never displaced by a newer weak one.

## Document-length normalisation

FTS5's `bm25()` already normalises by document length, and that normalised
value is what the `bm25` signal consumes. The effect is visible: in the
evaluation corpus, `datos/sensores/sensor_bjt.log` (4 000 words, two
mentions of *bjt*) scores `bm25 = 0.40` against `1.01` for a focused
document, and needs a 4.5× increase of `term_freq` to overtake it. Bulk
never wins.

## Measured headroom (phase 013)

Precision@K saturate on a corpus this small — the default weighting already
puts a relevant document first for every labelled query (P@1 = 1.000,
MRR = 1.000). Metrics alone therefore cannot tell a safe weight change from
a harmful one, so the instrument also answers a sharper question: **is the
signal load-bearing (does zeroing it change a ranking?), and how far can it
move before it does?**

| Weight | Query | Load-bearing? | Boundary | Headroom |
|---|---|---|---|---|
| `recency` | `diagrama` | yes — at 0 the tie falls back to alphabetical order | none ≤ 8.0 | ≥ 26.7× |
| `path_match` | `BJT` | no | 5.75 | 7.2× |
| `term_freq` | `BJT` | no | 6.79 | 4.5× |
| `bm25` | `BJT` | yes — at 0 the long log rises to #3 | none ≤ 8.0 | ≥ 4.0× |
| `phrase_exact` | `"ebers moll"` | yes — at 0 the scattered document enters the top 3 | none ≤ 8.0 | ≥ 4.0× |
| `proximity` | `"ebers moll"` | yes — at 0 the scattered document enters the top 3 | none ≤ 8.0 | ≥ 5.3× |
| `filename_tokens` | `informe` | yes — at 0 a content-only document takes #1 | none ≤ 8.0 | ≥ 4.0× |
| `filename_exact` | `informe` | yes | **3.67** | **1.2×** |
| `doc_type` | `BJT` | no | none ≤ 8.0 | ≥ 16.0× |

Two readings worth keeping in mind:

- **No weight is decorative.** Eight of the ten textual signals change a
  measured ranking when switched off. `path_match` and `doc_type` are
  guard rails: they never decide an ordering on their own, which is exactly
  what they are for.
- **`filename_exact` is the tightest number in the system** (+22 % swaps the
  two exact-name documents). It is also a deliberate design choice — a file
  called exactly what you searched for should come first — so it is the one
  to leave alone.

Reproduce any row with:

```bash
python -m evaluation --flip path_match BJT
```

## Signals that are deliberately *not* added

- **Query structure** (AND/OR/parentheses/phrase) is not an additive
  signal: a phrase query already earns `phrase_exact` and `proximity`, and
  boolean structure changes the candidate pool retrieved by FTS5 before any
  scoring happens. Adding a third bonus for the same evidence would
  double-count it.
- **Filter matches** are not a signal either: `type:`, `source:`,
  `after:`, `before:` and `size:` are applied in SQL *before* ranking, so
  every candidate satisfies every requested filter. A signal computed from
  them would be the constant 1.0 for the whole result set and would change
  no ordering. The evaluation exercises them anyway
  (`bjt type:txt`, `type:pdf`) to prove the filter and the ranking compose.

## Extension points

`Ranker.score(..., usage_boost=…)` and `context_boost=…` accept optional
signals in `[0, 1]`, clamped, with weight `0.0` by default. Usage data is
only collected when the user turns usage learning on (phase 008), and a
personal context only applies when one is active. Textual relevance is
provably untouched by both: a test compares every textual signal with and
without the boosts and requires them to be identical.

## Retrieval

Candidates come from FTS5 (`MATCH`, ordered by `bm25, path`) with a pool of
`max(limit × 5, 50)` rows; the composite score decides the final `limit`
results. Queries are translated by the query language of phase 012, so
`MATCH` is built from quoted word runs and whitelisted columns only — raw
FTS5 operator syntax is never passed through.

## Evaluating a change

```bash
python -m evaluation                       # labelled corpus, P@K / R@K / MRR
python -m evaluation --json results.json   # raw numbers, incl. per-signal points
python -m evaluation --flip recency diagrama
```

`evaluation/baseline.json` pins the top-3 of every labelled query and the
aggregates. A change that moves a ranking fails
`test_evaluation.py::test_ranking_matches_the_committed_baseline` until the
baseline is regenerated *and* the move is justified by a measurement.
