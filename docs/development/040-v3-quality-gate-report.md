# Fase 040 — Puerta de calidad v3

## Qué es esta fase

Las nueve fases anteriores сдела work que nadie iba a volver a mirar. Esta es
la fase que lo re-mide todo y **decide qué se publica**, y su valor está
justo en que no es una lista de comprobaciones: cada puerta tiene un comando,
un resultado y una evidencia, y las que no se pudieron ejecutar se dicen.

El gate de la 030 tenía trece invariantes. Este tiene **veintitrés**: los
mismos trece, más **uno por cada promesa que el programa 031–040 hizo**. Un
gate que deja de comprobar en el límite viejo dejaría de vigilar las nueve
fases que añadió, que es exactamente cómo se pudren las puertas.

Y lo mismo pasa con el rango de fases: `PHASES` pasa de `range(1, 31)` a
`range(1, 41)`. Un gate que documenta las fases 001–030 y se declara cerrado
está vigilando un programa que terminó hace nueve fases.

## La puerta

`python -m evaluation.gate` · salida 0/1

### Los trece invariantes de la 030

| # | Invariante | Resultado |
|---|---|---|
| 1 | Presupuesto de dependencias | PASS — runtime `['pypdf','watchdog']` |
| 2 | Sin imports de red ni de modelo | PASS |
| 3 | El núcleo es independiente de la plataforma | PASS — 52 módulos |
| 4 | Puntos de contacto Win32 declarados | PASS — 1, con motivo |
| 5 | Costura de plataforma | PASS |
| 6 | Inventario de privacidad completo | PASS — 15 tablas |
| 7 | Las reparaciones no tocan ficheros del usuario | PASS — 2 ficheros idénticos |
| 8 | Sin depuración abandonada | PASS |
| 9 | Versión con fuente única | PASS — 2.0.0 / esquema 10 |
| 10 | Cada fase documentada | PASS — 40 fases |
| 11 | CHANGELOG cubre cada fase | PASS — 40 fases |
| 12 | Recuento documentado = recuento real | PASS — 1216 recogidos |
| 13 | Hoja de ruta cerrada | PASS — 40 hechas, 0 abiertas |

### Los diez invariantes que las nueve fases añadieron

| # | Invariante | Qué promete | Resultado |
|---|---|---|---|
| 14 | Las capas opcionales se pueden quitar | «Opcional» significa *quitable*, no sólo presente: tras `remove_all()` la búsqueda exacta sigue funcionando y quedan **0** filas | PASS |
| 15 | Los archivos hostiles están acotados | 034: presupuesto por entrada y rechazo de rutas absolutas o con `..` | PASS |
| 16 | Las acciones por lote no salen de los resultados | 035: abre exactamente las rutas que recibe, y con tope de 50 | PASS |
| 17 | El modo portable no escribe en el directorio del usuario | 037: todo dentro de `UniversalSearch-data`, **0** ficheros bajo `LOCALAPPDATA` | PASS |
| 18 | El modo portable nunca cae en silencio | 037: una carpeta inservible da error, no cambia de sitio | PASS |
| 19 | Los dos builds fijan una versión | 037: 2.0.0 en los tres sitios, icono en ambos, y el spec one-file **no** hace `COLLECT` | PASS |
| 20 | La puerta de rendimiento puede no concluir | 038: tres códigos de salida distintos y una máquina ocupada **veta** | PASS |
| 21 | Toda cadena visible está catalogada | 039: 86 entradas, 0 literales sueltos | PASS |
| 22 | El contraste cumple WCAG AA | 039: 7 pares × 2 temas | PASS |
| 23 | Las búsquedas guardadas no guardan datos del usuario | 036: sólo consulta y presentación | PASS |

## Mediciones

Windows 11 Pro build 26200 · AMD Ryzen 5 2600 (6/12 hilos) · 16 GB ·
Python 3.14.6 · `.venv` del proyecto · 2026-10-01.

| Comprobación | Comando | Resultado |
|---|---|---|
| Suite completa | `python -m pytest tests -q` | **1210 passed, 6 skipped** (1216 recogidos) |
| Análisis estático | `python -m pyflakes src tests benchmarks evaluation` | sin salida, exit 0 |
| Puerta de calidad v3 | `python -m evaluation.gate` | **PASS (23/23)** |
| Calidad de búsqueda | `python -m evaluation` | **MRR 0,833**, P@1 0,833 — sin cambios respecto a la línea base |
| Puerta 031 (búsqueda robusta) | `python -m evaluation.fuzzy_gate` | SHIP 6/6 |
| Puerta 032 (sugerencias) | `python -m evaluation.suggest_gate` | SHIP |
| Puerta 033 (correo) | `python -m evaluation.mail_gate` | SHIP |
| Puerta 034 (comprimidos) | `python -m evaluation.archive_gate` | SHIP |
| Puerta 035 (lotes) | `python -m evaluation.batch_gate` | SHIP |
| Puerta 036 (organizar) | `python -m evaluation.organize_gate` | SHIP 9/9 |
| Puerta 037 (distribución) | `python -m evaluation.distribution_gate` | SHIP 11/11 |
| Puerta 039 (accesibilidad) | `python -m evaluation.accessibility_gate` | SHIP 6/6 |
| Puerta 038 (rendimiento) | `python -m evaluation.perf_gate` | ver más abajo |

### La puerta de rendimiento, y por qué dice «inconcluyente» en este informe

La primera vez que se ejecutó dentro de esta fase, con la suite completa
corriendo en segundo plano, la puerta respondió:

```
carga:  CPU del sistema al 65% antes de medir (> 60%)
VEREDICTO: INCONCLUYENTE          (salida 2)
```

