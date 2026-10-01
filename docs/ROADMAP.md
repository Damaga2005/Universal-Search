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

## v2.x (phases 031–040)

- [x] Robust search: typos and partial words (031)
- [x] Query suggestions (032)
- [x] Email as a source (033)
- [x] Content inside archives (034)
- [x] GUI batch operations (035)
- [x] Grouping, sorting and saved searches (036)
- [x] Distribution: portable mode and single executable (037)
- [ ] Reproducible performance gate (038)
- [ ] Accessibility and interface (039)
- [ ] Universal Search v3 quality gate (040)

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

---

Phases 001–037 are implemented and documented (per-phase reports in
`docs/development/`). The v2 quality gate is an executable instrument, not a
promise: `python -m evaluation.gate` runs thirteen local invariants —
dependency budget, no network or model imports, a platform-independent data
path, declared Windows touchpoints, a complete privacy inventory, repairs that
provably never touch a user's files, single-sourced versioning, and
documentation that matches the code. The phase-030 gate measured **916
passing tests, 3 skipped** over 919 collected, clean pyflakes, a green packaged
smoke over both real
executables, and a re-measured semantic layer (hybrid failure R@5 0.762,
exact-match correctness 1.0, no top-1 regression, nonsense queries still
empty) after the packaged smoke exposed and fixed a zero-idf degeneracy and an
exact-token precision gate.

Current test count: 1131 tests collected.
