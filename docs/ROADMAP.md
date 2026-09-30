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

## v0.8+ (phases 021–030)

- [x] Tray & background experience (021)
- [x] Related-document graph (022)
- [x] Indexing UX & control center (023)
- [x] Provider expansion (024)
- [x] Content extraction v2 (025)
- [x] Local semantic search without cloud AI (026)
- [x] Windows shell integration (027)
- [ ] Observability & recovery (028)
- [ ] Release engineering & CI (029)
- [ ] Universal Search v2 quality gate (030)

---

Phases 001–026 are implemented and documented (per-phase reports in
`docs/development/`). The phase-027 quality gate measured **859 passing tests,
3 skipped**, clean pyflakes, a measured lexical baseline (MRR 0.833, exact-match
correctness 1.0) and an optional, dependency-free, fallback-only local
semantic layer that cleared a priori evidence gates (hybrid MRR 0.944). Still
open: the planned phases from 027 through 030.
