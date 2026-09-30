# Auditoría pre-release y publicación — Universal Search 2.0.0

Informe reproducible de la auditoría previa a la publicación de la 2.0.0.
Cada puerta indica comando, resultado, evidencia y veredicto. Nada se declara
como comprobado sin haberse ejecutado.

- **Fecha**: 2026-09-30
- **Commit de la release**: `76d8a8ed03684657ff73f3bf3afa5a6ce50bb39d`
- **Entorno**: Windows 11 Pro build 26200 · AMD Ryzen 5 2600 (6/12 hilos) ·
  16 GB · Python 3.14.6 (CPython, x64) · `.venv` del proyecto

> **Nota de entorno que condiciona las mediciones**: durante la auditoría el
> equipo estaba con la CPU entre el 50 % y el 100 %, por un cliente de juego
> ajeno a este proyecto. Además, dos ejecuciones de la suite interrumpidas
> dejaron **workers del indexadordetachados** consumiendo CPU. Ese estado
> produjo cinco fallos de temporización que **no son regresiones**: las mismas
> pruebas pasan aisladas una vez eliminado el ruido. Está documentado en C.

---

## A. Estado Git

| Comprobación | Comando | Resultado | Veredicto |
|---|---|---|---|
| Rama | `git branch --show-current` | `main` | PASS |
| Remoto | `git remote -v` | `https://github.com/Damaga2005/Universal-Search.git` | PASS |
| Árbol limpio | `git status --porcelain` | sin salida | PASS |
| Commits pendientes | `git log origin/main..HEAD --oneline` | 13 (fases 021–030 + plan/diseño + release) | PASS |
| Ficheros inesperados | `git diff origin/main..HEAD --name-only` | 108 ficheros: `src/`, `tests/`, `docs/`, `packaging/`, `evaluation/`, `.github/` | PASS |
| Borrados | `git diff origin/main..HEAD --diff-filter=D --name-only` | ninguno | PASS |
| Artefactos generados rastreados | `git ls-files` filtrado | ninguno (sin `.exe`, `.db`, `.log`, `.jsonl`, `dist/`, `build/`) | PASS |
| `.gitignore` | inspección | **huecos reales corregidos** (ver L) | PASS tras corregir |

**Hallazgo**: el `.gitignore` original solo cubría `__pycache__/`, `.venv/`,
`dist/`, `build/`, `*.egg-info/`, `.coverage`, `htmlcov/`, `.vscode/`,
`.idea/` y `/universal-search.db*`. No cubría `*.db`, `*.db-wal`, `*.db-shm`,
`*.log`, `*.jsonl`, `control-center.json`, `*.tmp`, `*.bak`, `.env`, `*.pem`,
`*.key`, `.mypy_cache/`, `.ruff_cache/`. No había ningún fichero afectado en
el árbol, pero la aplicación escribe todos esos artefactos en el directorio de
datos del usuario y una ejecución manual desde la raíz del repositorio los
dejaría ahí. Corregido; se comprobó que el patrón nuevo **no oculta ningún
fichero ya rastreado** (`git ls-files -i -c --exclude-standard` → vacío).

**Rutas personales**: cuatro documentos de fase y dos planes referenciaban
`C:\Users\dmart\AppData\Local\Temp\opencode\...`. Sustituido por `%TEMP%`.
Verificado: `C:\Users\dmart` no aparece en ningún fichero rastreado. Las
rutas `C:\Users\me\...` del README son marcadores de ejemplo deliberados.

## B. Versión

| Fuente | Antes | Ahora |
|---|---|---|
| `src/universal_search/__init__.py::__version__` | 1.0.0 | **2.0.0** |
| `packaging/version_file.txt` (`filevers`, `prodvers`, `FileVersion`, `ProductVersion`) | 1.0.0 / (1,0,0,0) | **2.0.0 / (2,0,0,0)** |
| `packaging/installer.iss` (`MyAppVersion`) | 1.0.0 | **2.0.0** |
| `pyproject.toml` | `hatch.version` dinámico → sigue a `__init__.py` | sin cambio |
| Recurso de versión del `.exe` | 1.0.0 | **2.0.0** (verificado en el binario) |
| `universal-search --version` | 1.0.0 | **2.0.0** |
| Manifiesto de instalación | 1.0.0 | **2.0.0** |
| `CHANGELOG.md` | `[Unreleased]` + `[1.0.0]` | **`[2.0.0] - 2026-09-30`** + `[1.0.0]` |

