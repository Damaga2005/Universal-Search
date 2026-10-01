# Fase 037 — Distribución: modo portable y un ejecutable único

## La regla que lo gobierna

**Un artefacto que no se ha ejecutado no se publica.** La auditoría de 2.0.0
terminó con el hallazgo más incómodo del ciclo: la release adjuntó los dos
`.exe` sueltos y, al descargarlos y ejecutarlos, **no arrancaban**
(`PYI-8: Failed to load Python DLL`). El build es *one-dir*: los ejecutables
necesitan la carpeta `_internal/` hermana, 27 MB y 956 ficheros. Lo publicado
no era utilizable, y se detectó descargando el artefacto, que es exactamente
para lo que existe una auditoría.

La causa no era técnica, era de proceso: la construcción era una **secuencia de
pasos que alguien tenía que recordar en el orden correcto**, y nadie comprobaba
el resultado. Esta fase atacó la causa:

1. `packaging/build.ps1` construye **y arranca lo que ha construido**. Para el
   ejecutable de un solo fichero lo copia a una carpeta vacía y lo usa allí: esa
   es exactamente la reproducción del defecto de 2.0.0, convertida en puerta.
2. El single-file es un artefacto de primera clase, no un accidento del
   one-dir.
3. El modo portable hace que la copia sea **portátil de verdad**: todos sus
   datos en su propia carpeta, y nada escrito bajo `%LOCALAPPDATA%`.

## Puerta de evidencia

`python -m evaluation.distribution_gate`.

| Puerta | Umbral | Medido | Veredicto |
|---|---|---|---|
| T1 el estado se queda en la carpeta portable | 0 fugas | **0** | PASS |
| T2 la búsqueda funciona en portable | sí | **sí** | PASS |
| T3 `forget` funciona en portable | sí | **sí** | PASS |
| T4 ficheros escritos bajo `LOCALAPPDATA` | 0 | **0** | PASS |
| T5 los documentos del usuario intactos | sí | **sí** | PASS |
| T6 un home explícito manda | sí | **sí** | PASS |
| T7 la copia instalada no cambia | sí | **sí** | PASS |
| T8 caídas silenciosas | 0 | **0** | PASS |
| T9 una versión en los dos `.spec` | sí | **sí** | PASS |
| T10 el spec one-file es un fichero | sí | **sí** | PASS |
| T11 el one-file responde a `--version` solo | sí | **sí** | PASS |

**VEREDICTO: SHIP (11/11)**. Registro en
`evaluation/distribution_baseline.json`.

### Suite completa

| | Con la 037 | Sin la 037 (base) |
|---|---|---|
| Recogidos | 1131 | 1100 |
| Fallos | **1** (1127 pasados) | 1 + 3 |

El único fallo es `test_gui_ux.py::test_keyboard_navigation_moves_and_opens`
(`pump(timeout=5)` con el Tk intermitente que `docs/RELEASE.md` ya describe), y
**falla igual sin este código**: se comprobó guardando la fase entera en un
stash y ejecutando la suite completa. Pasa aislado (18/18). Los tres fallos de
`test_v2_gate` de la columna base son un artefacto del stash —los documentos
decían 1131 y los tests eran los de antes— y no una regresión.

La primera ejecución completa dio **cuatro** fallos, tres de ellos en
`test_release`, `test_gui_services` y `test_background`, con el mensaje «el
índice apareció junto al repositorio». Esa era la fuga de `os.environ` de los
tests nuevos, descrita más abajo; corregida, no vuelve a aparecer.

## Una medición que cambió de veredicto con el mismo código

La puerta de la 031 (T5, p95 añadido ≤ 8 ms) falló con **18,70 ms** al
ejecutarla mientras la suite completa ocupaba la CPU al **94 %**. Repetida con
el equipo descargado: **7,50 ms**, dentro del umbral, y la puerta da **6/6
SHIP**. El baseline de la 031 se dejó sin tocar.

No es una anecdote: es la razón de que la fase 038 sea la siguiente. Un número
de latencia medido con la máquina ocupada no mide la latencia del producto,
mide la de quien lo está midiendo, y publicarlo como veredicto es peor que no
publicarlo. La puerta de 037 registra la carga de CPU junto a cada cifra por
esa razón.

## El fallo de la 031 que encontró esta fase

T2 falló en la primera ejecución y **tenía razón**. Al investigar: el índice
difuso nunca se marcaba sucio al indexar. Solo lo hacía la capa semántica
(`indexer.py::_mark_semantic_dirty`), así que en un índice recién construido la
tabla de huellas estaba **vacía y no marcada**, `ensure_fresh()` no la
reconstruía, y `transisto` no encontraba nada hasta que alguien ejecutaba el
rebuild a mano.

Es decir: la 031 entregaba la Tolerance a erratas pero **no la conectaba al
camino normal de indexado**. Los tests propios de la 031 no lo detectaban
porque llaman a `rebuild()` explícitamente — un test que prepara el escenario
antes de medir no puede encontrar un escenario que nadie preparó.

La corrección (`indexer.py::_mark_fuzzy_dirty`) es una línea junto a la que ya
existía para la capa semántica, con el mismo contrato: marcar, nunca hacer
fallar la pasada de indexado. Y la puerta T2 ahora usa la cadena de motores
completa, la que monta el CLI, precisamente para que una prueba de
almacenamiento no pueda volver a pasar por alto la recuperación.

## Dos correcciones a la puerta, y las dos importan

