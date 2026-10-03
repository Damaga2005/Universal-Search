# Roadmap

## v0.1
- [x] Project skeleton
- [x] Local filesystem provider
- [x] SQLite + FTS5
- [x] Basic CLI
- [x] Initial test

## v0.2
- [x] Incremental indexing
- [x] Ignore rules
- [x] Deleted-file reconciliation
- [x] Background indexer

## v0.3
- [x] PDF / DOCX / XLSX extraction
- [x] Better snippets
- [x] Ranking engine

## v0.4
- [x] Windows desktop GUI
- [x] Global hotkey
- [x] Windows Start/Search integration
- [x] Tray indexer

## v0.5
- [x] OneDrive synced-folder provider
- [x] Cloud-only OneDrive provider

## v0.6+
- [x] University workspace/profile
- [x] Local usage-based ranking
- [x] Installable Windows application

## v0.7+ (phases 011–020)

- [x] Performance & scalability (011)
- [x] Advanced search language (012)
- [x] Ranking v2: evaluation & explainability (013)
- [x] Local document intelligence (014)
- [x] Index diagnostics & maintenance (015)
- [x] Windows integration (016)
- [x] UX & accessibility (017)
- [x] Privacy & security hardening (018)
- [x] Provider & extension architecture (019)
- [x] Production release & reliability (020)

## v2.x — completed (phases 031–040)

- [x] Robust search: typos and partial words (031)
- [x] Query suggestions (032)
- [x] Email as a source (033)
- [x] Content inside archives (034)
- [x] GUI batch operations (035)
- [x] Grouping, sorting and saved searches (036)
- [x] Distribution: portable mode and single executable (037)
- [x] Reproducible performance gate (038)
- [x] Accessibility and interface (039)
- [x] Universal Search v3 quality gate (040)

---

## v0.8 (phases 021–030)

- [x] Tray & background experience (021)
- [x] Related-document graph (022)
- [x] Indexing UX & control center (023)
- [x] Provider expansion (024)
- [x] Content extraction v2 (025)
- [x] Local semantic search without cloud AI (026)
- [x] Windows shell integration (027)
- [x] Observability & recovery (028)
- [x] Release engineering & CI (029)
- [x] Universal Search v2 quality gate (030)


## v3.x (phases 041–050)

- [x] Product experience & UX (041)
- [x] Interactive Search Experience (042)
- [x] Settings & Configuration (043)
- [x] Local learning v2 (044)
- [ ] Search Quality & Relevance (045)
- [ ] Indexing Scalability & Performance (046)
- [ ] Storage & data lifecycle (047)
- [ ] Windows & Environment Matrix (048)
- [ ] Windows Distribution & Installation (049)
- [ ] Universal Search 3.x Product Gate (050)

---

Phases 001–040 are implemented and documented; phases 041–050 are planned (per-phase documentation in
`docs/development/`). The quality gate is an executable instrument, not a
promise: `python -m evaluation.gate` runs **23 local invariants** — the thirteen
from phase 030 (dependency budget, no network or model imports, a
platform-independent data path, declared Windows touchpoints, a complete
privacy inventory, repairs that provably never touch a user's files,
single-sourced versioning, documentation that matches the code) plus ten added
by phase 040, one per promise the 031–040 programme made: that the optional
layers can be removed, that hostile archives are bounded, that batch actions
stay inside the results, that portable mode never writes under
`%LOCALAPPDATA%` and never falls back silently, that both builds pin one
version, that the performance gate can decline to conclude, that every visible
string is catalogued, that contrast meets WCAG AA, and that a saved search
holds no user data.

The v2 gate measured **916 passing tests, 3 skipped** over 919 collected,
clean pyflakes, a green packaged smoke over both real executables, and a
re-measured semantic layer (hybrid failure R@5 0.762, exact-match correctness
1.0, no top-1 regression, nonsense queries still empty) after the packaged smoke
exposed and fixed a zero-idf degeneracy and an exact-token precision gate. The
v3 gate re-measured all ten phases of the programme on Windows 11, Python
3.14.6: **every phase gate SHIP**, MRR unchanged at 0,833, pyflakes clean, and
the full suite at **1249 collected, 0 failed** on a machine at rest. The
programme's ten phases were pushed to `main` on explicit instruction; the
version tag, the artefact publication and the release notes are separate steps
that follow the release list. The version is still `2.0.0` — finishing a
programme of phases is not a reason to rename the software.

Phase 041 is the first of the 3.x programme and the first one that measured the
*experience* rather than the engine: `python -m evaluation.ux_gate` runs nine
invariants about the primary flow, and it found that the results pane had no
hierarchy at all — a `Listbox` flattening name, folder, type, source and snippet
into one clipped string — plus two defects the phase 039 audit had missed on one
of its two error paths: a rejected query drawn as an ordinary status line, and a
failed search leaving the previous query's answers on screen.

Its ninth invariant — filling a page of 50 results inside the interaction budget
— is **INCONCLUSIVE** and exits 2, because the phase 038 load veto found the
machine busy with something that is not this project. It is declared, not
assumed, and it closes on its own when the machine is idle.

Phase 042 integrated what the earlier phases had built and the window had never
called: sorting, grouping, saved searches, verified suggestions, explanations and
the fuzzy layer. Its first invariant exists because phase 041 drew a malformed
query as a failure that **the running application could never be shown** — the
service swallowed the error into a field nobody read, and phase 041's own gate
passed because its test stubbed the service. `python -m evaluation.interaction_gate`
runs 11 invariants about the seam between the mechanisms and the user; all 11
pass, including keystroke-to-result at 185 ms against a 600 ms budget.

Phase 043 turned configuration from a bag of fields into a contract. `python -m evaluation.settings_gate` runs 13 invariants over it, and none needs a window. The measurement that justified it: a negative `indexer_file_delay` in `config.json` reached `time.sleep(-1)`, which raises inside the indexing loop and fails the whole pass — reachable only by editing a JSON file, and now refused by the schema.

Phase 044 took the one ranking signal that is not derived from the query and
found that it had never learned anything: `USAGE_COUNTS_SQL` counted opens and
ignored the `query` column it had been writing since phase 008, so a document
opened for `examen` was also boosted for `receta paella`, and `opened_at` was in
the schema unread, so a 2024 event weighed the same as yesterday's. Events are
now scoped to the query they were recorded under and decay on a four-step
schedule, and `python -m evaluation.learning_gate` runs 11 invariants over a
synthetic history. All 11 pass. The weight was measured rather than inherited:
at 0.5 the signal was worth 3.45% of the score against `recency`'s 2.14%, i.e.
*stronger* than the signal the project already calls secondary, so it is now
0.25 and 1.75% — at a cost of two reorderable pairs out of 23, both of them
near-ties. Learning promotes 6 of 33 (query, candidate) pairs and demotes none;
with no history MRR is unchanged and not one of the 18 corpus queries moves.

Current test count: 1365 tests collected.
