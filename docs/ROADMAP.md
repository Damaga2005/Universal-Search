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
- [ ] Global hotkey
- [x] Windows Start/Search integration
- [ ] Tray indexer

## v0.5
- [ ] OneDrive synced-folder provider
- [ ] Cloud-only OneDrive provider

## v0.6+
- [ ] University workspace/profile
- [ ] Local usage-based ranking
- [ ] Related-document graph
- [ ] Installable Windows application

---

Phases 001–006 are implemented and documented (per-phase reports in
`docs/development/`), with 129 passing tests. Global hotkey, tray indexer,
OneDrive providers, personal context/usage learning and the installer
remain for later phases.
