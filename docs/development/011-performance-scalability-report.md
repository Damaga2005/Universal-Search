# Fase 011 — Performance & Scalability

## Qué se entregó

1. **Suite de benchmarks reproducible** (`benchmarks/`, ejecutable con
   `python -m benchmarks`): corpus sintético determinista (sin RNG ni
   reloj), perfiles 1000 / 10000 / 100000 documentos, fuera del CI
   ordinario por diseño (solo el perfil 1000 dura ~15 s).
   Mide: indexación inicial, indexación incremental, actualización de un
   documento, reconciliación de borrados, latencia de búsqueda (fría y
   caliente por forma de consulta), apertura de base de datos y memoria
   pico durante indexación y búsqueda (`--memory`, vía `tracemalloc`).
2. **Métricas locales** (`src/universal_search/metrics.py`):
   colector con anillo acotado (512 registros), sink JSONL
   append-only con throttle de 5 s y compactación automática
   (a los últimos 200 registros al pasar de 512 KB). Cableado real:
   `SearchEngine.search` registra latencia + nº de resultados,
   `Indexer.index_root` registra duración + contadores de `IndexStats` +
   `db_writes` (delta de `connection.total_changes`), y los puntos de
   entrada (`SearchService`, `background.reconcile`, CLI) fijan el sink
   (`AppPaths.metrics_file` → `metrics.jsonl`) y lo exponen:
   `universal-search index` imprime `elapsed=…s db_writes=…` y
   `metrics.summary()` queda como superficie para la fase 015.
3. **Mantenimiento de la base de datos**: `SearchDatabase.sizes()` y
   `SearchDatabase.maintenance(vacuum=…)` (checkpoint `TRUNCATE` a
   demanda + `VACUUM` opcional) fijan la estrategia de crecimiento.
4. **Invalidación explícita de cachés**: `ranking.clear_caches()` limpia
   las cuatro cachés de ranking; test de techo y de golpe de caché.
5. **17 tests nuevos** (`test_scale.py` 7, `test_metrics.py` 10).

## Metodología de benchmark

- Determinismo total: el contenido de cada archivo se deriva de
  aritmética sobre su índice (`WORDS[(i*7 + k*13) % len]`), carpeta
  cíclica entre 6 dominios y la frase `ebers moll` en el 10 % de los
  documentos. Dos ejecuciones indexan bytes idénticos.
- Cada perfil es un árbol temporal real (E/S de disco incluida, como en
  producción) + la base de datos fuera del árbol indexado.
- `--memory` añade `tracemalloc` solo cuando se pide (su overhead es
  medible: indexar 1000 docs pasa de 4.53 s a 7.66 s); las cifras
  titularas usan la pasada limpia.
- Salida ASCII (compatible con la consola OEM de Windows) y `--json`
  para archivar corridas.

## Resultados — perfil 1000 (limpio, sin instrumentación)

| Métrica | Valor |
|---|---|
| Escritura del corpus (1000 archivos) | 1.487 ms |
| Apertura de BD (media de 10; la 1ª crea el esquema) | 7.66 ms |
| Indexación inicial | 4.53 s (4.5 ms/doc) |
| Pase incremental (sin cambios) | 0.176 s → **25× más barato** que construir |
| Actualización de 1 documento (con escaneo completo) | 0.200 s |
| Actualización masiva (10 docs) | 0.278 s |
| Reconciliación de borrados (10 docs) | 0.211 s |
| Búsqueda fría (1.ª tras abrir) | 31.6 ms |
| Búsqueda caliente media / p50 / p95 / máx (150 muestras) | 20.6 / 23.6 / 31.4 / 44.6 ms |
| Pico de memoria indexando | 2.3 MiB |
| Pico de memoria buscando | 2.1 MiB |
| Tamaño del índice (1000 docs) | 3.3 MiB |

Las cinco formas de consulta del bloque caliente: término único, AND
multi-término, frase exacta, `path:` + término y consulta sin
resultados (peor caso). Memoria pico ~2 MiB confirma que el corpus
**nunca** se carga entero en RAM (requisito de la spec).

