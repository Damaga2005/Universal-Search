# Fase 021 — Tray & Background Experience

## Qué se entregó

1. **Un controlador de bandeja opcional** (`universal-search tray`) que
   mantiene un proceso de usuario en el área de notificación de Windows sin
   convertir el indexador en un servicio.
2. **Un servicio de estado de aplicación** (`background_service.py`) que
   convierte el bloqueo, el PID, el estado atómico y los marcadores del worker
   en una instantánea coherente. No consulta SQLite ni inventa una cola.
3. **Un backend Win32 nativo** (`platforms/tray.py`) construido con `ctypes` y
   las API `Shell_NotifyIcon`, `RegisterClassW` y `CreateWindowExW`. No se
   añadió ninguna dependencia de runtime.
4. **Un controlador independiente de plataforma**
   (`tray.py`): construye el menú, despacha comandos, decide notificaciones,
   conserva la propiedad del worker que inició y coordina un cierre seguro.
5. **Señalización hacia la ventana existente**: Abrir, Búsqueda rápida,
   Configuración y Diagnóstico reutilizan `gui.pid`, `gui-show.flag` y
   `gui-diagnostics.flag`; no se creó una segunda UI de ajustes o diagnóstico.
6. **Propiedad por generación y proceso**: la bandeja genera un token que pasa
   al hijo y solo lo reclama cuando worker, bloqueo y estado presentan exactamente
   esa generación. Los leases del SO serializan locks y reclamaciones; las
   paradas modernas usan un archivo por generación y la terminación forzada
   exige identidad de creación más una comprobación inmediata del propietario.
7. **Salida limpia e idempotente**: Salir quita el icono, libera el bloqueo de
   la bandeja y detiene solo el worker que esa instancia inició. Un worker
   iniciado por autostart, GUI, CLI u otra bandeja permanece activo.
8. **Autostart sin cambios**: `indexer autostart on` sigue registrando el
   worker `indexer run`; no inicia automáticamente la bandeja.

## Archivos reales de la fase

### Diseño, runtime y empaquetado

- `docs/superpowers/specs/2026-09-23-tray-background-experience-design.md`
- `packaging/entry-gui.py`
- `src/universal_search/appconfig.py`
- `src/universal_search/background.py`
- `src/universal_search/background_service.py` (nuevo)
- `src/universal_search/cli.py`
- `src/universal_search/gui/app.py`
- `src/universal_search/gui/services.py`
- `src/universal_search/hotkey.py`
- `src/universal_search/platforms/tray.py` (nuevo)
- `src/universal_search/platforms/worker.py` (nuevo; lease y process handle)
- `src/universal_search/tray.py` (nuevo)

### Tests

- `tests/test_background.py`
- `tests/test_background_service.py` (nuevo)
- `tests/test_cli.py`
- `tests/test_gui.py`
- `tests/test_hotkey.py`
- `tests/test_packaging_entry.py` (nuevo)
- `tests/test_tray.py` (nuevo)
- `tests/test_windows_tray.py` (nuevo)
- `tests/test_worker_ownership.py` (nuevo; concurrencia yleases)

### Documentación sincronizada en la tarea 6

- `docs/development/021-tray-and-background-experience-report.md` (este informe)
- `docs/README.md`
- `docs/ROADMAP.md`
- `docs/ARCHITECTURE.md`
- `docs/PRIVACY.md`
- `README.md`
- `CHANGELOG.md`

`docs/RELEASE.md` se revisó, pero no se modificó: no contiene un inventario de
comandos CLI al que haya que añadir `tray`; su procedimiento de build y sus
resultados de release siguen siendo válidos.

## Arquitectura

```text
Eventos/menú Win32
        │
        ▼
WindowsTray (ctypes, adaptador de plataforma)
        │ comandos enteros + ticks
        ▼
TrayController (menú, política, propiedad, salida)
        │
        ▼
BackgroundService (estado de aplicación)
        │
        ▼
background.py + ficheros de coordinación + worker
```

- `WindowsTray` y `NullTray` son adaptadores intercambiables. En Windows se
  selecciona `WindowsTray`; en otras plataformas `NullTray` informa que no hay
  área de notificación. Los mangos DLL son inyectables, de modo que el
  protocolo se prueba con fakes deterministas.
- `TrayController` no conoce SQLite ni detalles de Win32. Construye el menú,
  ejecuta acciones de aplicación y contiene los errores para que el bucle
  nativo no reciba un traceback.
