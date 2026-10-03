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
| 11 | `usage` | 0.0 | **Optional** local usage learning; 0.0 until the user enables it (then 0.25, phase 044) |
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

## Measured headroom (re-measured in phase 045)

Precision@K is not saturated on this corpus — the default weighting puts a
relevant document first for 26 of 30 labelled queries (P@1 = 0.867, MRR =
0.867). Metrics alone still cannot tell a safe weight change from a harmful one,
so the instrument also answers a sharper question: **is the signal load-bearing
(does zeroing it change a ranking?), and how far can it move before it does?**

**These rows are measurements, and the corpus they were taken on changed in
phase 045** (27 documents / 18 queries → 39 / 30). Three rows moved, and two of
them moved for a reason worth reading: `phrase_exact` and `proximity` are no
longer load-bearing for `"ebers moll"` because the corpus gained
`codigo/electronica/ebers_moll.py`, an implementation file whose name matches
the phrase better than the scattered document the row used to detect.

| Weight | Query | Load-bearing? | Boundary | Headroom |
|---|---|---|---|---|
| `recency` | `diagrama` | yes — at 0 the tie falls back to alphabetical order | none ≤ 8.0 | ≥ 26.7× |
| `path_match` | `BJT` | no | 5.75 | 7.2× |
| `term_freq` | `BJT` | no | none ≤ 8.0 (was 6.79) | ≥ 20.0× |
| `bm25` | `BJT` | yes — at 0 the long log rises to #3 | none ≤ 8.0 | ≥ 4.0× |
| `phrase_exact` | `"ebers moll"` | **no** (was yes) | none ≤ 8.0 | ≥ 4.0× |
| `proximity` | `"ebers moll"` | **no** (was yes) | none ≤ 8.0 | ≥ 5.3× |
| `filename_tokens` | `informe` | yes — at 0 a content-only document takes #1 | none ≤ 8.0 | ≥ 4.0× |
| `filename_exact` | `informe` | yes | **3.66** | **1.22×** |
| `doc_type` | `BJT` | no | none ≤ 8.0 | ≥ 16.0× |

Three readings worth keeping in mind:

- **No weight is decorative**, and no *row* is permanent. Four of the nine
  signals change a measured ranking when switched off; `path_match`,
  `term_freq` and `doc_type` are guard rails that never decide an ordering on
  their own, which is exactly what they are for.
- **`filename_exact` is the tightest number in the system** (+22 % swaps the
  two exact-name documents). It is also a deliberate design choice — a file
  called exactly what you searched for should come first — so it is the one to
  leave alone. Phase 044 added a mechanical guard on top: learning never boosts
  a document whose name is the query.
- **A headroom table is a measurement with a corpus hash attached, not a
  property of the code.** Two rows changed without a line of ranking code
  changing. `evaluation/quality_gate` Q10 exists to catch the *weights*
  drifting; nothing catches the *table* drifting, so it was re-measured by hand
  and the previous values are marked rather than quietly replaced.

Reproduce any row with:

```bash
python -m evaluation --flip path_match BJT
```

## When the ranking must not change

Phase 045 added `evaluation/diagnose.py` and `evaluation/quality_gate.py`,
which answer the question this section is about from the other direction: given
the current corpus, **is there any reproducible failure a weight could reach?**

The answer today is **no**. All eight measured failures are lexical —
synonym, paraphrase, morphology, CJK tokenisation — and **not one of the missing
documents enters the candidate pool at all**, even at `limit=500`. Ranking only
reorders what retrieval returned, so no weighting change can reach them; six of
the eight are already recovered by the semantic fallback layer that shipped in
phase 026. Nine candidate weights were measured end to end and none moved MRR.

That is why `docs/RANKING.md` did not gain a single new number this phase. The
instruments got sharper and the answer got firmer, and the file is unchanged.

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

## Local learning (phase 044)

Learning is the one signal that is not derived from the query, so the whole
section exists to keep it secondary. Its value lives in
`universal_search/learn.py`, as pure functions over numbers.

**Scope.** An event counts for the query it was recorded under (80 %) and for
the document in general (20 %). Four opens under a *different* query move
nothing at all — four fifths of four is below the floor.

**Floor.** Below `MIN_EFFECTIVE_EVENTS = 2.0` effective events the signal is
exactly zero. One accidental click must not reorder anybody's results, and
that is what makes cold start deterministic rather than merely quiet.

**Decay.** Four buckets, a step rather than a curve because a step can be read
on a screen: full weight to 30 days, then 0.6, 0.3, and a floor of 0.1. Four
opens inside a month are the whole signal; the same four spread over a year and
a half are worth nothing, because 4 × 0.3 = 1.2 is below the floor.

**The weight, measured.** The ceiling on any signal in a weighted mean is
`weight / (total + weight)`. At 0.5 the usage signal was worth 3.45 % of the
final score against `recency`'s 2.14 % — *stronger* than the signal the project
already calls secondary. At **0.25** it is 1.75 %, i.e. 0.82× `recency`. The
23 consecutive score gaps in the evaluation corpus are bimodal: a cluster of
near-ties below 0.024 and a bulk of real margins above 0.044. Halving the
weight costs two reorderable pairs out of 23 and both are near-ties, which is
the only place learning was ever going to act.

**Dominance, not argument.** The weight ordering cannot promise that exact
matches stay dominant — a quarter of `filename_exact` is nothing when a rival's
content signals are stronger and the two sit within one usage step of each
other. So the ranker refuses the boost outright:

```python
if signals["filename_exact"] >= 1.0:
    signals["usage"] = 0.0
```

A document whose file name *is* what you typed has already been answered. The
rule lives in the ranker, not in the SQL, so a caller that computes a boost by
hand gets the same answer.

**Inspectable.** `universal-search usage effect` reports, per (document,
query), how many opens survive the decay, what they are worth now, and how many
signals no longer move anything while their row is still on disk.
`usage clear` and `privacy forget` delete them; `Indexer._delete()` deletes them
too, so a document that leaves the disk does not leave its query text behind.

Evidence: `python -m evaluation.learning_gate` — 11 invariants, including that
learning promotes 6 of 33 (query, candidate) pairs and demotes none, and that
with no history MRR is unchanged and not one of the 18 corpus queries moves by
so much as a rounding step.

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
