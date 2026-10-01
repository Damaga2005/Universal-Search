# Universal Search — Roadmap & Documentation

Índice de documentación con el estado real del roadmap, fase a fase.
El roadmap por versiones (v0.1–v0.8+) vive en [ROADMAP.md](ROADMAP.md).

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
| 011 | Performance & escalabilidad: benchmarks, métricas locales, cachés acotadas | ✅ Completada | [011-performance-scalability.md](development/011-performance-scalability.md) | [informe](development/011-performance-scalability-report.md) |
| 012 | Advanced search: lenguaje de consultas (parser/AST) | ✅ Completada | [012-advanced-search.md](development/012-advanced-search.md) | [informe](development/012-advanced-search-report.md) |
| 013 | Ranking v2: evaluación y explicabilidad | ✅ Completada | [013-ranking-v2.md](development/013-ranking-v2.md) | [informe](development/013-ranking-v2-report.md) |
| 014 | Inteligencia documental local | ✅ Completada | [014-document-intelligence.md](development/014-document-intelligence.md) | [informe](development/014-document-intelligence-report.md) |
| 015 | Diagnóstico e índice mantenible | ✅ Completada | [015-index-diagnostics.md](development/015-index-diagnostics.md) | [informe](development/015-index-diagnostics-report.md) |
| 016 | Integración con Windows | ✅ Completada | [016-windows-integration.md](development/016-windows-integration.md) | [informe](development/016-windows-integration-report.md) |
| 017 | UX y accesibilidad | ✅ Completada | [017-ux-accessibility.md](development/017-ux-accessibility.md) | [informe](development/017-ux-accessibility-report.md) |
| 018 | Privacidad y seguridad | ✅ Completada | [018-privacy-security.md](development/018-privacy-security.md) | [informe](development/018-privacy-security-report.md) |
| 019 | Arquitectura de proveedores y extensión | ✅ Completada | [019-provider-plugin-architecture.md](development/019-provider-plugin-architecture.md) | [informe](development/019-provider-plugin-architecture-report.md) |
| 020 | Release de producción y fiabilidad | ✅ Completada | [020-production-release.md](development/020-production-release.md) | [informe](development/020-production-release-report.md) |
| 021 | Bandeja de notificación y experiencia en segundo plano | ✅ Completada | [021-tray-and-background-experience.md](development/021-tray-and-background-experience.md) | [informe](development/021-tray-and-background-experience-report.md) |
| 022 | Grafo local de documentos relacionados | ✅ Completada | [022-related-document-graph.md](development/022-related-document-graph.md) | [informe](development/022-related-document-graph-report.md) |
| 023 | UX de indexación y centro de control | ✅ Completada | [023-indexing-ux-and-control.md](development/023-indexing-ux-and-control.md) | [informe](development/023-indexing-ux-and-control-report.md) |
| 024 | Expansión de proveedores (NAS/extraíble, identidad `(source, path)`) | ✅ Completada | [024-provider-expansion.md](development/024-provider-expansion.md) | [informe](development/024-provider-expansion-report.md) |
| 025 | Extracción de contenido v2 (contrato versionado, límites, diagnósticos) | ✅ Completada | [025-content-extraction-v2.md](development/025-content-extraction-v2.md) | [informe](development/025-content-extraction-v2-report.md) |
| 026 | Búsqueda semántica local con evidencia primero (capa opcional sin dependencias) | ✅ Completada | [026-local-semantic-search.md](development/026-local-semantic-search.md) | [informe](development/026-local-semantic-search-report.md) |
| 027 | Integración con el shell de Windows | ✅ Completada | [027-windows-shell-integration.md](development/027-windows-shell-integration.md) | [informe](development/027-windows-shell-integration-report.md) |
| 028 | Observabilidad y recuperación local | ✅ Completada | [028-observability-and-recovery.md](development/028-observability-and-recovery.md) | [informe](development/028-observability-and-recovery-report.md) |
| 029 | Release engineering y CI (puertas verificadas) | ✅ Completada | [029-release-engineering-and-ci.md](development/029-release-engineering-and-ci.md) | [informe](development/029-release-engineering-and-ci-report.md) |
| 030 | Puerta de calidad v2 (gate ejecutable) | ✅ Completada | [030-universal-search-v2-quality-gate.md](development/030-universal-search-v2-quality-gate.md) | [informe](development/030-v2-quality-gate-report.md) |
| 031 | Búsqueda robusta: erratas y palabras parciales | ✅ Completada | [031-robust-search.md](development/031-robust-search.md) | [informe](development/031-robust-search-report.md) |
| 032 | Sugerencias de consulta verificadas | ✅ Completada | [032-query-suggestions.md](development/032-query-suggestions.md) | [informe](development/032-query-suggestions-report.md) |
| 033 | Correo como fuente (`.eml`, `.mbox`) | ✅ Completada | — | [informe](development/033-mail-source-report.md) |
| 034 | Contenido dentro de `.zip` | ✅ Completada | — | [informe](development/034-archive-content-report.md) |
| 035 | Operaciones por lotes sobre la selección | ✅ Completada | — | [informe](development/035-batch-operations-report.md) |
| 036 | Agrupar, ordenar y búsquedas guardadas | ✅ Completada | — | [informe](development/036-organize-report.md) |
| 037 | Distribución: modo portable y ejecutable único | ✅ Completada | — | [informe](development/037-distribution-report.md) |
| 038 | Puerta de rendimiento reproducible | ✅ Completada | — | [informe](development/038-performance-gate-report.md) |
| 039 | Accesibilidad e interfaz (teclado, nombres, contraste, catálogo) | ✅ Completada | — | [informe](development/039-accessibility-report.md) |
| 040 | Puerta de calidad v3 (23 invariantes) y decisión de publicación | ✅ Completada | — | [informe](development/040-v3-quality-gate-report.md) |
| 041 | Experiencia de producto y UX | ⬜ Planificada | — | [plan](development/041-product-ux-redesign.md) |
| 042 | Experiencia interactiva de búsqueda | ⬜ Planificada | — | [plan](development/042-search-experience-v2.md) |
| 043 | Configuración y ajustes | ⬜ Planificada | — | [plan](development/043-settings-and-personalization.md) |
| 044 | Aprendizaje local v2 | ⬜ Planificada | — | [plan](development/044-local-learning-v2.md) |
| 045 | Calidad y relevancia de búsqueda | ⬜ Planificada | — | [plan](development/045-search-quality-v3.md) |
| 046 | Escalabilidad y rendimiento de indexación | ⬜ Planificada | — | [plan](development/046-indexing-performance-v3.md) |
| 047 | Almacenamiento y ciclo de vida | ⬜ Planificada | — | [plan](development/047-storage-and-data-lifecycle.md) |
| 048 | Windows y matriz de entornos | ⬜ Planificada | — | [plan](development/048-portability-and-environment-matrix.md) |
| 049 | Distribución e instalación Windows | ⬜ Planificada | — | [plan](development/049-distribution-and-installation-v2.md) |
| 050 | Puerta de producto Universal Search 3.x | ⬜ Planificada | — | [plan](development/050-product-v3-gate.md) |