- `BackgroundService` es la frontera de lectura. Consolida el PID/lease,
  `indexer.lock.owner`, `indexer-status.json` y las banderas de pausa/parada;
  el registro SQLite sigue perteneciendo al worker y a los diagnósticos.
- La GUI sigue siendo dueña de los ajustes y del diagnóstico. Las acciones del
  menú presentan esa ventana o tocan una solicitud que esta consume.
- El empaquetado usa el mismo entrypoint para GUI y CLI. Con argumentos ejecuta
  la CLI, por lo que el comando congelado `universal-search tray` está
  disponible sin otra dependencia ni otro ejecutable.

## Estado de la aplicación

| Evidencia coordinada | Estado derivado | Controles del menú | Aviso permitido |
|---|---|---|---|
| No hay worker vivo; el bloqueo puede estar obsoleto | `stopped` | Iniciar, salir | Ninguno por la transición solicitada |
| Worker vivo y marcador de parada | `stopping` | Los controles de ciclo de vida se desactivan | Ninguno |
| Worker vivo y marcador de pausa | `paused` | Reanudar, detener, salir | Ninguno por la transición solicitada |
| Worker vivo, estado `indexing`, PID y generación coincidentes | `indexing` | Pausar, detener, salir | Desaparición inesperada |
| Worker vivo, estado `idle`, PID y generación coincidentes | `idle` | Pausar, detener, salir | Final de una pasada de al menos 120 s |
| Worker vivo, estado `error`, PID y generación coincidentes | `error` | Detener, salir | Error nuevo o texto de error nuevo |
| Worker vivo sin estado utilizable | `starting` | Pausar, detener, salir | Desaparición inesperada |

Un JSON ilegible, una identidad de generación inválida o un archivo inaccesible
no hace fallar el sondeo: se informa como `problem`. Un PID muerto se considera
`stopped`; el bloqueo obsoleto se conserva hasta que el siguiente arranque pueda
sustituirlo bajo el lease. Solo `last_scan_at` y `last_scan_stats` se conservan
como histórico explícito. Error, hotkey, raíces, `updated_at`, estado y la
generación actual se ocultan si no coinciden con el lock vivo. El worker
reconcilia un sistema de ficheros, así que no existe una profundidad de cola
fiable que mostrar.

## Comandos y pertenencia

### Línea de comandos

```text
universal-search tray
universal-search indexer start
universal-search indexer stop
universal-search indexer status
universal-search indexer pause
universal-search indexer resume
universal-search indexer autostart on|off|status
```

`universal-search tray` es el único comando nuevo. Es opcional y bloquea
mientras su proceso está en la bandeja. `--help` solo muestra argparse y no
publica un icono. En una plataforma sin área de notificación devuelve un
código no cero; una bandeja ya activa también rechaza la segunda instancia.

El autostart conserva exactamente el sufijo `indexer run`:

- fuente: `"<python>" -m universal_search.cli indexer run`;
- congelado: `"<UniversalSearch\\universal-search.exe>" indexer run`.

No se cambió el valor de registro y no se añadió un autostart para `tray`.

### Menú de la bandeja

1. Resumen de estado (desactivado).
2. Abrir Universal Search.
3. Búsqueda rápida.
4. Pausar o Reanudar indexación, según el estado.
5. Iniciar o Detener indexador, según el estado.
6. Diagnóstico.
7. Configuración.
8. Salir.

Abrir, Búsqueda rápida y Configuración presentan la ventana existente.
Diagnóstico consume la solicitud de diagnóstico de esa misma ventana. La
bandeja no implementa paneles propios de ajustes, reparaciones o búsqueda.

## Política de notificaciones

La función de transición es independiente del backend y solo permite una
notificación desplegable en estos casos:

- primera entrada en `error` o cambio del texto mientras ya estaba en error;
- aparición de un problema nuevo del atajo global;
- desaparición no solicitada desde `indexing` o `starting`, con un texto
  distinto cuando queda un bloqueo obsoleto;
- paso de `indexing` a `idle` dentro de la misma generación cuando los
  timestamps demuestran al menos 120 segundos de trabajo.

Iniciar, pausar, reanudar y detener por petición del usuario no generan
avisos. Tampoco los cambios sin novedad ni los errores repetidos. La
notificación se publica con `Shell_NotifyIcon`; no es un toast WinRT y su
visibilidad depende de los ajustes de notificaciones de Windows. La primera
evaluación se retrasa hasta que el icono está listo, por lo que un error de
arranque no se pierde y no se duplica en el primer timer. El backend registra
`TaskbarCreated` y vuelve a ejecutar `NIM_ADD` tras el reinicio de Explorer.
Los fallos de comandos distintos de Exit quedan acotados a 300 caracteres, se
conservan como último resultado y se registran solo por tipo de excepción.
`KeyboardInterrupt` devuelve 130 y libera el lock en el camino habitual.

