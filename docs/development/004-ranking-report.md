# Phase 004 — Search Ranking: Implementation Report

## What was implemented

- **`index/ranking.py`** — a standalone, deterministic ranking layer:
  - `RankingWeights` — explicit weights (defaults sum to 14.0).
  - `Ranker.signals()` / `Ranker.score()` — the ten required signals plus the reserved usage hook.
  - `query_terms()` / `tokens()` — query and text tokenization aligned with FTS5's `unicode61` (underscores split).
  - `Candidate` — the storage-agnostic input of the ranker (name, path, content, source, modified_at, bm25).
- **Formula** (documented in `docs/RANKING.md` and the module docstring):

  ```
  score = Σ (weightᵢ × signalᵢ) / 14.0   →   score ∈ [0, 1]
  ```

  | signal | weight | | signal | weight |
  |---|---:|---|---|---:|
  | filename_exact | 3.0 | | bm25 | 2.0 |
  | filename_tokens | 2.0 | | path_match | 0.8 |
  | phrase_exact | 2.0 | | doc_type | 0.5 |
  | term_freq | 1.5 | | source | 0.4 |
  | proximity | 1.5 | | recency | 0.3 |
  | **usage (reserved)** | **0.0** | | | |

- **`SearchEngine` rework**: FTS5 retrieves a candidate pool (`max(limit×5, 50)`, `ORDER BY bm25, path`), the ranker scores every candidate, results sort by `(-score, path)` and the final `limit` is returned with a new `score` field.
- **Snippet fix**: FTS highlight snippets are kept; name/path-only matches no longer dump the whole content — `build_snippet()` produces a bounded ~160-char excerpt around the first term, or `None` when the content contains no matching term.
- **Query robustness preserved**: FTS operator errors (`C++`, `foo:bar`, unbalanced quotes) still fall back to `sanitize_query()`; scoring always uses operator-independent `query_terms()`.

## Technical decisions

1. **Exact filename weight is the strongest single signal (3.0)** — a file named like the query outranks weak content matches, per acceptance criteria.
2. **Path weight is deliberately low (0.8) and excludes terms shorter than 2 characters**, so drive letters and accidental folder names can never dominate relevant content.
3. **Recency is bounded to [0.5, 1.0]** with weight 0.3: its maximum influence on a score is ~0.0107 — secondary by construction (asserted by a test).
4. **Tokenization unified with FTS5**: `unicode61` treats `_` as a separator; `tokens()` does the same, fixing underscore-joined filenames (`BJT_Ebers_Moll.pdf`) and making phrase/freq/proximity signals consistent with retrieval.
5. **Determinism**: SQL secondary ordering by path, Python sort by `(-score, path)`, recency quantized to whole days. No randomness, no clock-dependent ordering within a day.
6. **Personalization does not exist yet**: `usage` weight is `0.0`; `usage_boost` is accepted but provably inert (test asserts score equality with and without it). University workspace context is likewise not implemented (belongs to a later phase).

## Dependencies

None added.

## Limitations

- Only documents matching the FTS query enter the ranking pool (50 by default); a theoretically perfect filename match outside the pool would not be re-ranked (not observed in practice: filename matches rank high in bm25 too).
- Content is fetched for pool candidates (bounded by pool size × the 2 M-char extraction cap).
- The `source` signal is constant today; workspace/context weighting is a reserved extension point.

## Tests

`tests/test_ranking.py` (23 tests):
**BJT acceptance scenario** (filename PDF > heavy-content PDF > relevant MD > path-only doc); path-only never beats content; exact filename beats weak content; filename tokens; phrase vs scattered terms; determinism across runs; path tie-breaking; bounded recency; `C++` content over drive letter; `foo:bar` fallback; quoted phrase; per-signal unit tests (exact, tokens fraction, bm25 normalization, doc_type, recency neutral/recent, reserved usage, normalization bounds); snippet tests (no content dump, highlight preserved, windowing, empty inputs).

## Acceptance criteria

- Deterministic results — `test_results_are_deterministic`.
- Exact filename outranks weak content — `test_exact_filename_match_outranks_weak_content_match`.
- Contextual strength beats isolated occurrence — `test_path_only_document_never_outranks_content` + freq/proximity signals.
- Ranking isolated from storage and presentation — `index/ranking.py` imports only stdlib + a `Candidate` dataclass.
- Every scoring signal tested — yes, per-signal unit tests.

## How to run

```bash
universal-search search "BJT"          # ranked results
.venv\Scripts\python -m pytest tests/test_ranking.py -v
```

Formula details: `docs/RANKING.md`.
