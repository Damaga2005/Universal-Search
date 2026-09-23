# Fase 015 — Index Management & Diagnostics

## Qué se entregó

1. **`src/universal_search/diagnostics/`** — tres módulos y un paquete
   público, todos de solo lectura por defecto:
   - `stats.py` — `collect()`: número de documentos, con texto y
     solo-nube, reparto por tipo y por origen, tamaño de BD/WAL/SHM, filas
     de inteligencia, eventos de uso, versión de esquema y de aplicación,
     último pase (de las métricas locales) y estado del worker.
   - `health.py` — `check()`: doce comprobaciones, cada una con un
     veredicto y una frase accionable. Distingue **aviso** (degradado pero
     usable) de **fatal** (no se puede confiar en el índice).
   - `repair.py` — cinco operaciones con dos niveles de seguridad.
2. **Superficie CLI**: `diagnose summary | health | repair {reconcile,
   fts, extract, intelligence, all}`, con códigos de salida
   **0 sano / 1 avisos / 2 fatal**.
3. **Superficie GUI**: menú **Diagnóstico** con "Estado del índice"
   (informe de solo lectura en una ventana propia) y "Reconstruir índice
   completo…" **detrás de una confirmación explícita** (`askyesno`).
4. **Política de logs verificada**: el registro ya era local, rotado
   (1 MB × 3 copias), con timestamp y nivel; ahora un filtro
   (`_BoundedMessage`) **acota cada mensaje a 500 caracteres**, y dos tests
   lo comprueban: uno de formato/rotación y otro que indexa un PDF
   corrupto con una frase secreta dentro y verifica que esa frase no
   aparece nunca en el log.
5. **23 tests nuevos** (`tests/test_diagnostics.py`). Suite completa:
   **424 en verde**.

## Los tres estados que un usuario puede encontrar

| Estado | `diagnose health` | Código | Ejemplo |
|---|---|---|---|
| Sano | `ok` | 0 | índice completo, worker quieto |
| Degradado | `warning` | 1 | fila FTS huérfana, documento sin texto, bloqueo obsoleto |
| Roto | `fatal` | 2 | no hay índice, la BD no abre, falta la tabla `documents` |

## Comprobaciones y su veredicto

| Comprobación | Veredicto | Qué detecta |
|---|---|---|
| `database` | fatal | No existe (sin crearlo) o no se puede abrir |
| `schema` | fatal / warning | `user_version` distinto de la esperada |
| `schema.objects` | warning | Faltan objetos del esquema |
| `fts.coverage` | warning | Documentos sin fila de búsqueda |
| `fts.orphans` | warning | Filas de búsqueda sin documento |
| `documents.identities` | warning | Rutas duplicadas (el esquema lo impide: el test lo demuestra) |
| `paths.stale` | warning | Rutas que ya no existen (muestra acotada) |
| `extraction` | warning | Documentos sin texto extraído (binario, nube o fallo) |
| `extraction.hash` | warning | Texto sin `content_hash` |
| `intelligence` | warning / ok | Derivado de versión antigua o no construido |
| `worker.lock` | warning | Bloqueo de un proceso que ya no existe |
| `worker.state` | warning | Último pase del worker fallido |

**Bugs reales que los tests encontraron durante la fase** (no Theoretical,
no "debería"):

1. `rebuild_all` fallaba con `PermissionError` en Windows: `with
   connection:` en sqlite3 hace *commit*, **no** cierra, y el fichero
   quedaba bloqueado. Ahora el cierre es explícito y determinista
   (`contextlib.closing`) en todo el paquete de diagnóstico.
2. `check()` sobre una base inexistente la **creaba** como efecto
   secundario, porque `sqlite3.connect()` crea el fichero. Un diagnóstico
   no puede escribir sobre el índice que inspecciona: ahora se comprueba
   la existencia antes de conectar.
3. Si la aplicación está abierta, el fichero sigue bloqueado. Se reintenta
   cinco veces y, si persiste, se informa: `RepairBlocked` →
   *"index.db está en uso — cierra la ventana y detiene el indexador"*, en
   lugar de un `WinError` opaco.