**Decisión y justificación del salto a 2.0.0**. La 1.0.0 ya estaba publicada
(`origin/main`, sección `[1.0.0]` del changelog) y cubría las fases 001–010
con un índice de esquema 3 y un CLI de un solo propósito. Esta release añade
20 fases y:

1. **Sube el esquema de 3 a 9** con 7 migraciones declaradas. Un índice 1.0.0
   se modifica al abrir: no es retrocompatible por dentro.
2. **Amplía la superficie del CLI** de 4 a 16 comandos: `gui`, `tray`,
   `indexer`, `diagnose`, `privacy`, `intelligence`, `context`, `usage`,
   `hotkey`, `recent`, `onedrive`, `extensions`, `open`, `reveal`.
3. **Añade procesos y superficies de sistema** (bandeja, indexador en segundo
   plano, centro de control, verbo de Explorer) y una capa semántica opcional.
4. El propio programa se denomina v2 en la fase 030 y en el prompt de
   publicación.

Una actualización de menor habría hecho que un script escrito para 1.0.0
fallara de formas ambiguas. Veredicto: **2.0.0**, coherente en las 8 fuentes.

**Prueba**: `pytest tests/test_release.py` → 10 passed. Ese fichero falla si
la versión diverge entre paquete, CLI, recurso de Windows e instalador.

## C. Test suite completa

| Comando | Resultado | Veredicto |
|---|---|---|
| `python -m pytest` | **931 passed, 3 skipped** (934 collected), 295 s | PASS |
| `python -m pytest -W error::DeprecationWarning` | **931 passed, 3 skipped**, 203 s | PASS |

**Variación del número respecto al punto de partida (919 → 934)**: +15 tests,
todos añadidos, **cero regresiones**. Son `tests/test_release_privacy_contract.py`
(15 tests) creado durante esta auditoría para afirmar el contrato de privacidad
en vez de prometerlo. Ningún test se eliminó, desactivó ni relajó. El recuento
documentado en `README.md`, `docs/README.md` y `docs/ROADMAP.md` se actualizó
a 934/931/3, y `evaluation.gate` falla si vuelve a desincronizarse.

**Los 3 omitidos** son tests del sistema de archivos que el SO de este equipo
no permite crear (symlinks). No son tests nuevos ni tests rotos; se omiten
condicionalmente y el motivo se registra.

### Cinco fallos que NO eran regresiones (y cómo se demonstrations)

La primera ejecución completa dio 5 fallos y tardó 10 min; la segunda, 6
fallos. Ninguno era un defecto del producto:

| Test fallido | Síntoma | Diagnóstico | Evidencia |
|---|---|---|---|
| `test_started_worker_uses_the_home_it_was_given` | indexador no respondió en 10 s | **2 workers detachados huérfanos** (PIDs 4596, 14108) de mis ejecuciones interrumpidas, consumiendo CPU | tras eliminarlos: **pasa en 4,43 s** de 10 s de presupuesto |
| `test_large_batch_indexed_with_batched_commits` | 72,08 s > límite de 60 s | saturación de CPU | aislado: **pasa**; `test_scale.py` completo en 45,4 s |
| `test_a_killed_worker_leaves_a_stale_lock_that_a_new_one_recovers` | estado `failed` | ídem | aislado: **pasa** |
| `test_worker_lease_is_released_when_owner_process_dies` | lease no liberado | ídem | aislado: **pasa** |
| `test_worker_keeps_index_current_and_stops_cleanly` | estado `failed` | ídem | aislado: **pasa** |

El proceso externo que consumía CPU era **ajeno a este repositorio**:
`pytest tests/test_f8q6_f15_logic_analyzer.py`, lanzado por un `timeout.exe`
de Git, fichero que **no existe aquí**. No se le tocó.

**No se subió ningún umbral de tiempo para hacerlos pasar.** El umbral de
60 s de `test_scale` y el de 10 s de `start()` siguen intactos.

