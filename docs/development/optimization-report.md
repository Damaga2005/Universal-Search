# Audit & optimization pass (2026-09)

Not a numbered phase — this is the record of the standing order after 007–010:
audit everything, fix all errors, make the search engine ultra-fast, sync docs.

## Audit (commit `620d11b`)

* `pyflakes src tests` → exit 0 (venv-only audit tool, not a project dependency).
* Dead conditional removed in `indexer.py` (`root_text = str(root_text) if False
  else str(root_path)` → `str(root_path)`).
* `gui/services.py`: declared `__all__` covering the public API, including the
  hotkey re-exports used by `app.py`.
* Unused imports removed: `ranking.py` (dead `SearchDatabase` import),
  `cli.py` (`dataclasses.replace`), `indexer.py` (`SourceKind`), plus unused
  imports in 8 test files.
* Strict `-W error::DeprecationWarning` suite green; `compileall -W error`
  clean; no TODO/FIXME/breakpoint markers left in the tree.

## Profiling method

Three harnesses (scratch, not committed): a bench (2000-doc build + 40-query
latency), an anatomy profiler (connection / SQL / scoring / scan buckets), and
an interleaved SQL decomposition (11 rounds, medians) to defeat ±4 ms machine
noise. Initial hypotheses were disproven by data:

| Hypothesis | Measurement | Verdict |
|---|---|---|
| DDL (SCHEMA) runs per query | ~0.1 ms per connect | not a factor (gate kept anyway) |
| fsync dominates commits | commit p50 = 0.20 ms with `synchronous=NORMAL` | not the villain |
| FTS stale-delete full scan kills the build | 1.7 ms/doc, flat growth curve | not the build killer; kept for correctness |
| per-document connection churn | open 4.4 + commit 0.6 + **last-close WAL checkpoint 8–16 ms** ≈ 19 ms/doc | **the actual build cost** |
| `documents` join for every FTS match | 16.69 ms → 10.53 ms when pushed behind the pool LIMIT | biggest query-side win |
| two-phase SQL (meta + content-by-rowid + Python snippet) | FULL 16.07 / META 18.10 / META+snip 21.23 — inconsistent across runs | **skipped**: within noise, and would lose FTS `[highlight]` brackets |
| `snippet()` by rowid without `MATCH` | works but drops the brackets | rejected |

## Changes

1. **`database.py`** — persistent connection pragmas (`journal_mode=WAL`,
   `foreign_keys=ON`, `synchronous=NORMAL`, `cache_size=-16000`,
   `mmap_size=256 MiB`, `temp_store=MEMORY`) + schema-present gate that skips
   `executescript(SCHEMA)` when all four objects exist; `_migrate()` still
   runs on every connect.
2. **`indexer.py`** — one **reused connection per `Indexer`**
   (`connection()` / `close()`); `upsert()` no longer opens + WAL-checkpoints
   a fresh connection per document. `index_root()` commits in batches of
   `COMMIT_EVERY = 200` instead of per file. Errors roll back to the last
   batch (same semantics as the old `closing()` wrapper).
3. **`ranking.py`** — `content_words()` cache (SHA-256 key, cap 1024);
   phrase + term counts + positions computed in **one fused pass** over the
   content tokens (was three scans); `score()` sums signals inline instead of
   building the explainability dict; `unique_set` hoisted out of the loop;
   phrase check by tuple adjacency (no O(content) string joins); `lru_cache`
   on ISO date parsing, name/stem tokenization and path-component tokens —
   the same candidates are re-scored on every keystroke.
4. **`search.py`** — `RESULTS_SQL` pushed down: the candidate pool
   (`MATCH` + `bm25` + `LIMIT`) is computed first and `documents` is joined
   only over the pool (16.69 → 10.53 ms interleaved median). Source/type
   filters join `documents` **inside** the pool subquery, preserving both the
   placeholder order (`match, filters, limit`) and the semantics that a
   filtered search ranks the whole filtered set before the limit.

## Results (bench, 2000 documents, same script before/after)

| Metric | Before | After | Δ |
|---|---:|---:|---:|
| build 2000 docs | 45.26 s | **2.78 s** | **16.3×** |
| mean query | 48.6 ms | **34.4 ms** | −29 % |
| p95 query | 80.3 ms | **46.6 ms** | −42 % |
| SQL (content + snippet, pool 250) | 19.7 ms | **10.6 ms** | −46 % |
| `ranker.score` × 250 | 34.4 ms | **12.3 ms** | −64 % |
| per-document upsert | 18.8 ms | **1.45 ms** | 12.9× |
| `index_root` warm rescan (300 files) | 773 ms | **45 ms** | 17× |
| first query after a fresh build | 36.5 ms | 73 ms | one-time, cold page cache; still below the old mean |

## Verification

* `pyflakes src tests` → 0.
* `pytest tests/ -q` → **208 passed** (also under `-W error::DeprecationWarning`).
* Latency tripwires tightened in `tests/test_performance.py`
  (mean < 100 ms, worst < 300 ms — ~3× the measured values).
* No new dependencies; no AI / external APIs / cloud / Elasticsearch /
  Redis / Docker anywhere in the stack.