## Coordinación de procesos

Cada rol conserva su propia identidad y no puede reclamar el papel de otro:

| Rol | Archivo | Semántica |
|---|---|---|
| Worker | `indexer.lock` + `indexer.lock.lease` + `indexer.lock.owner` | PID conservado, lease del SO durante toda la vida y generación única |
| GUI | `gui.pid` | Registro singleton comprobado por actividad antes de abrir otra ventana |
| Bandeja | `tray.pid` + `tray.pid.lock` | PID para diagnóstico más bloqueo exclusivo del sistema operativo |

Son tres papeles PID independientes. `gui.pid` se comprueba por actividad y no
es un bloqueo de fichero del sistema operativo; el worker y la bandeja sí
tienen reclamaciones de bloqueo más fuertes. El starter presenta una generación
por entorno y la bandeja solo reclama si lock, status, PID y generación
coinciden. La reclamación de arranque se escribe atómicamente en una ruta que
incluye PID y hash de generación; solo se recupera cuando ese PID está
demonstrablemente muerto. Si falla la publicación o la limpieza, se reintenta
tres veces solo sobre los paths final/temporal de ese PID y generación. Si el
borrado sigue denegado, la operación falla cerrado con esos paths en el mensaje,
sin tocar otra reclamación. La parada moderna escribe únicamente
`indexer-stop.<hash>.flag`, que el worker de esa generación consume sin una
carrera de comprobar-y-borrar sobre un archivo compartido. El marcador genérico
se conserva solo para workers sin generación. Antes de forzar, Windows consulta
el FILETIME de creación desde el handle y compara PID, generación e identidad;
Linux usa pidfd cuando existe. El sidecar del propietario se vuelve a leer
justo antes de terminar. Si no puede probarse una identidad segura, se conserva
la parada cooperativa y no se fuerza. La salida nativa elimina el icono, el
timer, la ventana oculta y la clase Win32 en todos los caminos.

## Decisiones de alcance

- **Sin Windows Service**: un servicio exigiría otro ciclo de vida, permisos y
  recuperación. La aplicación conserva un proceso por usuario y el worker ya
  sobrevive al cierre de la ventana.
- **Sin cloud ni telemetría**: todo el estado y todas las acciones siguen siendo
  locales. No hay servidor, API ni coordinación multiusuario.
- **Sin dependencia de bandeja**: `ctypes` y los módulos estándar exponen la
  API nativa. Las únicas dependencias de runtime siguen siendo `pypdf` y
  `watchdog`.
- **Sin bandeja automática**: el tray es una UI opcional. El autostart
  existente sigue iniciando únicamente `indexer run`.
- **Sin UI duplicada**: configuración y diagnóstico continúan en la ventana
  existente.

## Limitaciones conocidas

1. **El smoke visible es opt-in y no pertenece a la suite normal.** En la
   verificación final se ejecutó
   `UNIVERSAL_SEARCH_TRAY_NATIVE_SMOKE=1 pytest ...::test_windows_tray_native_add_command_exit_and_delete_smoke`
   en Windows: publicó `NIM_ADD`, recibió el comando Exit por `PostMessageW` y
   ejecutó `NIM_DELETE` en el `finally` (1 passed). Sin la variable, el test se
   omite para no dejar un icono ni bloquear CI. El smoke congelado sigue siendo
   `tray --help` más el smoke existente del paquete.
2. `tray` está disponible solo donde `sys.platform == "win32"` y existe un área
   de notificación utilizable.
3. El resumen del menú es deliberadamente mínimo. La última pasada y el texto
   de trabajo pendiente permanecen en el snapshot de estado y los
   diagnósticos; no se muestran como una cola numérica inventada.
4. Las notificaciones desplegables dependen de la configuración del área de
   notificación de Windows y no sustituyen a una aplicación toast moderna.
5. La bandeja solo detiene un worker cuya generación exacta ella misma inició.
   Un worker PID-only legado se puede leer y controlar por CLI, pero nunca se
   reclama como propiedad de la bandeja.