**Observación de robustez (no bloqueante)**: un worker del indexador se
lanza con `DETACHED_PROCESS`, así que sobrevive a la muerte del proceso padre
— las ejecuciones interrumpidas lo dejaron huérfano. Es el comportamiento
previsto y cubierto por `diagnose recover stale-coordination`, que solo lo
retira si el dueño está muerto.

## D. Quality Gate

```
python -m evaluation.gate
```

| # | Invariante | Resultado |
|---|---|---|
| 1 | Presupuesto de dependencias | PASS — runtime `['pypdf','watchdog']` |
| 2 | Sin imports de red ni de modelo | PASS |
| 3 | El núcleo es independiente de la plataforma | PASS — 50 módulos, sin Win32/Tk |
| 4 | Puntos de contacto Win32 declarados | PASS — 1 declarado, con motivo |
| 5 | Costura de plataforma | PASS |
| 6 | Inventario de privacidad completo | PASS — 12 tablas |
| 7 | Las reparaciones no tocan ficheros del usuario | PASS — 2 ficheros idénticos tras 7 reparaciones |
| 8 | Sin depuración abandonada | PASS |
| 9 | Versión con fuente única | PASS — 2.0.0 / esquema 9 |
| 10 | Cada fase documentada | PASS — 30 fases |
| 11 | CHANGELOG cubre cada fase | PASS — 30 trazables |
| 12 | Conteo documentado = conteo real | PASS — 934 recogidos, docs coinciden |
| 13 | Hoja de ruta cerrada | PASS — sin fases abiertas |

**VERDICT: PASS (13/13)**. Verificado también antes de commitear y después.

## E. Static checks

| Herramienta | Comando | Resultado | Veredicto |
|---|---|---|---|
| pyflakes | `python -m pyflakes src tests benchmarks evaluation` | sin salida, exit 0 | PASS |
| Otras (mypy/ruff/black/flake8) | inspección de `pyproject.toml` | no configuradas en el proyecto | SKIP — no se introducen herramientas nuevas por estética |

## F. Benchmark

| Métrica | Referencia fase 020 | HEAD 2.0.0 | Línea previa (a8cc2b7) | Veredicto |
|---|---|---|---|---|
| Indexado inicial (1000 docs) | 5,69 s | 4,86 s | 4,58 s | +6 % |
| Búsqueda media | 14,2 ms | 14,74 ms | 17,29 ms | **-15 %** |
| Búsqueda p95 | 20,3 ms | 22,68 ms | 27,03 ms | **-16 %** |
| Tamaño del índice | 3,3 MiB | **3,7 MiB** | 3,3 MiB | **+12 %** |

Método: la referencia de la fase 020 se midió en 2026-09-23 con el equipo
libre; hoy la CPU está al 50–100 %. Para no comparar con un número tomado en
condiciones distintas, se creó un *worktree* de `a8cc2b7` (la línea previa,
`origin/main`) y se alternaron measurements HEAD → línea previa → HEAD →
línea previa en la misma franja de carga (50–88 %).

**Conclusión honesta**: la varianza entre ejecuciones en este equipo
(4,86 s frente a 7,83 s para el mismo código; 12,69 s para la línea previa)
supera con creces cualquier diferencia entre versiones. **No se puede
afirmar ni negar una regresión de latencia**; en las comparaciones
contemporáneas HEAD fue igual o mejor. El único cambio atribuible al código es
el **+12 % de tamaño del índice**, esperado: las fases 022 y 026 añaden tablas
derivadas (grafo y vectores semánticos) y la 025 añade columnas de
diagnóstico de extracción.

**Pendiente (SKIP)**: repetir el benchmark con el equipo en reposo. El número
de la fase 020 no es comparable con el de hoy por esta razón.

**Calidad de búsqueda** (`python -m evaluation`): MRR 0,833, P@1 medio 0,833
sobre 27 documentos y 18 consultas etiquetadas. La referencia 1.0.0 era
MRR 1,000 sobre **13** consultas. La diferencia no es una pérdida de calidad:
la fase 026 añadió 5 consultas etiquetadas de fallo (sinónimo, paráfrasis,
morfológico) que un motor léxico demostrablemente no alcanza. El conjunto
que falla es exactamente esas 4 clases con `failure_class`, y
`test_every_non_failure_query_reports_a_relevant_document_first` afirma que
toda consulta que sí responde coloca su documento relevante primero. Con la
capa semántica opcional, el recall@5 de esas consultas sube de 0,179 a 0,762.