## Reparaciones: la línea entre seguro y destructivo

| Operación | Nivel | Confirmación |
|---|---|---|
| `reconcile <root>` | Seguro, idempotente | ninguna |
| `rebuild intelligence` | Seguro (aditivo y versionado) | ninguna |
| `rebuild fts` | **Destructivo** | `confirm=True` en la API, `--yes` en CLI |
| `rebuild extract <path>` | **Destructivo** | ídem |
| `rebuild all --root …` | **Destructivo** (borra la BD) | ídem |

La confirmación está **en el código**, no en la interfaz: las tres
funciones lanzan `ConfirmationRequired` sin `confirm=True`, y hay un test
que lo comprueba para las tres y verifica que el índice no cambió.

Comportamiento real medido:

- `reconcile` dos veces → `changed=0`, `skipped=3`.
- `rebuild fts` con una fila huérfana y un documento sin fila →
  `2 changed` y las dos comprobaciones vuelven a `ok`.
- `rebuild all` → borra 3 documentos, sus filas FTS y las derivadas, y
  reindexa desde cero; la búsqueda sigue funcionando.
- CLI sin `--yes` → mensaje de rechazo y código 1, **índice intacto**.

## Privacidad de los informes

Ningún informe lee contenido de documentos: solo recuentos, tamaños,
rutas (metadatos) y estados. Los mensajes nombran como máximo tres rutas
de ejemplo, porque un diagnóstico que imprime 400 000 rutas es un ataque
de denegación de servicio contra quien lo lee. La prueba de fugas de log
usa un secreto embebido en los bytes de un PDF corrupto y comprueba que
no aparece en el fichero de log.

## Criterios de aceptación (spec 015)

| Requisito | Estado |
|---|---|
| El usuario puede determinar la salud del índice | ✅ `diagnose health` + ventana de diagnóstico |
| Reparación/reconstrucción segura y probada | ✅ dos niveles, confirmación en código, tests de ambas rutas |
| Reconstrucción completa posible | ✅ `diagnose repair all` (borra y reindexa) |
| Los diagnósticos no filtran contenido | ✅ informes sin contenido + log acotado + test con secreto |
| Estados degradados con tests | ✅ huérfanos, sin texto, rutas obsoletas, bloqueo, esquema, BD corrupta |
| Diagnóstico en CLI y GUI | ✅ ambos, con el servicio testeable sin Tk |
| Semántica de búsqueda preservada | ✅ sin cambios en indexación ni búsqueda (401 → 424 tests, todos verdes) |

## Limitaciones (deliberadas)

- **`paths.stale` sondea una muestra (200 rutas), no el índice entero**:
  recorrer 100 000 rutas es un trabajo de reconciliación, no de
  diagnóstico. La muestra detecta unidades desmontadas o movidas, que es
  el fallo real; `universal-search index <root>` hace el trabajo completo.
- **`rebuild fts` no re-extrae documentos de nube**: se los salta y los
  cuenta, en vez de fingir. La salud seguirá diciendo que esos documentos no
  tienen texto hasta que el worker los descargue.
- **`rebuild all` necesita las rutas**: si el índice se borró y el usuario
  no recuerda qué tenía, no hay forma de reconstruirlo por arte de magia.
  El servicio GUI usa las rutas de la configuración.
- **La GUI no repara fila por fila**: la ventana muestra el diagnóstico y
  ofrece la reconstrucción completa; el resto de reparaciones vive en el
  CLI, donde el flags `--yes` es explícito y auditable.
- Sin notificaciones asíncronas: la ventana no refresca sola el
  diagnóstico; se abre bajo demanda (una consulta de salud es una lectura
  de la BD).

## Cómo ejecutar

```bash
universal-search diagnose summary
universal-search diagnose health                     # código 0/1/2
universal-search diagnose repair reconcile <root>
universal-search diagnose repair fts --yes
universal-search diagnose repair extract informe.pdf --yes
universal-search diagnose repair intelligence
universal-search diagnose repair all --root C:\Users\me\Docs --yes

.venv\Scripts\python -m pytest tests\test_diagnostics.py
```