## Resultados — perfil 10000

| Métrica | Valor |
|---|---|
| Escritura del corpus (10000 archivos) | 19.88 s |
| Apertura de BD (media de 10) | 8.06 ms |
| Indexación inicial | 191.45 s (19.1 ms/doc; FTS/WAL crecen con el índice) |
| Pase incremental (sin cambios) | 1.02 s → **187× más barato** que construir |
| Actualización de 1 documento (escaneo de 10k) | 1.07 s |
| Actualización masiva (100 docs) | 3.05 s |
| Reconciliación de borrados (100 docs) | 2.09 s |
| Búsqueda fría | 43.2 ms |
| Búsqueda media / p50 / p95 / máx | 28.5 / 29.7 / 49.0 / 61.0 ms |
| Tamaño del índice (10000 docs) | 31.3 MiB (3.2 KB/doc, lineal) |

Lectura clave: **la latencia de búsqueda es plana con el tamaño del
índice** (20.6 ms con 1000 docs, 28.5 ms con 10000 — 10× documentos,
+38 % de media) porque el *pool* de candidatos va acotado por FTS+bm25
con `LIMIT` antes de puntuar. El coste que sí crece es la indexación
inicial (FTS/WAL sobre árboles más grandes); el pase incremental se
mantiene en ~1 s porque solo escanea metadatos.

El perfil 100000 existe como opción de la suite
(`--profile 100000`, requiere ~200 MB de disco libres y varios
minutos) y queda **fuera del CI** por decisión de diseño, tal como
permite la spec.

## Auditoría SQLite/FTS (con evidencia, sin cambios gratuitos)

- **Índices**: sin altas nuevas. La reconciliación usa
  predicados de rango sobre `path` (cubierto por el `UNIQUE` de
  `documents.path`), `usage_events(document_id)` cubre los consumos de
  uso, y FTS5 con contenido es obligatorio para snippets. Añadir un
  índice sin evidencia de cuello de botella sería especulación.
- **Transacciones/batching**: se mantiene `COMMIT_EVERY=200` (un commit
  amortizado cada 200 cambios); el test de lote grande cruza tres veces
  ese límite.
- **WAL/checkpoint**: el autocheckpoint de SQLite mantiene el WAL
  estable (~4 MB medidos); `maintenance()` ofrece un `TRUNCATE`
  explícito y el test verifica que el WAL queda en 0 tras él.
- **Ciclo de vida de conexiones**: `index_root` abre y cierra una
  conexión por pase (~12 ms amortizados por pase, insignificante frente
  a los segundos de un pase); el `Indexer` reutiliza su conexión de
  escritura para `upsert`. **No se introduce paralelismo**: la spec lo
  exige solo si los benchmarks lo demuestran, y un único escritor
  SQLite ya está limitado por E/S (4.5 ms/doc); paralelizar solo
  añadiría contención.
- **Crecimiento/mantenimiento**: `sizes()` reporta BD/WAL/SHM;
  `maintenance(vacuum=True)` devuelve `freelist_before/after` y el test
  demuestra `freelist_after == 0` y `wal == 0` tras la operación.

## Cachés: techo + invalidación + tests

| Caché | Clave | Techo | Invalidación | Test |
|---|---|---|---|---|
| `content_words` | SHA-256 del contenido | 1024 (vaciado al llenarse) | `clear_caches()` | techo con cap=2, golpe por identidad |
| `_parse_iso` | cadena ISO exacta | `lru_cache(4096)` | `clear_caches()` | poblado + limpiado |
| `_name_parts` | nombre exacto | `lru_cache(4096)` | `clear_caches()` | poblado + limpiado |
| `_path_component_tokens` | ruta exacta | `lru_cache(4096)` | `clear_caches()` | poblado + limpiado |