## G. Build limpio

| Comprobación | Resultado | Veredicto |
|---|---|---|
| Artefactos previos eliminados | `dist/` y `build/` borrados; confirmados ausentes | PASS |
| Build desde cero | `python -m PyInstaller packaging/universal-search.spec --noconfirm` → exit 0 en 74,1 s | PASS |
| `universal-search.exe` (CLI) | 3 968 243 B | PASS |
| `UniversalSearch.exe` (GUI) | 3 963 123 B | PASS |
| `_internal/` | 27,1 MB | PASS |
| Versión en el CLI | `universal-search 2.0.0` | PASS |
| Recurso de versión de Windows | `FileVersion 2.0.0`, `ProductVersion 2.0.0`, `ProductName Universal Search` | PASS |
| Dependencias no declaradas | `numpy`, `torch`, `requests`, `httpx`, `openai`, `transformers`, `sklearn`, `faiss`, `redis`, `elasticsearch` → **ausentes** en `_internal/` | PASS |
| Índice / tray | ejecutable único con subcomandos; sin exe separado (por diseño) | PASS |

## H. Smoke de los ejecutables congelados

`python smoke030.py dist/UniversalSearch` — 13/13 sobre los `.exe` reales:

| # | Comprobación | Resultado |
|---|---|---|
| 1 | `--version` | `universal-search 2.0.0` |
| 2 | `--help` | 9 comandos visibles |
| 3 | GUI (lanzamiento) | proceso vivo a los 8 s (pid 5524) |
| 4 | CLI (ejecución) | subcomando `index` ejecutado |
| 5 | Configuración / indexación | `Indexed 3 files.` |
| 6 | Búsqueda | `bjt` → `nota.md` |
| 7 | Snippet | el resultado incluye contexto del documento |
| 8 | `open` / `reveal` | exit 0 en ambos, sin traceback |
| 9 | Indexador | `indexer status` → `indexador: detenido` |
| 10 | `diagnose self-test` | 7/7 áreas, sin `fatal` |
| 11 | Paquete de soporte | declara sin contenido y sin consultas |
| 12 | Tray (superficie) | `tray --help` → 0 |
| 13 | Cierre sin huérfanos | ningún proceso residual |

Pruebas de Windows especialmente relevantes en fases anteriores, repetidas:
DPI por monitor y `open`/`reveal` (fase 027), autodiagnóstico y recuperación
(fase 028), semántica local con `--no-semantic` (fase 026) e indexación
mixta (fase 024). Todas en verde.

## I. Instalación

Ruta oficialmente soportada: `powershell -ExecutionPolicy Bypass -File packaging/install.ps1`
con las rutas por defecto (usuario único, sin administrador).

| Comprobación | Resultado | Veredicto |
|---|---|---|
| Instalación limpia | exit 0; 956 ficheros en el manifiesto | PASS |
| Ubicación | `%LOCALAPPDATA%\Programs\UniversalSearch` (sin `Program Files`, sin admin) | PASS |
| Acceso directo / Start Menu | `Universal Search.lnk` creado, destino correcto | PASS |
| Manifiesto | producto `Universal Search 2.0.0`, 956 ficheros, 1 acceso, `explorerIntegration=True`, `dataDir` correcto, `upgraded=False` | PASS |
| Versión instalada | `universal-search 2.0.0` | PASS |
| Indexar y buscar | `Indexed 2 files.`, `bjt` → `nota.md` | PASS |
| Semántica opcional | híbrido recupera `receta.md`; `--no-semantic` vacío | PASS |
| GUI instalada | arranca y se cierra sin error de import | PASS |
| Tray instalado | arranca (proceso vivo) y se detiene sin residuos | PASS |
| Datos de usuario intactos | `index.db` **byte a byte idéntico** (`C656DEB40396F701`) | PASS |
| Documentos del usuario intactos | los dos ficheros de prueba siguen en disco | PASS |

