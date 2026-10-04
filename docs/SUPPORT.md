# Matriz de soporte

Estado: medida en la fase 048. Generada con `python -m evaluation.portability_gate`.

Este documento dice **qué está probado** y **qué sólo se sondea**. No dice «se
soporta» para nada que no esté en la tabla de la puerta con evidencia.

La regla de la fase 048 es que *«el desajuste actual entre CI y runtime debe
hacerse explícito y resolverse o documentarse, en vez de tratarse en silencio
como soporte»*. Aquí está el desajuste, medido, y está resuelto en dos direcciones
a la vez: el CI se amplía para cubrir lo que se promete, y la promesa se acota a
lo que el CI cubre.

## Resumen

| | Soportado | Probado en CI | Sondeado |
|---|---|---|---|
| Sistema operativo | Windows 10, Windows 11 | `windows-latest` (Windows Server) | Linux (núcleo) |
| Arquitectura | x86-64 | x86-64 | x86-64 |
| Python | 3.12, 3.13, 3.14 | 3.12, 3.13, 3.14 | — |
| GUI (Tk) | sí | build + arranque del `.exe` | — |
| Empaquetado | PyInstaller one-dir y one-file | ambos, más prueba de arranque | — |

**Linux no es una plataforma soportada.** El job `core-portability` corre la
suite allí como **sonda no bloqueante** (`continue-on-error: true`), y su
propósito declarado en el propio fichero de CI es comprobar que el núcleo
independiente de la plataforma sigue siendo portable. La GUI, el registro de
Windows, la tecla rápida y los scripts del instalador son específicos de Windows
por diseño.

macOS **no se prueba en absoluto**. No aparece en ningún job y no hay ninguna
afirmación sobre él.

## El detalle, eje por eje

### Sistema operativo

| | |
|---|---|
| Declarado | Windows 10, Windows 11 |
| Medido en desarrollo | Windows 11 Professional, build 26200 (`platform.win32_ver()`) |
| CI | `windows-latest`, que es una imagen de **Windows Server** en un runner alojado |

**Lo que el CI no prueba, y está declarado aquí para que nadie lo dé por
hecho:** `windows-latest` no es Windows 10 ni Windows 11. Es la imagen de
Windows Server que ofrece GitHub, que no es el sistema que tiene un usuario. El
proceso de indexación, el registro, la tecla rápida y la bandeja del sistema se
ejercitan en un sistema operativo distinto del de destino.

Este valor no se puede observar desde el proceso, así que se declara en lugar de
medirse. Una matriz construida sobre una suposición es peor que una matriz que
dice que no lo sabe.

### Python

| | |
|---|---|
| Declarado | `>=3.12,<3.15` |
| Medido en desarrollo | **CPython 3.14.6**, compilador MSC v.1944 64 bit (AMD64) |
| CI | 3.12, 3.13, 3.14 |

**El desajuste que la fase exigía hacer explícito.** Antes de la 048, el paquete
decía `requires-python = ">=3.12"` —una promesa abierta— mientras el CI probaba
**una sola versión, la 3.12**, y la máquina de desarrollo corre **3.14**. Es decir:
el código se desarrollaba y se ejecutaba en una versión que nadie probaba, y el
paquete prometía 3.15, 3.16 y siguientes sin evidencia de nada.

Resuelto en las dos direcciones:

- el CI ahora corre una matriz de 3.12, 3.13 y 3.14, que es lo que el paquete
  dice soportar;
- y `requires-python` se acota a `<3.15`, porque una promesa abierta es una
  promesa que nadie ha medido.

El coste de acotar es que hay que tocar `pyproject.toml` al salir 3.15. Es el
precio correcto: preferimos un aviso de `pip` a una promesa sin comprobar.

**Una observación medida sobre 3.14:** `sqlite3.version` ya **no existe** en esta
versión. El módulo `sqlite3` expone `sqlite3.sqlite_version`. No es un defecto
de este proyecto —nadie lo usa—, pero es exactamente la clase de cambio que hace
que una versión no probada sea una versión dangerous, y por eso el rango se acota
en vez de dejarse abierto.

### Arquitectura

| | |
|---|---|
| Declarado | x86-64 |
| Medido | `platform.machine()` = `AMD64`, puntero de 64 bits, `MSC v.1944 64 bit` |
| CI | x86-64 |

**ARM64 no se prueba ni se declara.** La imagen de Windows que se publica es
x86-64 y no se ha medido el emulador ARM64 de Windows sobre x86-64, que es donde
este proyecto podría funcionar o no. No hay afirmación en ninguna dirección.

