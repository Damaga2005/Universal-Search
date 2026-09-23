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
- [ ] Tray indexer

## v0.5
- [x] OneDrive synced-folder provider
- [x] Cloud-only OneDrive provider

## v0.6+
- [x] University workspace/profile
- [x] Local usage-based ranking
- [ ] Related-document graph
- [x] Installable Windows application

## v0.7+ (phases 011–020)

- [x] Performance & scalability (011)
- [x] Advanced search language (012)
- [x] Ranking v2: evaluation & explainability (013)
- [x] Local document intelligence (014)
- [ ] Index diagnostics & maintenance (015)
- [ ] Windows integration (016)
- [ ] UX & accessibility (017)
- [ ] Privacy & security hardening (018)
- [ ] Provider & extension architecture (019)
- [ ] Production release & reliability (020)

---

Phases 001–010 are implemented and documented (per-phase reports in
`docs/development/`), with 208 passing tests, plus a profiled audit &
optimization pass (`docs/development/optimization-report.md`): indexing
16× faster (2000 docs 45.3s → 2.8s), mean query 48.6 → 34.4 ms.
Still open: the tray indexer and the related-document graph.