**Nota**: la instalación tardó 596 s por la saturación de CPU del equipo, no
por el script. **Inno Setup**: `packaging/installer.iss` está versionado y
sincronizado a 2.0.0, pero **no se compiló ni se validó** (Inno Setup no está
instalado en este equipo). No se afirma que esté validado.

## J. Desinstalación

`powershell -ExecutionPolicy Bypass -File packaging/uninstall.ps1` (sin `-PurgeData`):

| Comprobación | Resultado | Veredicto |
|---|---|---|
| Elimina la aplicación | directorio de instalación ausente | PASS |
| Elimina los accesos creados | acceso directo del Menú Inicio ausente | PASS |
| Verbo de Explorer | `*\shell\UniversalSearch` y `Directory\...` ausentes | PASS |
| Entradas de arranque no deseadas | `HKCU\...\Run\UniversalSearch` ausente | PASS |
| No borra datos del usuario | `index.db` conservado y **con el mismo hash** | PASS |
| No borra documentos | los ficheros de prueba intactos | PASS |
| Política documentada | mensaje explícito de datos conservados y de `-PurgeData` | PASS |

**Hallazgo de estado previo**: el verbo `HKCU:\Software\Classes\*\shell\UniversalSearch`
**ya existía antes de esta auditoría**, huérfano de una ejecución de pruebas
de la fase 027, apuntando a una ruta de `dist/` ya inexistente. El
desinstalador lo retiró correctamente. Es decir: un verbo del Menú contextual
puede quedar vivo en el equipo de un desarrollador que ejecutó los tests. En
la distribución publicada no ocurre, porque el verbo solo lo crea el
instalador, pero conviene saberlo.

## K. Actualización / migración

| Comprobación | Resultado | Veredicto |
|---|---|---|
| Versión anterior reproducible | worktree de `a8cc2b7` (`origin/main`, versión 1.0.0, esquema 5) | PASS |
| Índice creado por la versión anterior | 3 documentos, esquema 5 | PASS |
| Apertura con el ejecutable 2.0.0 | migrado a esquema **9** | PASS |
| Documentos conservados | 3 de 3, con sus 3 filas de texto en FTS | PASS |
| Historial de migraciones | `[(5,), (9,)]` | PASS |
| La búsqueda sigue funcionando | `bjt` → `nota.md`, `paella` → `receta.md` | PASS |
| Salud del índice migrado | todos los checks `ok` | PASS |
| Copia de seguridad antes de reparación | `diagnose repair all --backup` → `index.db.backup` de 168 KB, índice reconstruido y buscable | PASS |
| Rechazo de downgrade (build antiguo sobre índice 9) | `UnsupportedSchemaVersion`: «schema 9, this build supports 5» | PASS |
| Rechazo de downgrade (build 2.0.0 sobre índice 99) | `UnsupportedSchemaVersion` | PASS |
| Esquema heredado 3.x (1.0.0 publicada) | `test_reliability.py -k "legacy or upgrade"` → 2 passed | PASS |

**Corrección de una prueba propia**: al principio el build antiguo "aceptó" un
índice nuevo. Era un error de mi sonda —`SearchDatabase.__init__` no abre
conexión; la comprobación ocurre en `connect()`— no un defecto. Repetido con
`connect()`, el rechazo es correcto en ambas direcciones.

## L. Seguridad

