# Fase 020 — Production Release & Reliability

## Qué se entregó

1. **Rechazo de downgrade** (`UnsupportedSchemaVersion`): una base con una
   versión de esquema *mayor* que la del build no se abre, en vez de
   reescribir el sello en silencio. Un downgrade silencioso haría que dos
   builds creyeran ser dueños del esquema.
2. **Historial de migraciones** (`schema_migrations`, versión 5): una fila
   por versión realmente aplicada, solo a partir de esta fase — no se
   inventan filas para versiones que este build no aplicó. Visible en
   `diagnose summary`.
3. **Copias de seguridad consistentes**: `SearchDatabase.backup()` usa la
   API de backup de SQLite (correcta aunque el worker escriba) y
   `diagnose repair all --backup` la toma antes de borrar.
4. **Un bug real de worker** encontrado por los tests de recuperación:
   `background.start(paths)` solo pasaba el directorio de trabajo al
   proceso hijo, de modo que un worker lanzado con rutas propias
   publicaba su bloqueo y su estado en el **home real del usuario**. El
   hijo hereda ahora `UNIVERSAL_SEARCH_HOME`.
5. **Espera de arranque** de 5 s a 10 s, con el motivo escrito: en una
   máquina cargada un worker correcto se notificaba como fallido.
6. **CI** (`.github/workflows/ci.yml`): puerta de calidad en Windows
   (pyflakes, suite, migraciones, fiabilidad, calidad de búsqueda,
   seguridad), build con PyInstaller + prueba de humo + hashes, y un
   **sondeo no bloqueante** en Ubuntu para el núcleo independiente de la
   plataforma.
7. **`docs/RELEASE.md`**: procedimiento reproducible, estrategia de
   actualización, lista de release y la puerta de calidad con los
   resultados de verdad.
8. **`CHANGELOG.md`** con las fases 011–020.
9. **31 tests nuevos** (`test_reliability.py`) y `tests/conftest.py` con
   una sonda de disponibilidad de Tk.
10. Suite completa: **515 en verde, 1 omitido**.

## Puerta de calidad final (números reales)

Windows 11, Python 3.14.6, 2026-09-23.

| Comprobación | Resultado |
|---|---|
| Suite completa | 515 passed, 1 skipped (95 s) |
| pyflakes (`src tests benchmarks evaluation`) | limpio |
| Benchmarks, 1000 docs | indexado 5.69 s · media 14.2 ms · p95 20.3 ms · índice 3.3 MiB |
| Evaluación etiquetada | P@1 1.000 · R@3 0.923 · MRR 1.000 |
| Build Windows | 2 ejecutables (CLI 3 707 254 B, GUI 3 702 134 B) |
| Humo del empaquetado | 12 pasos, todos correctos |

La prueba de humo no importa nada de la aplicación: ejecuta los
ejecutables congelados como haría una persona —version, index, search,
lenguaje de consulta, consulta malformada (código 1), diagnose summary y
health, intelligence show/related, privacy show, extensions, y
backup + reconstrucción total + búsqueda posterior.

## Qué se comprobó de fiabilidad

| Escenario | Comprobación |
|---|---|
| Base de una versión posterior | Rechazada, con mensaje accionable, sin reescribir el sello (API, diagnóstico y CLI) |
| Actualización desde el esquema de 1.0.0 | Documentos y texto sobreviven; se aplican tablas nuevas |
| Historial | Una fila por versión, sin duplicados al reconectar |
| Copia de seguridad | Copia utilizable y restaurable; no sobrescribe |
| Reconstrucción con copia | La copia conserva el índice anterior |
| Pasada interrumpida | Lo ya confirmado es buscable; la siguiente pasada reconcilia sin duplicados |
| Worker terminado a la fuerza | El bloqueo obsoleto no impide arrancar otro worker |
| Worker con rutas propias | Publica bloqueo y estado en el home pedido, no en el del usuario |
| Cierre limpio | `stop` libera bloqueo y estado (suite de background) |

## Una regresión que introduje y detecté

Al añadir la comprobación de versión al CLI, `search` abría la base de
datos **antes** de validar la consulta, lo que rompía una garantía de la
fase 012: una consulta malformada no debe crear ficheros. El test
`test_invalid_query_cli_prints_error_not_traceback` lo detectó; la
comprobación ahora solo se hace si el fichero ya existe.

## Problemas del entorno que la fase destapó

1. **El lanzador del venv no conserva el PID** en esta instalación de
   Python 3.14: `Popen.pid` ≠ `os.getpid()` dentro del hijo. El primer
   test de recuperación lo asumía mal; ahora mata al **titular del
   bloqueo**, que es la propiedad real. Ningún cambio de producto: el
   código ya leía el PID del bloqueo, no el del proceso lanzado.
2. **El runtime de Tcl/Tk puede no leer su propia biblioteca** con la
   máquina saturada, y los tests de GUI dan error antes de ejecutar nada
   nuestro. `conftest.py` lo detecta una vez por sesión y **omite** esos
   tests con el motivo, en vez de fingir que la ventana está probada.
3. `os.kill(pid, SIGTERM)` no existe en Windows (`WinError 87`): el test
   usa `taskkill`, que es la herramienta de la plataforma.

Ninguno de los tres era un defecto de la aplicación; los tres habrían
hecho que la puerta de calidad fallara por motivos que no son del código,
que es peor que no tener puerta.

## Limitaciones (deliberadas)

- **Sin autoactualizador**: ejecutar código descargado exigiría verificar
  autenticidad e integridad, y no hay cadena de firma. Se documenta la
  actualización manual y se publican hashes.
- **Sin firma digital** en los ejecutables.
- **Sin verificación de downgrade *entre* bases** (solo del esquema): una
  base dañada se diagnostica, no se repara sola.
- **El sondeo de Linux no bloquea**: el núcleo es independiente, pero
  GUI, registro, atajo e instalador son de Windows por diseño.
- **La lista de release es un procedimiento humano**: la parte
  automatizable (tests, lint, build, humo, hashes) está en CI; instalar en
  una máquina limpia y pulsar botones sigue siendo de una persona.
- **No se probó una instalación real en otra máquina** desde esta sesión:
  el smoke test usa los ejecutables en esta máquina, y el instalador está
  cubierto por sus tests, no ejecutado aquí.

## Cómo reproducir

```bash
python -m pytest tests -q
python -m pyflakes src tests benchmarks evaluation
python -m benchmarks --profile 1000
python -m evaluation
python -m PyInstaller packaging/universal-search.spec --noconfirm
# lista completa en docs/RELEASE.md
```