**Cero código de medición se ejecutó.** Eso no es un fallo de la puerta: es la
puerta haciendo su trabajo, y la razón por la que este informe no publica una
cifra de latencia tomada con la máquina ocupada. La medición con la máquina
libre —CPU al 5 %, calibración 1,00× de su referencia— sí se hizo y está en el
informe de la 038: nueve métricas dentro de tolerancia, dispersión entre
pasadas menor que una regresión, **PASS con salida 0**.

Un informe que mezclara las dos cifras como si fueran comparables sería
precisamente el documento que la 038 existe para impedir.

### Sobre los omitidos

Son de dos tipos, y conviene no mezclarlos:

- **Los tres del sistema de archivos** que el sistema operativo de esta máquina
  no permite crear (symlinks). No son tests nuevos ni tests rotos; se omiten
  condicionalmente y el motivo queda registrado. Son los mismos tres desde la
  fase 020.
- **Los tres de la ventana de Tk** de `test_accessibility.py`, omitidos con el
  motivo cuando el runtime no arranca en ese instante.

Ninguno se cuenta como pasado. Los omitidos son 6, y el recuento que la
documentación declara son los **1216 recogidos**, precisamente porque esa es la
cifra que `pytest --collect-only` puede saber sin ejecutar nada.

### Sobre la intermitencia de Tk

Una ejecución completa dio **1210 passed, 6 skipped, 0 failed**. Otras dos
ejecuciones anteriores del mismo árbol dieron dos fallos en `test_gui_ux`: el
runtime de Tcl/Tk a veces no puede leer su propia biblioteca durante una
fracción de segundo (`Can't find a usable init.tcl`) cuando la suite satura la
máquina. Se comprobó que **pasan aislados** y que `test_gui_ux` junto a
`test_accessibility` están en verde juntos.

Es el problema conocido que `docs/RELEASE.md` describe desde la fase 020, y no
se subió ningún umbral para disimularlo. Se registra aquí con las tres
ejecuciones porque una puerta que publica un número y esconde la varianza es
justo lo que este programa lleva cuarenta fases corrigiendo.

## Pruebas de la propia puerta

`tests/test_v2_gate.py` pasa de 20 a **34 tests**. Dos de los nuevos no comprueban
un invariante, sino **la extensión de la puerta**:

- que haya exactamente 23 invariantes, y que los diez de la 040 estén
  presentes por nombre;
- que `PHASES` llegue a la 040.

Eso no es burocracia. Una puerta que deja de comprobar en el límite viejo lo
hace **sin ponerse roja**, que es la forma más discreta de pudrirse; por eso el
límite necesita un test propio.

Dos más comprueban que los checks nuevos **saben decir que no**: una cadena sin
catalogar y una paleta casi blanca sobre blanco.

## Privacidad y seguridad

| Comprobación | Resultado |
|---|---|
| Nada sale del equipo | invariante 2: sin sockets, sin cliente HTTP, sin SDK |
| Presupuesto de dependencias | invariante 1: `pypdf` y `watchdog`, y nada más |
| Inventario de privacidad | invariante 6: 15 tablas, todas declaradas |
| `forget` elimina los derivados de las capas nuevas | invariante 7 más `tests/test_privacy.py` |
| Modo portable | invariantes 17 y 18: sin fugas y sin caídas silenciosas |
| Búsquedas guardadas sin datos del usuario | invariante 23 |
| Cargas maliciosas | invariante 15: presupuesto y rechazo de zip-slip |

## Decisión: **no se publica**

**No se publica, y la razón es una regla del proyecto, no una carencia.**

`docs/RELEASE.md` dice en el paso 15 de su lista: *publicar (push) **solo** con
instrucción explícita*. El prompt de esta fase dice «decidir qué se publica y
publicarlo, o explicar por qué no», y no hay ninguna instrucción de publicar en
esta sesión. El procedimiento fue escrito para que la decisión de publicar no
dependa de que alguien tenga prisa un viernes.

Lo que **sí** está listo, y es lo que la fase tenía que dejar:

- las 40 fases commiteadas, cada una con su informe;
- el árbol limpio y el gate en verde;
- los tres artefactos de `packaging/build.ps1` construidos y arrancados;
- las puertas por fase, todas en SHIP.

Queda pendiente, y está escrito en la lista del release, lo que requiere una
decisión humana: subir a `main`, etiquetar, publicar los artefactos con sus
hashes y redactar las notas de versión.

## Limitaciones

- **La puerta lee el código y construye objetos; no lee la interfaz.** Los
  invariantes 21 y 22 comprueban catálogo y contraste, y el recorrido con
  teclado es un test. Lo que un lector de pantalla real anuncie no se ha
  medido, y sigue sin medirse desde la 039.
- **El invariante 15 lee el extractor, no lo ejecuta con una bomba.** La
  propiedad que se comprueba es que el defences *están en el camino*; que
  funcionen lo prueba el corpus hostil de `tests/test_archive_source.py`.
  Reimplementar la extracción dentro del gate sería una segunda implementación
  de la misma idea.
- **El invariante 19 compara recetas de build, no binarios.** Que los dos
  ejecutables arrancan lo comprueba `packaging/build.ps1` y el humo de CI, que
  se ejecutan aparte y no forman parte de este comando.
- **El invariante 20 comprueba que la puerta *puede* no concluir, no que la
  máquina esté tranquila.** La medición en sí es el informe de la 038.
- **Las cifras de latencia pertenecen a una CPU.** La puerta lo dice y lo
  declara en el baseline; en otra máquina el veredicto es indicativo.
- **Sin revisión independiente.** Ya declarado en las notas de la 2.0.0 y
  mantenido en las diez fases de este programa.