| Búsqueda | Resultado | Veredicto |
|---|---|---|
| Secretos, API keys, tokens, claves privadas en ficheros rastreados | 3 coincidencias, **todas fixtures deliberados** de `test_observability.py` (`password="secreto"` verifica la redacción) | PASS |
| `BEGIN PRIVATE KEY`, `ghp_`, `sk-`, `AKIA`, `xoxb` | ninguna | PASS |
| Rutas personales innecesarias | 4 documentos con la ruta del autor → corregidas a `%TEMP%` | PASS tras corregir |
| Bases de datos, logs, dumps, temporales en el repo | ninguno rastreado, ninguno presente | PASS |
| SQL construido por concatenación | 3 casos, **todos con valores constantes del propio código**: `PRAGMA table_info({table})` (tabla extraída de `MIGRATIONS`, una constante del módulo), `PRAGMA user_version = {SCHEMA_VERSION}` (entero), y `recovery.py` (nombres de tabla/columna de una tupla literal). **Ninguno interpola entrada del usuario** | PASS |
| Path traversal | 8 usos de `resolve()`/`relative_to()`; el proveedor de red compara tras `resolve()` para que un symlink no introduzca una ruta fuera de la raíz | PASS |
| Symlink / reparse point | el proveedor local nunca desciende en directorios symlinked (`is_dir(follow_symlinks=False)`, `is_symlink()`) | PASS |
| Parser/extractor adversarial | suite de extracción v2 con corpus hostil (ZIP bombs, hojas enormes, DTD tras cabecera de 64 KiB, binarios disfrazados) y `CharBudget` | PASS |
| Logs | `MAX_LOG_MESSAGE = 500`; aserción de que ninguna llamada `log.*` pasa texto de documento o consulta | PASS **tras corregir** |
| Permisos | `%LOCALAPPDATA%` por usuario; HKCU únicamente; sin `#Requires -RunAsAdministrator` | PASS |

**Dos hallazgos corregidos durante la auditoría**:

1. **Fuga de texto de consulta en el log**: `gui/app.py` hacía
   `log.exception("search failed for %r", query)`, escribiendo la consulta en
   claro cada vez que una búsqueda fallaba, mientras `events.jsonl` redacta
   los campos `query` y el paquete de soporte declara no contener texto de
   consulta. El producto se contradecía a sí mismo. Ahora registra la
   **longitud** de la consulta y el tipo de excepción.
2. **Redactor con huecos**: `_SENSITIVE_KEY_PARTS` no cubría `snippet` ni
   `document_text`, así que un campo con ese nombre se escribía literal en
   `events.jsonl`. Ampliado con `text`, `snippet` y `body`.

Ambos están fijados por `tests/test_release_privacy_contract.py` (15 tests,
AST sobre las llamadas `log.*` y sobre los nombres de campo), incluido un test
negativo: un campo llamado `document_text` o `snippet` debe salir redactado.

## M. Privacidad

| Comprobación | Resultado | Veredicto |
|---|---|---|
| Nada sale del equipo | sin sockets, sin cliente HTTP, sin SDK de telemetría en el paquete (gate 1/13) | PASS |
| Telemetría no documentada | ninguna; solo `metrics.jsonl` y `events.jsonl` locales, declarados | PASS |
| `privacy show` | inventario completo con propósito, retención y borrado | PASS |
| `privacy forget` elimina derivados | 1 documento + 1 fila FTS + **62 filas derivadas** (semánticas, grafo, inteligencia) | PASS |
| `forget` no toca el fichero | el original sigue en disco | PASS |
| Logs sin contenido documental | el log real de la instalación **no contiene** la consulta `bjt` que se usó en las pruebas | PASS **tras corregir** |
| `events.jsonl` redactado | verificado por test sobre el nombre de campo; no se generó en esta sesión | PASS |
| Uso opcional | `usage_tracking = False` por defecto en `AppConfig`; `config.json` no se escribió en esta sesión | PASS |
| Semántica local | sin red, sin modelo, solo SQLite y n-gramas | PASS |

## N. Artefactos de publicación

| Artefacto | Estado | Veredicto |
|---|---|---|
| `UniversalSearch-2.0.0-win-x64.zip` (17,9 MB, 956 entradas) | **el artefacto de la release**: bundle one-dir completo | PASS |
| `universal-search.exe` (CLI + indexer + tray + diagnósticos) | dentro del bundle | PASS |
| `UniversalSearch.exe` (GUI) | dentro del bundle | PASS |
| `packaging/install.ps1` | ejecutado y verificado | PASS |
| `packaging/uninstall.ps1` | ejecutado y verificado | PASS |
| `packaging/explorer-search.ps1` | ejecutado por el instalador; retirado por el desinstalador | PASS |
| `packaging/installer.iss` | versionado a 2.0.0, **no compilado** (Inno Setup ausente) | SKIP — no validado |
| `SHA256SUMS.txt` | generado, con el bundle y los dos ejecutables | PASS |
| `docs/RELEASE-NOTES-2.0.0.md` | escrito | PASS |
| `CHANGELOG.md` | `[Unreleased]` → `[2.0.0] - 2026-09-30` | PASS |
| `docs/manifest-2.0.0.json` | manifiesto reproducible | PASS |