**Fases 031–040 cerradas. Fases 041–050 planificadas.** El gate ejecutable tiene **23 invariantes**
(`python -m evaluation.gate`); la puerta de rendimiento sabe decir «inconcluyente»
en vez de publicar una cifra tomada con el equipo ocupado; la puerta de
accesibilidad comprueba teclado, nombres, contraste y catálogo de textos. Cada
fase 031–040 tiene su informe con la puerta que la midió. El resultado y la
decisión de publicación están en el
[informe de la fase 040](development/040-v3-quality-gate-report.md).

Verificado en la fase 010: **208 tests en verde**
(`.venv\Scripts\python -m pytest`), E2E completo de indexador, CLI, GUI e
instalador. Posteriormente, pasada de auditoría + optimización con
perfilado: indexación ×16 (2000 docs 45,3s → 2,8s) y consulta media
48,6 → 34,4 ms —
[informe](development/optimization-report.md). La verificación más reciente,
fase 030, deja **916 tests passed y 3 skipped**, pyflakes limpio, una capa
semántica local opcional medida, protección de privacidad para sus vectores,
comandos open/reveal, DPI por monitor, el verbo Explorer per-user reversible,
autodiagnóstico, paquete de soporte declarado, recuperación con casos
nombrados, puertas de CI verificadas por test y un gate v2 ejecutable de 13
invariantes (`python -m evaluation.gate`); detalles y límites en el
[informe de la fase 030](development/030-v2-quality-gate-report.md).

Current test count: 1216 tests collected.

## Cómo ejecutar

```bash
universal-search gui                   # ventana de búsqueda
universal-search tray                  # bandeja opcional de Windows
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
  procesos GUI/indexador/bandeja.
- [RANKING.md](RANKING.md) — fórmula de scoring y pesos.
- `development/` — un prompt por fase y, para las completadas, su informe
  (qué se hizo, decisiones, dependencias, limitaciones, tests, criterios de
  aceptación y cómo ejecutarla), más el
  [informe de auditoría + optimización](development/optimization-report.md).

## Convención de fases

Cada fase se considera terminada solo cuando: todos los tests pasan (los
anteriores incluidos), hay cobertura de tests propia, se documenta en un
informe y se marca en `ROADMAP.md`. Las fases entregadas siguen esa convención.
El alcance pendiente a partir de la fase 041 queda reflejado en `ROADMAP.md`.