6. En un runtime no Windows sin pidfd no existe una identidad de proceso
   enlazada al SO que permita forzar de forma segura. La parada cooperativa se
   conserva y la terminación forzada devuelve un fallo limitado en el tiempo;
   nunca se reintroduce un `kill` por PID.
7. Una reclamación legacy `indexer.starting` corrupta y sin PID no puede probar
   que su escritor esté muerto. El protocolo moderno no la genera y la
   reclamación con PID se recupera; el estado legacy se conserva y falla cerrado
   en vez de eliminar una reclamación posiblemente viva.
8. No se cambió la actualización manual, la ausencia de firma digital ni el
   alcance de las fases 022–030.

## Puerta de calidad medida

Ejecutado en Windows 11 build 26200, Python 3.14.6, el 2026-09-24.

| Comprobación | Comando | Resultado real |
|---|---|---|
| Suite completa | `.venv\Scripts\python -m pytest tests\ -q` | código 0 |
| Suite completa con recuento visible | `.venv\Scripts\python -m pytest tests\ -o addopts= -q` | **646 passed, 2 skipped in 48.99 s** |
| Suite focalizada de propiedad | `.venv\Scripts\python -m pytest tests\test_worker_ownership.py -q -o addopts=` | **29 passed in 2.57 s** |
| Análisis estático | `.venv\Scripts\python -m pyflakes src tests benchmarks evaluation` | código 0, sin salida |
| Build exacto | `.venv\Scripts\python -m PyInstaller packaging\universal-search.spec --noconfirm --clean` | **Build complete** con PyInstaller 6.22.3 |
| Humo congelado existente | `.venv\Scripts\python C:\Users\dmart\AppData\Local\Temp\opencode\smoke020.py dist\UniversalSearch` | 14 invocaciones con códigos esperados y `SMOKE TEST OK` |
| Ciclo worker congelado | `UNIVERSAL_SEARCH_HOME=< temporal> dist\UniversalSearch\universal-search.exe indexer start/status/stop` | códigos 0/0/0; PID 14628 observado, detenido y temporales eliminados |
| Ayuda del comando nuevo | `dist\UniversalSearch\universal-search.exe tray --help` | código 0; muestra `usage: universal-search tray [-h]` |
| Smoke nativo visible opt-in | `UNIVERSAL_SEARCH_TRAY_NATIVE_SMOKE=1 .venv\Scripts\python -m pytest tests\test_windows_tray.py::test_windows_tray_native_add_command_exit_and_delete_smoke -o addopts= -q` | **1 passed in 0.08 s**; NIM_ADD, comando Exit, NIM_DELETE |
| Texto de autostart | lectura de `background.autostart_command()` en modo fuente y con `sys.frozen=True` | ambos terminan en `indexer run`; no se escribió el registro |

Tamaños y SHA-256 medidos en esta build:

| Ejecutable | Bytes | SHA-256 |
|---|---:|---|
| `universal-search.exe` | 3 771 799 | `2f00a7297f32066ccb5efdf67c1b13fad407ae4d2aba9815ee27c1061eea90d6` |
| `UniversalSearch.exe` | 3 766 679 | `28b69dd79b5d176181f63e25eefc1b1c8d130ea8d1e7419d2a325dec9f0b0726` |

El smoke `smoke020.py` probó versión, indexado, búsqueda, lenguaje de
consulta, consulta malformada, diagnóstico, inteligencia, privacidad,
extensiones, backup/reconstrucción y búsqueda posterior. No ejecutó `tray` sin
argumentos; el icono real se comprobó por separado mediante el test nativo
opt-in descrito arriba.

## Criterios de aceptación

| Requisito | Estado |
|---|---|
| Bandeja nativa opcional | Implementada mediante `ctypes` |
| Estado stopped/starting/indexing/paused/idle/error/stopping | Derivado por `BackgroundService` |
| Iniciar, detener, pausar y reanudar | Implementados con propiedad por PID/generación |
| Abrir y Búsqueda rápida | Señalizan o abren la ventana existente |
| Ajustes y diagnóstico | Permanecen en la ventana existente |
| Una sola bandeja | PID más bloqueo exclusivo del sistema operativo |
| Salida sin residuos propios | Icono/timer/window/class y lock liberados; solo detiene al worker propio |
| Autostart compatible | Sigue siendo `indexer run` |
| Sin servicio, cloud o dependencia nueva | Decisión mantenida |
| Empaquetado | Compila y supera el humo existente |
| Icono real visible | Smoke opt-in ejecutado: NIM_ADD, comando Exit y NIM_DELETE |