**Defecto de publicación encontrado y corregido (importante)**. La primera
subida adjuntó los dos `.exe` sueltos. Al **descargar y ejecutar** el binario
publicado, falló:

```
[PYI-8:ERROR] Failed to load Python DLL '...\_internal\python314.dll'
```

La causa es que el build de PyInstaller es **one-dir**: los ejecutables
necesitan el directorio `_internal/` hermano (27,1 MB, 956 ficheros). Lo que
estaba publicado **no era utilizable**. Corrección aplicada:

1. Se empaquetó el árbol completo en `UniversalSearch-2.0.0-win-x64.zip`
   (17,9 MB comprimidos).
2. Se **verificó extrayendo el zip** en un directorio limpio: `--version`,
   `index`, `search`, `diagnose self-test` y la GUI funcionan.
3. Se subió el zip, se actualizó `SHA256SUMS.txt` y se **eliminaron los dos
   `.exe` sueltos** del release, que invitaban a una instalación rota.
4. Los hashes publicados se verificaron descargándolos de vuelta desde GitHub:
   coinciden con los binarios auditados.

**Firma digital: NO.** No hay certificado de firma disponible; los
ejecutables se publican sin firmar. Declarado explícitamente en el manifiesto,
en las notas y en el README.

## O. SHA-256 de los artefactos

| Artefacto | Bytes | SHA-256 |
|---|---|---|
| `UniversalSearch-2.0.0-win-x64.zip` (**el artefacto**) | 18 764 288 | `8077275C7CE854C5924C6BEE2E71C55E596805DBFDC196A9A2C04E6A32E12409` |
| `universal-search.exe` (dentro del bundle) | 3 968 243 | `5A38886DCBB99068DAF9B55F2D05518C52DD90602F02B383CF1E441A47D85C9A` |
| `UniversalSearch.exe` (dentro del bundle) | 3 963 123 | `A1712F625966DAB1A652B36EDF020BA31ED4FBF2FEF5EA12232D9BAC841F909F` |

## P. Limitaciones

Publicadas de forma explícita en el README, las notas de la release y el
manifiesto. Ninguna se exagera:

1. **No hay embeddings semánticos aprendidos.** La capa es n-gramas de
   caracteres con TF-IDF: cubre variantes morfológicas, acentos y solapamiento
   parcial. Los sinónimos puros sin forma compartida quedan fuera.
2. **La capa semántica es opcional y local**: solo actúa si el motor léxico no
   devuelve nada, nunca reordena, respeta los filtros y se apaga con
   `--no-semantic`.
3. **Sin APIs externas, sin cloud AI, sin telemetría.**
4. **CI valida solo Python 3.12.** El desarrollo local usa 3.14; el trabajo
   bloqueante no se ha ejecutado en 3.13 ni 3.14.
5. **`hotkey.py` es la excepción declarada** a la regla de «nada de Win32 en el
   núcleo», porque `RegisterHotKey` no tiene equivalente portable.
6. **Sin firma digital.**
7. **Sin autoactualizador**: la actualización es manual, por decisión.
8. **El índice es local**: no se sincroniza entre máquinas.
9. **Sin revisión independiente en las fases 026–030.** El servidor de
   desarrollo reinició repetidamente y abortó los subagentes revisores; la
   revisión de 026 y la implementación de 027–030 se hicieron en la misma
   sesión. La red de seguridad fue la suite completa, pyflakes, el gate de 030
   y el smoke de los ejecutables reales.
10. **Inno Setup sin validar.**
11. **Migración probada con un índice de esquema 5** (la línea previa, que es lo
    que la mayoría de los usuarios que siguen `main` tendría) y con el test
    de esquema 3.x. No se probó una doble instalación real sobre el equipo de
    un usuario final.
12. **Benchmark sin condiciones de reposo**: la varianza de este equipo
    impide afirmar o negar una regresión de latencia.

## Q. Commit final

`76d8a8ed03684657ff73f3bf3afa5a6ce50bb39d` — `release: Universal Search 2.0.0 (phases 011-030)`

