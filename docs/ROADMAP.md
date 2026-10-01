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


## v3.x — planned (phases 041–050)

- [ ] Product UX redesign (041)
- [ ] Interactive Search Experience (042)
- [ ] Settings & Configuration (043)
- [ ] Local learning v2 (044)
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
the full suite at **1210 passed, 6 skipped, 0 failed** over 1216 collected. The
programme's ten phases were pushed to `main` on explicit instruction; the
version tag, the artefact publication and the release notes are separate steps
that follow the release list. The version is still `2.0.0` — finishing a
programme of phases is not a reason to rename the software.

Current test count: 1216 tests collected.
