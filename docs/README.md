# Universal Search — Roadmap & Documentation

Índice de documentación con el estado real del roadmap, fase a fase.
El roadmap por versiones (v0.1–v0.6+) vive en [ROADMAP.md](ROADMAP.md).

## Estado por fase

| Fase | Entrega | Estado | Prompt | Informe |
|------|---------|--------|--------|---------|
| 001 | Fundación: SQLite + FTS5, proveedor local, CLI, tests | ✅ Completada | [001-foundation.md](development/001-foundation.md) | — |
| 002 | Indexación incremental, ignore rules, reconciliación de borrados | ✅ Completada | [002-incremental-index.md](development/002-incremental-index.md) | [informe](development/002-incremental-index-report.md) |
| 003 | Extractores de documentos (PDF / DOCX / XLSX / PPTX) | ✅ Completada | [003-document-extractors.md](development/003-document-extractors.md) | [informe](development/003-document-extractors-report.md) |
| 004 | Motor de ranking de búsqueda | ✅ Completada | [004-ranking.md](development/004-ranking.md) | [informe](development/004-ranking-report.md) |
| 005 | Aplicación de escritorio Windows (GUI) | ✅ Completada | [005-windows-desktop.md](development/005-windows-desktop.md) | [informe](development/005-windows-desktop-report.md) |
| 006 | Indexador en segundo plano | ✅ Completada | [006-background-indexer.md](development/006-background-indexer.md) | [informe](development/006-background-indexer-report.md) |
| 007 | Proveedor OneDrive (carpetas sincronizadas + solo-nube) | ✅ Completada | [007-onedrive.md](development/007-onedrive.md) | [informe](development/007-onedrive-report.md) |
| 008 | Contexto personal y búsqueda universitaria | ✅ Completada | [008-personal-context.md](development/008-personal-context.md) | [informe](development/008-personal-context-report.md) |
| 009 | Búsqueda global (hotkey, filtros, recientes) | ✅ Completada | [009-global-search.md](development/009-global-search.md) | [informe](development/009-global-search-report.md) |
| 010 | Release (v1.0.0, instalador) | ✅ Completada | [010-release.md](development/010-release.md) | [informe](development/010-release-report.md) |

Verificado en la fase 010: **208 tests en verde**
(`.venv\Scripts\python -m pytest`), E2E completo de indexador, CLI, GUI e
instalador. Posteriormente, pasada de auditoría + optimización con
perfilado: indexación ×16 (2000 docs 45,3s → 2,8s) y consulta media
48,6 → 34,4 ms —
[informe](development/optimization-report.md).

## Cómo ejecutar

```bash
universal-search gui                   # ventana de búsqueda
universal-search search "meeting notes"
universal-search indexer start         # indexador en 2.º plano (independiente de la GUI)
universal-search indexer status|pause|resume|stop|autostart on
```

Construir los ejecutables: ver «Build the Windows executables» en el
[README raíz](../README.md).

## Documentos clave

- [ROADMAP.md](ROADMAP.md) — alcance por versiones con checkboxes.
- [ARCHITECTURE.md](ARCHITECTURE.md) — capas (Provider → Extractor →
  Document → Indexer → FTS5 → Ranking → SearchEngine → UI) y modelo de
  procesos GUI/indexador.
- [RANKING.md](RANKING.md) — fórmula de scoring y pesos.
- `development/` — un prompt por fase y, para las completadas, su informe
  (qué se hizo, decisiones, dependencias, limitaciones, tests, criterios de
  aceptación y cómo ejecutarla), más el
  [informe de auditoría + optimización](development/optimization-report.md).

## Convención de fases

Cada fase se considera terminada solo cuando: todos los tests pasan (los
anteriores incluidos), hay cobertura de tests propia, se documenta en un
informe y se marca en `ROADMAP.md`. Las fases 001–010 siguen esa
convención y están entregadas; el alcance futuro (indexador en bandeja,
grafo de documentos relacionados) queda reflejado en `ROADMAP.md`.