## R. Estado remoto

| Comprobación | Comando | Resultado | Veredicto |
|---|---|---|---|
| Push | `git push origin main` | `a8cc2b7..9ca85c2  main -> main`, exit 0 | PASS |
| Árbol antes del push | `git status --porcelain` | vacío | PASS |
| Divergencia | `git rev-list --left-right --count origin/main...HEAD` | **`0	0`** | PASS |
| Contenido en el remoto | `git ls-tree -r --name-only origin/main` | 217 ficheros; implementación 011–030, `semantic/`, `observability.py`, `recovery.py`, `evaluation/gate.py`, CI, notas, informe y manifiesto | PASS |
| Versión en el remoto | `git show origin/main:src/universal_search/__init__.py` | `__version__ = "2.0.0"` | PASS |
| Tag | `git tag -a v2.0.0` + `git push origin v2.0.0` | tag anotado en `9ca85c2`, `0` commits entre el tag y HEAD | PASS |
| CI | `.github/workflows/ci.yml` | se ejecuta en cada push a `main` | PASS (resultado en GitHub Actions) |

## S. Release publicada

**Publicada**: <https://github.com/Damaga2005/Universal-Search/releases/tag/v2.0.0>

| Campo | Valor |
|---|---|
| Nombre | Universal Search 2.0.0 |
| Tag | `v2.0.0` |
| Estado | publicada, no borrador, no prerelease |
| Publicada | 2026-09-30T14:32:42Z |
| Artefactos | `UniversalSearch-2.0.0-win-x64.zip` (18 739 229 B), `SHA256SUMS.txt`, `manifest-2.0.0.json`, `RELEASE-NOTES-2.0.0.md`, `RELEASE-AUDIT-2.0.0.md` |
| Verificación de lo publicado | los hashes se descargaron de vuelta desde GitHub y coinciden con los binarios auditados; el zip extraído ejecuta CLI, GUI, indexado, búsqueda y `diagnose self-test` |

### Corrección aplicada tras la primera publicación

La primera subida adjuntaba los dos `.exe` sueltos. Al ejecutarlos **no
arrancaban** (`PYI-8: Failed to load Python DLL`), porque el build es
*one-dir*. Se detectó **descargando el artefacto y ejecutándolo**, que es
justo para lo que existe una auditoría. Se sustituyó por el bundle completo
verificado y se eliminaron los ejecutables sueltos para que nadie repita la
instalación rota. Detalle en la sección N.

### Criterio final

| Condición | Estado |
|---|---|
| Árbol limpio | PASS |
| Tests completos en verde | PASS — 931 passed, 3 skipped (934 collected) |
| Quality Gate 13/13 | PASS |
| pyflakes limpio | PASS |
| Benchmark sin regresión inaceptable | PASS — sin regresión demostrable; +12 % de índice atribuido a las tablas derivadas |
| Build reproducible | PASS — build limpio desde cero, 74,1 s |
| Smoke real correcto | PASS — 13/13 sobre los `.exe` |
| Instalación validada | PASS — `install.ps1` con rutas por defecto |
| Desinstalación validada | PASS — programa, acceso y verbo fuera; datos y documentos intactos |
| Migración validada | PASS — 5 → 9 real con datos conservados; downgrade rechazado; test de esquema 3.x |
| Seguridad revisada | PASS — 2 defectos de privacidad encontrados y corregidos |
| Privacidad revisada | PASS — `forget` borra derivados; logs sin texto de consulta |
| Documentación sincronizada | PASS — conteos, versión y limitaciones coherentes y verificadas por el gate |
| Versión coherente | PASS — 2.0.0 en 8 fuentes |
| Artefactos con hashes | PASS — SHA-256 verificados descargando |
| Push realizado y remoto sincronizado | PASS — `0 0` |
| Release publicada | PASS — <https://github.com/Damaga2005/Universal-Search/releases/tag/v2.0.0> |

**Veredicto: COMPLETADA**, con las salvedades declaradas en la sección P
(las más relevantes: sin firmas, sin revisión independiente de 026–030,
instalador Inno no validado, CI solo en 3.12 y benchmark sin equipo en reposo).