Ninguna caché puede devolver datos obsoletos: todas las claves son
valores inmutables (una coincidencia es byte-idéntica); `clear_caches()`
existe para reconstrucciones y tests que necesitan estado frío
verificable.

## Métricas: qué se guarda y qué nunca se guarda

Se guarda: latencia (ms), nº de resultados, duración del pase (s),
contadores de `IndexStats` (escaneados/creados/actualizados/sin
cambios/borrados/ignorados/con error/fallo de extracción/solo nube) y
`db_writes` (filas afectadas, delta exacto de `total_changes`).
**Nunca** se guarda: texto de consulta, rutas, nombres ni contenido
(la API ni siquiera acepta el texto — verificado por
`inspect.signature` en un test).

Superficies de exposición:

- `metrics.jsonl` en el directorio de datos de la aplicación (append,
  throttle 5 s, compactación, líneas a medio escribir se ignoran al
  leer).
- `universal-search index` → `elapsed=…s db_writes=…` en la salida.
- `metrics.summary()` / `metrics.records()` (API para la fase 015).
- `python -m benchmarks --json resultados.json` para corridas
  archivables.

## Tests (225 en verde; +17 sobre la base de 208)

- `test_scale.py`: lote grande (600 docs, cruza `COMMIT_EVERY` ×3,
  conteos y viaje de ida y vuelta a FTS), incremental materialmente más
  barato que construir, **búsqueda concurrente con indexación** (2
  lectores WAL + 1 escritor, sin errores, 120 resultados finales sin
  duplicados), **trabajo interrumpido** (crash simulado en el ítem 301 →
  sobreviven exactamente los 200 del lote comprometido, el resto hace
  rollback, el reanexo completa a 600 sin duplicados), cachés acotadas
  e invalidación explícita, resultados deterministas entre motores y
  estados de caché, y manejo de recursos (reutilización/cierre
  idempotente + `maintenance` limpio).
- `test_metrics.py`: registro de búsqueda, ausencia de texto de
  consulta, anillo acotado con contador de descartes, aritmética de
  `summary`, registro de pase con `db_writes`, sink append-only,
  lectura robusta a líneas rotas, throttle + forzado, sink roto sin
  excepciones, y persistencia vía `SearchService`.

## Criterios de aceptación (spec 011)

| Requisito | Estado |
|---|---|
| Benchmarks reproducibles de las 8 métricas pedidas | ✅ suite `benchmarks/` |
| Perfiles 1000 / 10000 / 100000 | ✅ (100000 on-demand, fuera de CI) |
| Auditoría SQLite/FTS basada en evidencia | ✅ secciones anteriores |
| Lotes, sin cargar corpus en memoria, trabajos evitados, memoria acotada | ✅ 2.3 MiB pico; tests |
| Crash-safe + incremental | ✅ test de interrupción |
| Paralelismo solo si los benchmarks lo demuestran | ✅ no introducido, documentado |
| Métricas locales de las 7 categorías | ✅ `metrics.py` + CLI + sink |
| Sin servidor de BD | ✅ sigue siendo SQLite embebido |
| Vacuum/crecimiento | ✅ `maintenance()` + tests |

## Limitaciones

- El perfil 100000 es on-demand (minutos, ~200 MB de disco): la spec
  permite separarlo del CI.
- `db_writes` mide **filas afectadas**, no sentencias emitidas: es un
  proxy exacto y honesto del volumen escrito, no un contador de SQL.
- Las latencias del benchmark (contenido sintético, 5 formas de
  consulta) no son SLO; los presupuestos de `test_performance.py`
  siguen siendo *tripwires* de regresión.
- La primera apertura de BD incluye la creación de esquema (de ahí la
  media de 7.7 ms); aperturas posteriores son de 1–2 ms.

## Cómo ejecutar

```bash
python -m benchmarks                       # perfil 1000, ~15 s
python -m benchmarks --profile 10000       # ~2 min
python -m benchmarks --profile 100000 --memory --json out.json
.venv\Scripts\python -m pytest tests/test_scale.py tests/test_metrics.py -q
```