### Tk

| | |
|---|---|
| Declarado | disponible |
| Medido | Tcl/Tk **8.6**, ventana abierta correctamente |
| CI | el `.exe` de la GUI arranca en el runner y se le pide que salga |

**Una sola combinación de escalado medida:** 96 DPI, `tk scaling` 1.334,
pantalla 2560×1440. El checklist de Windows de la 041 cubre escalado y DPI en
cuadrícula; aquí se registra el valor único que este entorno puede observar, y se
dice que es uno solo. Un proyecto con DPI 150 % y fuentes de texto más grandes
no es el mismo que éste, y no se ha medido.

### Locale y codificación

| | |
|---|---|
| Locale del sistema | `('es_ES', 'cp1252')` |
| `locale.getpreferredencoding(False)` | **`cp1252`** |
| Ficheros del proyecto | UTF-8 explícito en todos: config, métricas, eventos, control-center, logs |
| Salida del CLI | UTF-8, forzado explícitamente (fase 048) |

Este fue un defecto real, encontrado midiendo en vez de asumiendo. Durante
veinticinco fases, todos los comandos de este proyecto se precedieron de
`$env:PYTHONIOENCODING="utf-8"` — por costumbre, sin que nadie comprobara si era
necesario.

Medido: **redirigir `universal-search search` a un fichero producía bytes que no
son UTF-8 válido** — fallan al decodificar en el byte 19. Los caracteres en sí
sobrevivían, porque `ó` y `ñ` existen en cp1252, así que **nada se veía mal en
pantalla** y el daño sólo aparecía cuando algo posterior asumía UTF-8.

La salida del CLI ahora fija `encoding="utf-8"` explícitamente, y
`errors="replace"` se conserva: es lo que permite forzarlo sin que el proceso
muera al imprimir en una consola antigua.

**El intercambio declarado:** en una consola cp1252 legacy, la salida con
acentos no se verá correcta hasta `chcp 65001`. Es el mismo requisito que ya
exigen los propios ficheros UTF-8 del proyecto, y un carácter mal dibujado es
mejor que un flujo que ninguna herramienta puede leer.

### SQLite

| | |
|---|---|
| Medido | **3.50.4** |
| Modo | WAL, `synchronous=NORMAL`, `mmap_size=256 MiB`, `temp_store=MEMORY` |
| Sin `dbstat` | `compile_options` sólo lista `ENABLE_FTS3/4/5` y `ENABLE_RTREE` |

La versión de la biblioteca SQLite viene con el intérprete de Python. Se registra
porque la fase 047 depende de ella: sin `dbstat`, el reparto de bytes por tabla es
imposible y el informe lo declara en vez de estimarlo.

### Empaquetado

| | |
|---|---|
| PyInstaller | **6.22.3** |
| Artefactos | one-dir (`UniversalSearch\\`) y one-file |
| CI | ambos se construyen; se lanzan **solos**, desde una carpeta sin hermanos |

La prueba del ejecutable one-file en aislamiento no es un detalle: la 2.0.0
publicó dos `.exe` que no arrancaban (`PYI-8: Failed to load Python DLL`) porque
una compilación one-dir necesita `_internal/`. Esa forma de fallo está ahora en
CI como regresión.

## Lo que NO se declara soportado

- **Linux**: sólo sonda del núcleo. No hay GUI, ni instalador, ni integración con
  el shell, ni tecla rápida.
- **macOS**: nada. No se prueba, no se mide, no se menciona como posible.
- **ARM64**: no se prueba ni se afirma en ninguna dirección.
- **Python 3.15+**: fuera del rango declarado hasta que el CI lo cubra.
- **Windows Server**: es lo que el CI prueba, no es lo que se soporta.

## Cómo se mantiene esta matriz

`python -m evaluation.portability_gate` falla si:

- `pyproject.toml` deja de acotar `requires-python`, o el rango deja de
  coincidir con las versiones que el CI nombra;
- los clasificadores desaparecen o vuelven a prometer un sistema operativo que la
  matriz declara no soportado;
- el CI deja de cubrir una versión del rango, o añade una fuera de él;
- el intérprete que ejecuta la puerta está **fuera del rango declarado** — que es
  el fallo que hizo esta fase necesaria;
- esta matriz y los docs se contradicen sobre qué es soporte y qué es sonda.

La puerta no puede comprobar el comportamiento en otra máquina ni en otra
versión de Python. Comprueba que lo que el proyecto **afirma** coincide con lo
que el CI **demuestra**, que es donde el desajuste vivía.