# Phase 008 — Personal Context and University Search: Implementation Report

## What was implemented

- **`context.py`** (new module) — the personal-context layer, all of it local
  and deterministic:
  - **Context model**: `Context(name, roots, subjects, preferred_sources,
    doc_types, related_terms, recency_days)` parsed/validated from plain
    `config.json` entries (`context_from_dict` normalizes case, drops
    unusable entries, never raises).
  - **Configuration helpers**: `with_context` (add/extend with merge +
    dedupe, and it also ensures the context roots are *indexed* by adding
    them to `config.roots`), `with_context_removed` (clears the active
    context when it is the removed one), `with_active_context` (validates,
    `ValueError` on unknown names), `configured_roots` (explicit roots +
    every context root, deduplicated — used by the background worker).
  - **Related terms** (`expansion_terms`): synonyms only widen the FTS
    retrieval pool — the scoring pass always receives the original query
    terms, so exact matches keep every signal and are never hidden behind a
    synonym.
  - **Explainable boost** (`context_boost_for`): additive, bounded bonuses —
    root 0.5 + subject 0.25 + doc type 0.15 + preferred source 0.10 +
    recency 0.10, **capped at 1.0** — returned together with human-readable
    reasons (`root:…`, `subject:…`, `type:…`, `source:…`, `recency:…`).
  - **Usage signal** (`usage_boost_from`): saturating log curve, 0 opens →
    0, 4+ opens → 1.
- **Ranking** (`index/ranking.py`): two new bounded signals — `context`
  (weight 0.0 → `ACTIVATED_CONTEXT_WEIGHT = 1.0` only when a context is
  applied) and `usage` (0.0 → `ACTIVATED_USAGE_WEIGHT = 0.5` only when
  learning is enabled **and** events exist). The denominator stays dynamic,
  so scores remain normalized to [0, 1]. New `Ranker.contributions()`
  returns the per-signal weighted breakdown (`score` is derived from it),
  which is what makes personalization inspectable.
- **Database**: new local-only `usage_events` table (document id, query
  text, timestamp) created idempotently by the existing schema — old
  databases pick it up on first connection without any destructive step.
- **Search engine** (`index/search.py`): `search(query, limit, *,
  context=None, usage=False, explain=False)`:
  - context → expanded retrieval + per-candidate bounded boost with notes;
  - usage → counts joined from `usage_events` for the candidate ids,
    converted to the bounded signal (weight activated only when counts
    exist);
  - explain → full per-signal breakdown attached to each `SearchResult`
    (`explain` + `explain_notes`);
  - usage persistence API: `record_open` (query/result association),
    `usage_rows` (inspectable), `clear_usage` (deletable).
- **Service layer**: `SearchService.search(..., context=..., explain=...)`
  resolves the active/named context from configuration and gates usage on
  `usage_tracking`; `SearchService.record_open` is a privacy gate — no
  config flag, no record.
- **GUI**: *Contexto* combo box in the toolbar (`(todos)` + context names),
  persisted as `active_context`, re-runs the current search on change;
  opening a result records the usage signal (only when enabled).
- **Background worker**: watches and reconciles **explicit roots + context
  roots** (`configured_roots`), so a University folder added only through
  `context add` is indexed without extra steps.
- **CLI**:
  - `context list | add NAME [--root … --subject … --type … --source …
    --recency-days N] | remove NAME | use NAME|none | relate NAME term
    synonym…`;
  - `usage on | off | show [--database] | clear [--database]`;
  - `search --context NAME --explain` (also honors the active context,
    unknown `--context` exits 1 with a clear message);
  - `search --explain` prints `score=… filename_exact=… context=… ·
    root:…; type:…; recency:…`.

## Technical decisions

1. **Expansion never touches scoring** — structurally guarantees the
   acceptance criterion "related terms improve recall without hiding exact
   matches"; tests prove both halves.
2. **Boosts are bounded signals + activated weights**, not score
   multipliers: max personalization contribution is 1.0/15 ≈ 6.7 % (context)
   and 0.5/14.5 ≈ 3.4 % (usage), versus 3.0 for an exact filename match —
   personalization provably cannot overwhelm exact matches (tested).
