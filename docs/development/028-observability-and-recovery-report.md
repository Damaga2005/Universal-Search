# Fase 028 — Observabilidad y recuperacion local

## Que se entrego

1. **Eventos estructurados** (`src/universal_search/observability.py`):
   `EventRecorder` escribe JSON Lines con esquema fijo
   (`at`, `component`, `event_id`, `severity` + campos), rota por tamano
   (1 MB x 3) y **redacta por nombre de campo**: cualquier clave que contenga
   `password`, `token`, `secret`, `credential`, `content` o `query` se
   escribe como `[redacted]`, y los valores de texto se cortan a 300
   caracteres. No hay texto de documento ni de consulta en ningun campo.
2. **Autodiagnostico** (`diagnose self-test`): ejerce base de datos, FTS,
   esquema, proveedores, extractores, trabajador y espacio en disco, y devuelve
   el peor veredicto (0 ok, 1 warning, 2 fatal). Cada comprobacion es un
   `CheckResult` con nombre, estado y detalle acotado.
3. **Paquete de soporte** (`diagnose export --output RUTA`): JSON con
   declaracion explicita (`contains_document_content: false`,
   `contains_query_text: false`, `contains_credentials: false`) mas los
   veredictos y el espacio libre. No es un zip opaco: declara su contenido.
4. **Recuperacion con casos nombrados** (`src/universal_search/recovery.py`):
   solo cuatro casos, cada uno con un alcance declarado:
   - `orphan-derived`: borra filas derivadas (semantico, grafo) cuyo documento
     ya no existe. No toca documentos ni ficheros.
   - `dirty-derived`: marca el grafo y el indice semantico para reconstruir.
   - `stale-coordination`: retira lock, owner, estado, pause/stop y claims de
     arranque **solo** si el proceso dueno esta muerto. Con dueno vivo
     devuelve `owner-alive` y no cambia nada.
   - `reset-derived`: borra todo lo derivado (requiere `--yes`).
   Un caso desconocido lanza `ValueError`: no hay un "arreglar todo" genérico.
5. **Centro de control**: botones "Autodiagnostico" y "Paquete de soporte…",
   ambos con `ActionResult` tipado (`data_scope="none"`). Cada accion del
   centro de control deja ahora un evento acotado en `events.jsonl`.

## Por que esta forma

- **Sin dependencias**: JSON Lines, `hashlib`-free, `shutil` y SQLite. Nada de
  APM, nada de red, nada de telemetria saliente.
- **Un caso = un alcance**: la recuperacion no puede tocar ficheros del
  usuario; el test lo verifica leyendo el fichero fuente tras la operacion.
- **Fallo tipado**: el self-test y el paquete nunca lanzan excepciones a la
  interfaz; devuelven estado o un `ActionResult` de error.

## TDD log

- RED: `tests\test_observability.py` y `tests\test_recovery.py` ->
  `ModuleNotFoundError` (no existian los modulos).
- GREEN: los modulos pasaron a verde; el primer fallo real fue mio en el test
  (`paths.database` es un `Path`, no la base de datos) y las columnas `NOT
  NULL` de `document_graph_nodes`; se corrigio el test, no el codigo.
- RED: los tests del CLI fallaron con "DID NOT RAISE SystemExit" porque
  `main()` solo lanza `SystemExit` cuando el codigo es distinto de 0; se
  ajustaron las aserciones al contrato real del CLI.
- RED: `test_rebuild_kinds...` detecto que el evento de la reconstruccion
  completa se llama `rebuild-full` (el `_run_locked` ya normalizaba el
  nombre); se ajusto la expectativa al identificador real.
- Suite completa: **874 passed, 3 skipped**. pyflakes limpio.

## Compatibilidad y limitaciones

- `events.jsonl` se declara en el inventario de privacidad (fichero, para que
  se vea) y en `docs/PRIVACY.md`.
- El paquete de soporte no incluye el log, ni la base de datos, ni rutas de
  usuario: solo veredictos y contadores. Es deliberado; un soporte con datos
  reales seria un problema de privacidad, no una ayuda.
- `stale-coordination` depende de `process_alive`; un PID reutilizado por el
  sistema puede dar un falso "vivo". Es el mismo riesgo que ya acepta el
  lock del trabajador en la fase 021, y por eso el caso nunca borra el lease
  persistente: solo los ficheros de estado.
- El self-test abre la base de datos real en modo lectura; no escribe filas.