**La primera versión de T1 afirmaba algo falso.** Medía "el estado se queda en
la carpeta portable" comparando contra `marker`, pero `enable()` escribía en
`exe_dir` mientras `status()` miraba junto al ejecutable, que sin congelar es
el directorio de trabajo. La puerta fallaba por un motivo equivocado y habría
podido "arreglarse" añadiendo un interruptor de producción que sólo existe
para que un test sea cómodo.

Lo correcto es que **la puerta se sitúe en la carpeta que dice simular**: es
exactamente la situación de quien lleva la aplicación en un pendrive, y no
introduce ninguna superficie nueva en el producto.

**T11 no puede pasar por defecto.** Si el build no se ha ejecutado, la puerta
informa `no compilado` y **no lo cuenta como aprobado**. Una puerta que se da
por buena sin medición es la forma más rápida de dejar de ser una puerta.

## Pruebas

`tests/test_distribution.py`, **31 tests**. La precedencia de las tres reglas
(`UNIVERSAL_SEARCH_HOME` > portable > instalado) y el motivo de cada orden;
los valores `0`/`false`/`no` de la variable de entorno **no** activan el modo
portable; el marcador escrito se lee como texto y explica qué hace y cómo se
deshace; indexar y buscar escriben dentro de la carpeta portable; **cero**
ficheros bajo `LOCALAPPDATA%`; la carpeta de datos separada de la del programa;
un `forget` sigue sin borrar ficheros; una carpeta inutilizable da error en
lugar de reubicar; los cuatro caminos del CLI (`on`, `off`, `status`, y el
fallo de `on` con código 1); **cambiar de modo no mueve el índice**; los dos
`.spec` con la misma versión y el mismo icono; el spec one-file sin `COLLECT`
y con los imports perezosos del worker; y que README y CI construyan y prueben
el ejecutable único.

`tests/test_observability.py` suma **3 tests** para la nueva comprobación
`deployment` del self-test.

## Una fuga de la puerta que ensució la suite

La primera versión de la puerta **dejó `LOCALAPPDATA` apuntando a su directorio
temporal y se quedó dentro de la carpeta que simulaba el pendrive**. No se
notaba al mirarla: la puerta pasaba. Se notó en la suite completa, donde tres
tests de **otros módulos** fallaron con un índice que había aparecido junto al
repositorio (`test_release::test_user_data_lives_outside_the_installation` y
`test_gui_services::test_default_home_uses_localappdata`).

Es el mismo fallo que la fase existe para evitar, apuntado a la suite en vez
de a un usuario: un cambio de directorio y de variable de entorno que nadie ve.
Se corrigió con un gestor de contexto (`_as_a_copy_on_a_stick`) que restaura
ambos al salir, **también cuando la medición lanza una excepción**, y con dos
tests que lo fijan.

También se corrigió la causa paralela en los tests: tres de ellos escribían
`os.environ[...] = "1"` en vez de usar `monkeypatch`, así que la variable
sobrevivía al test. El fixture `clean_environment` es ahora `autouse` y todo
test pasa por `monkeypatch`.

## Integración

- `src/universal_search/portable.py`: la decisión de dónde viven los datos, con
  `status()`, `enable()`, `disable()`, `describe()` y `ensure_writable()`.
- `AppPaths` gana `portable: bool`, y `ensure()` valida la escritura cuando
  está activo. Como **todos** los escritores pasan por ahí, el modo portable
  tiene un único sitio donde negarse a funcionar.
- `default_home()` delega; `per_user_home()` queda separado para que los
  diagnósticos puedan nombrar la ubicación instalada sin pasar por la decisión.
- CLI: `portable status|on|off`, con `--base` y `--data`.
- `privacy show` declara el modo de despliegue. Quién pregunta «qué guardas y
  dónde» está preguntando también esto.
- `diagnose self-test` gana un área `deployment`: es la primera pregunta cuando
  un índice «desaparece», y está en la línea de comando en ese momento.

## Limitaciones

- **El modo portable no mueve un índice existente.** Escribe el marcador y dice
  dónde mirar. Reubicar el índice es una decisión sobre horas de trabajo:
  hacerlo en silencio lo duplicaría o lo perdería.
- **El modo portable no se propaga a otros usuarios de la misma máquina.** Si
  dos cuentas comparten la carpeta del pendrive, escriben en el mismo sitio.
- **El one-file es más lento de arrancar**: desempaqueta unos 30 MB en `%TEMP%`
  en cada ejecución, incluidos los comandos del CLI. Por eso es la opción
  *además* del one-dir, no la sustituta.
- **El one-file es un build de consola.** Lo que se ganó: `search` imprime de
  verdad. Lo que se paga: al abrirlo con doble clic queda una consola detrás.
- **La carpeta `UniversalSearch-data` no es un sitio seguro.** Escritura local
  en la misma carpeta que el programa; cualquiera con acceso al disco puede
  modificarla. Es la contrapartida de la portabilidad, no un descuido.
- **El `install.ps1` y `uninstall.ps1` no saben de modo portable.** La
  instalación con `packaging/build.ps1` deja el ejecutable donde esté;
  el desinstalador de la ruta instalada no toca la carpeta portable, porque no
  la conoce.
- **Inno Setup sigue sin compilarse en CI.** Está versionado y sincronizado con
  la versión, y `installer.iss` lo declara explícitamente; entrar en CI exigiría
  añadir una dependencia de compilación que el proyecto no tiene.