3. **Explainability is first-class**: every score decomposes into named
   weighted contributions plus contextual reasons; available in the CLI
   (`--explain`) and in `SearchResult.explain`.
4. **Usage is opt-in, local, inspectable, deletable** (spec privacy rules):
   default off, stored only in the app-home database, `usage show` /
   `usage clear`, no network code anywhere in the application.
5. **Contexts are plain JSON config** — human-editable, no new storage
   format, no new dependency.
6. **Context roots become indexed roots** (`with_context` +
   `configured_roots`) — a preference over a folder nobody indexes would be
   useless.

## Dependencies

None added (zero new packages for this phase).

## Limitations

- "Result selected" as a usage signal is not recorded (only *opened* +
  query association); the spec lists signals as optional ("may include").
- Subject matching is a case-insensitive substring check over the document
  path — folders named after the subject are the intended usage; it does
  not classify by content (that would be opaque, not explainable).
- The GUI context combo refreshes its list when a search runs; a context
  created elsewhere appears on the next search/window start.
- Recent-queries UI and global shortcut arrive with phase 009 (spec 009).

## Tests

`tests/test_context.py` (**20 tests**) + 3 CLI tests in
`tests/test_cli.py` + 2 GUI tests in `tests/test_gui.py`:

- parsing normalization + garbage rejection; config round-trip; invalid
  entries dropped on load; merge/dedupe; removal clears the active context;
  activation validation (`ValueError`); `configured_roots` union;
- expansions (add related only, no duplicates/originals, `None` context);
- boost reasons and the 1.0 cap; neutral outside a context; usage saturation;
- default weights keep the 14.0 exact-match baseline; contributions sum /
  score consistency;
- **recall**: synonym widens results while the exact match keeps rank 1;
- **context roots**: identical documents — the in-context one scores higher,
  by a provably bounded delta;
- **key acceptance**: exact filename match still wins with the context
  active, while the boosted document's score still rises;
- **explain**: complete breakdown, `context == 1.0 × (0.5 root + 0.25
  subject)`, root/subject notes present, `sum/total == score`, off by
  default;
- **privacy**: usage disabled by default → nothing recorded, storage inside
  the per-user app directory; enabled → recorded with the query, inspectable
  by name, `clear_usage` removes all; opened result ranks higher by a bounded
  delta, no influence when learning is off;
- **service**: named-context override resolves to the right `Context`,
  usage stays opt-in;
- **CLI**: full lifecycle (add → list → use → relate → none → remove, unknown
  → exit 1 + stderr), usage on/show/clear/off against a real index,
  `search --context/--explain` output, unknown context exit code;
- **GUI**: combo `(todos)`/contexts → persists `active_context` → re-runs
  the search; open records the signal only when enabled.

Full suite: **170 passed** (was 145 after phase 007).

## Acceptance criteria

- A user can define a University context — `context add`/`relate`/`use` +
  GUI combo + tests.
- University results can receive contextual ranking — root/subject/type/
  source/recency boost with tests.
- Related terms improve recall without hiding exact matches —
  `test_related_terms_improve_recall_without_hiding_exact`.
- Personalization is explainable — `--explain` breakdown + notes +
  `test_explain_breakdown_is_complete_and_consistent`.
- Tests verify personalization never overwhelms exact matches —
  `test_exact_filename_match_beats_context_preference` (+ bounded-delta
  assertions for both boost types).

## How to run / manual verification performed

```bash
universal-search context add Universidad --root D:\uni --subject Álgebra --type pdf --recency-days 60
universal-search context relate Universidad fourier transformada
universal-search context list
universal-search context use Universidad
universal-search search fourier --context Universidad --explain
universal-search usage on
universal-search usage show / usage clear
.venv\Scripts\python -m pytest tests/test_context.py -v
```

Real-session demo output: `context add` → `contexto «Universidad» guardado
(1 raíz(es))`; `relate` → `«fourier» ← transformada`; `list` →
`Universidad: 1 raíz(es), 1 materia(s), 1 tipo(s), 1 término(s)
relacionado(s)`; `search --explain` → the result with
`score=0.707 … context=0.750 … · root:…\uni; type:md; recency:60d`;
`usage on` → `uso local activado (solo en este equipo; nunca se sube)`.
