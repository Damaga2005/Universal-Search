# 026 — Evidence-first local semantic search: development report

## What shipped

A measured, optional, dependency-free local semantic layer — and the
instrument that justifies it. The phase did **not** assume embeddings were
needed: it first measured the lexical engine on a fixed corpus extended
with the failure classes a semantic layer would have to fix, then applied
an evidence gate, then shipped the only design that cleared it.

### The measurement instrument (`evaluation/`)

The corpus (`evaluation/corpus.py`) grew from 20 to **27 documents** and
from 13 to **18 labelled queries**, adding the failure classes:

* `transistor-bipolar` — a BJT document that never says "BJT" (synonym).
* `tension-base-emisor` — says "tension" where the query says "voltaje"
  (synonym pair).
* `polarizacion-acentuada` — accented variant of "polarizacion".
* `calculo-punto-operacion` — the paraphrase target for "como se determina
  el punto de trabajo".
* `malformed` — a `.md` whose body is binary garbage (name-only match).
* `futbol`, `viajes` — unrelated-domain distractors.

The runner (`evaluation/runner.py`) gained a deterministic **corpus hash**
(SHA-256 over every file's path, bytes, mtime and size), per-query **latency**,
**exact-match correctness** (for every query whose text appears verbatim in
a document, is an exact document ranked first?) and a **failure inventory**.
`python -m evaluation --semantic-baseline evaluation/semantic_baseline.json`
writes the full record.

### The semantic layer (`src/universal_search/semantic/`)

* `ngram.py` — `SemanticProvider`, a versioned character 3-gram TF-IDF
  embedder. The only local semantic technique that needs no runtime
  dependency, no network and no model download.
* `index.py` — `SemanticIndex`, versioned/rebuildable/removable vector
  storage (schema v9): an inverted n-gram index plus per-document norms and
  content words. The indexer marks it dirty after a pass; it rebuilds
  lazily on the next fallback search.
* `engine.py` — `HybridSearchEngine`, a fallback-only wrapper. The lexical
  engine always runs first and its results are returned unchanged; the
  semantic layer is consulted **only** when lexical returns nothing.

## The measured lexical baseline

Corpus hash `03acc29316628acd3c3c9c7a6d92e541fbc3942093da007e43c6c75e89476ad6`,
27 documents, 18 queries, k = 1/3/5, limit 10:

| metric | lexical |
|---|---|
| MRR | 0.833 |
| P@1 / P@3 / P@5 | 0.833 / 0.593 / 0.433 |
| R@1 / R@3 / R@5 | 0.503 / 0.760 / 0.817 |
| exact-match correctness | 1.000 (13 exact queries) |
| mean latency | ~8 ms (informational) |

The failure inventory — the concrete synonym/paraphrase failures:

| query | class | retrieved / relevant |
|---|---|---|
| `voltaje base emisor` | synonym | 0 / 2 (total) |
| `como se determina el punto de trabajo` | paraphrase | 0 / 3 (total) |
| `receta paella` | morphological | 0 / 1 (total) |
| `BJT` | synonym | 6 / 7 (partial — `transistor-bipolar` unreachable) |

## The evidence gate

Thresholds fixed **before** measuring (a priori, so they cannot be moved to
fit the result). A local semantic layer is shipped only if ALL hold:

* **T1** — hybrid mean R@5 on the failure subset ≥ 0.50.
* **T2** — exact-match correctness stays 1.0.
* **T3** — no pre-existing query's top-1 moves.
* **T4** — no new runtime dependency, no network.

A bounded semantic *boost* on a non-empty lexical pool was prototyped and
**rejected**: it flipped the exact-token query `CMOS` (violating T3) and
produced no gain on the failure subset. The **fallback-only** design
cleared every threshold:

| gate | lexical | hybrid | result |
|---|---|---|---|
| T1 failure R@5 | 0.179 | **0.762** | PASS |
| T2 exact-match | 1.000 | 1.000 | PASS |
| T3 top-1 regressions | — | 0 | PASS |
| T4 deps / network | — | none | PASS |

**Decision: SHIP** the fallback-only local semantic layer. Hybrid
aggregates: MRR 0.833 → **0.944**, R@5 0.817 → **0.947**.

### The precision gate

A fixed cosine threshold (0.12) was not robust: in a small corpus the
incidental sub-word overlap of a nonsense query (`nad`/`ada` from `nada`)
scored 0.26, clearing the threshold. The layer therefore also requires the
query to share at least one **content word** (token ≥ 3 chars) with the
document. Real semantic matches share content words; nonsense queries share
only incidental fragments. This is what makes the "must retrieve nothing"
contract survive (`zzz no existe` and `noexistenadaquienadie` both stay
empty).

## TDD log

- RED: `.venv\Scripts\python.exe -m pytest tests\test_evaluation.py -q -o addopts=`
  → 2 failures: the extended corpus dropped MRR below 1.0 (the labelled
  failures) and moved the `notas` top-3 (a new distractor). Re-baselined
  `evaluation/baseline.json` with the corpus hash recorded.
- GREEN: same command → **32 passed** after adding the phase-026 corpus,
  runner instrumentation and tests.
- RED: `.venv\Scripts\python.exe -m pytest tests\test_semantic_search.py -q -o addopts=`
  → collection error (no `semantic` package), then failures for the
  fallback contract, exact-match authority and removal.
- GREEN: same command → **19 passed** after implementing the provider,
  index and hybrid engine.
- Regression: `.venv\Scripts\python.exe -m pytest -q -o addopts=` →
  **1 failure** (`test_no_results_state_explains_itself`): the semantic
  fallback returned a fuzzy match for the nonsense query `noexistenadaquienadie`
  in the GUI's 2-document corpus. Fixed with the shared-content-word
  precision gate (test-first: the gate was added to reject the nonsense
  query, then the real matches were re-verified).
- Post-review RED: `test_hybrid_fallback_does_not_bypass_filters` failed because
  the fallback ignored `source`/`type` filters; `test_forget_removes_semantic_vectors_and_inventory_declares_them`
  failed because semantic rows survived `privacy forget` and were absent from
  the inventory. Both were fixed test-first: filtered searches now disable the
  fallback, and forgetting a document deletes its vectors/terms transactionally.
- Full suite: `.venv\Scripts\python.exe -m pytest -q -o addopts=` →
  **852 passed, 3 skipped**, clean pyflakes on every touched file.

## Compatibility and limitations

* **Exact matches, phrases, filters and operators are authoritative.** The
  semantic layer only ever adds results to an empty lexical answer; it never
  reorders a non-empty one. Exact-match correctness is 1.0 with the layer on.
* **Filters disable the fallback.** `source` and `type` are parsed,
  authoritative filters; the semantic layer cannot reproduce their plan, so
  a filtered query never enters fallback and cannot resurrect excluded rows.
* **Vectors are private derived data.** `privacy forget` deletes the document's
  semantic rows in the same transaction, and the privacy inventory declares
  the three semantic tables.
* **Optional and removable.** With no provider configured, or with the
  derived tables deleted, the hybrid engine is exactly the lexical engine.
  `cli.py search --no-semantic` disables the fallback explicitly.
* **Pure synonyms remain out of reach.** `BJT` vs `transistor de union
  bipolar` shares no surface form, so the layer cannot close that gap. A
  learned embedding model would, but it needs a runtime dependency, a model
  download (network) and a license — the program boundaries forbid these
  unless a measured requirement justifies them, and phase 026 measured that
  they do not. This is the honest boundary of a dependency-free layer.
* **The paraphrase query is only partially recovered** (1 of 3 relevant):
  the relevant documents share only function words with the query, so their
  n-gram cosine is low. The layer recovers the one that shares content
  words; the other two stay below the threshold.
* **Accents are a retrieval non-issue, a mild ranking signal.** FTS5's
  unicode61 tokenizer folds diacritics, so `polarizacion` already retrieves
  `polarización`; the Python ranker scores it slightly lower. No change made.
* **No cloud, network, telemetry, external API or runtime dependency.** The
  layer is pure Python + SQLite (already a dependency). The embedder is a
  standard algorithm (char n-gram TF-IDF), so there is no model license to
  document; the code is versioned and rebuildable.
* **Resource cost.** Indexing: one extra pass per document (n-gram
  computation) during the lazy rebuild. Disk: one inverted index row per
  kept n-gram (≤ 200 per document) plus a per-document norm and word list.
  Query: the fallback runs only when lexical returns nothing, and only
  scans documents sharing n-grams with the query.

## Files

* `evaluation/corpus.py` — extended corpus (27 docs, 18 queries, failure classes).
* `evaluation/runner.py` — corpus hash, latency, exact-match correctness,
  failure inventory, `--semantic-baseline`.
* `evaluation/baseline.json` — re-recorded regression fixture (new corpus).
* `evaluation/semantic_baseline.json` — the measured baseline + decision.
* `src/universal_search/semantic/` — provider, index, hybrid engine.
* `src/universal_search/index/database.py` — schema v9 (derived semantic tables).
* `src/universal_search/index/indexer.py` — marks the semantic index dirty.
* `src/universal_search/cli.py` — `search --no-semantic`, hybrid wiring.
* `src/universal_search/gui/services.py` — hybrid wiring.
* `tests/test_semantic_search.py` — 20 tests for the layer, filters and the gate.
