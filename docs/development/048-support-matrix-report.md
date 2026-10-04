# Fase 048 — Matriz de soporte Windows y entornos

Estado: implementada. Puerta: `python -m evaluation.portability_gate`.
Matriz: [`docs/SUPPORT.md`](../SUPPORT.md).
Fase anterior: `047-storage-and-data-lifecycle-report.md`.

## El desajuste que la fase pedía

*«El desajuste actual entre CI y runtime debe hacerse explícito y resolverse o
documentarse, en lugar de tratarse en silencio como soporte.»*

Medido, en las tres direcciones:

| | |
|---|---|
| `requires-python` decía | **`>=3.12`** — una promesa abierta |
| el CI probaba | **3.12**, una sola versión |
| la máquina de desarrollo corría | **CPython 3.14.6** |

Es decir: el proyecto **se desarrollaba y se ejecutaba en una versión que nadie
probaba**, y el paquete **prometía 3.15, 3.16 y siguientes sin evidencia de
nada**.

Y un segundo hallazgo en el mismo eje: el paquete **no declaraba ni un solo
clasificador**. Un paquete que envía una aplicación de Windows y no dice para
qué plataforma es deja que `pip` la adivine.

## Lo que se resuelve

**En las dos direcciones a la vez**, porque arreglar sólo una deja el problema:

- el CI que puerta ahora corre una **matriz de 3.12, 3.13 y 3.14**;
- y `requires-python` queda **acotado a `>=3.12,<3.15`**.

Lo prometido y lo demostrado pasan a ser el mismo conjunto. El coste de acotar
es tocar `pyproject.toml` cuando salga 3.15: es el precio correcto, porque
preferimos un aviso de `pip` a una promesa sin comprobar.

**15 clasificadores**, que declaran Windows 10 y 11, x86-64, Python 3.12–3.14 y
español. Y deliberadamente **ninguno** de POSIX: el job de Ubuntu existe, pero
es una sonda no bloqueante, y un clasificador se lee como promesa de soporte.

## El defecto de codificación, encontrado midiendo

Durante **veinticinco fases**, todos los comandos de este proyecto se precedieron
de `$env:PYTHONIOENCODING="utf-8"` — por costumbre, sin que nadie comprobara si
hacía algo.

Medido en esta máquina (`locale.getlocale()` = `('es_ES', 'cp1252')`):

```
SIN la variable:  search redirigido a fichero -> 294 bytes
                  NO decodifican como utf-8: continuation byte inválido en el byte 19

CON la variable: search redirigido a fichero -> 296 bytes
                  decodifican como utf-8
```

Y lo importante: **los caracteres no se perdían**. `ó` y `ñ` existen en cp1252, así
que **nada se veía mal en pantalla**. El daño sólo aparecía aguas abajo, cuando un
editor, `jq` u otro programa leía el fichero suponiendo UTF-8.

Todos los ficheros del proyecto son UTF-8 explícito —config, métricas, eventos,
estado del centro de control, logs— **excepto la salida del CLI**, que heredaba la
página de códigos de la máquina. Esa era la inconsistencia.

La salida ahora declara `encoding="utf-8"`, y `errors="replace"` **se conserva**:
sigue siendo lo que evita que el CLI muera al imprimir, que era su propósito
original.

**El intercambio, declarado:** en una consola cp1252 legacy, la salida con
acentos no se verá correcta hasta `chcp 65001`. Es el mismo requisito que ya
exigen los propios ficheros del proyecto, y **un carácter mal dibujado es mejor
que un flujo que ninguna herramienta puede leer**.

Consecuencia: `$env:PYTHONIOENCODING="utf-8"` **ya no hace falta**, y hay una
prueba que lo comprueba con la variable eliminada explícitamente del entorno.

## La puerta: 9 invariantes

`python -m evaluation.portability_gate` → **9/9**, salida 0.

| # | Invariante | Medido |
|---|---|---|
| W1 | `requires-python` tiene cota superior | `>=3.12,<3.15` |
| W2 | toda versión declarada la prueba el CI | coinciden |
| W3 | toda versión probada está declarada | coinciden |
| W4 | clasificadores de plataforma presentes | 3 de 3 |
| W5 | ningún clasificador promete una plataforma no soportada | 0 |
| W6 | **el intérprete que corre la puerta está dentro del rango** | 3.14 dentro |
| W7 | el CI declara una matriz, no una versión fijada | 3.12/3.13/3.14 |
| W8 | la sonda de Linux es no bloqueante | `continue-on-error: true` |
| W9 | la documentación separa soporte de sonda | sí |

**W6 es la comprobación que habría detectado el problema original**: un proyecto
que se desarrolla en una versión que su propio CI nunca ejecuta.

**Y un error mío en la propia puerta.** W2 falló en la primera ejecución
acusando a una configuración correcta: leí `<3.15` como intervalo cerrado y
conté 3.15 como declarado. El error estaba en el instrumento, no en el proyecto, y
hay una prueba que fija que `<` es exclusivo y `<=` no.

## Lo que la puerta NO puede comprobar, y dice

- **Que la suite pase en 3.12 o 3.13.** La matriz del CI **no se ha ejecutado** —
  el cambio se hizo sin acceso a CI. Lo que sí está medido es la suite completa en
  **3.14.6**. Hasta que haya una corrida verde, 3.12 y 3.13 están **declarados y
  configurados, no probados**.
- **Que el CI sobreviva a un Windows Server**, que no es el Windows de un usuario.
- **Nada en macOS.** No hay job, no hay medición, no hay afirmación.

Un dato que mide por qué el rango se acota en vez de dejarse abierto: en 3.14.6,
**`sqlite3.version` ya no existe**. El módulo `sqlite3` expone `sqlite3.sqlite_version`.
No es un defecto de este proyecto —nadie lo usa—, pero es exactamente la clase de
cambio que hace que una versión sin probar sea una versión peligrosa.

## Los otros ejes, medidos

| eje | medido | declarado |
|---|---|---|
| SO | Windows 11 Professional, build 26200 | Windows 10, Windows 11 |
| arquitectura | `AMD64`, 64 bits, `MSC v.1944 64 bit` | x86-64; **ARM64 no se afirma en ninguna dirección** |
| Tk | Tcl/Tk **8.6**, ventana abierta | disponible |
| DPI | **96 DPI**, `scaling` 1.334, 2560×1440 | **una sola combinación medida** |
| SQLite | **3.50.4** | registrado, porque la 047 depende de la versión |
| PyInstaller | **6.22.3** | one-dir y one-file, probados aislados |

Dos cosas que la matriz dice que **no** sabe:

- `windows-latest` es una imagen de **Windows Server**, no Windows 10 ni 11. No
  se puede observar desde el proceso, así que se declara en vez de medirse: *una
  matriz construida sobre una suposición es peor que una matriz que dice que no
  lo sabe*.
- **Una sola combinación de DPI.** El checklist de la 041 cubre escalado en
  cuadrícula; aquí se registra el único valor que este entorno observa y se dice
  que es uno solo. Un proyecto con DPI al 150 % no es el mismo.

## Limitaciones

1. **La matriz del CI no se ha ejecutado.** Es el límite más importante y está
   escrito en el propio `ci.yml`, en el `CHANGELOG` y aquí.
2. **`docs/SUPPORT.md` describe un entorno, no todos.** Windows Server del CI y
   Windows 11 del desarrollo no son el mismo sistema y la diferencia no se ha
   medido.
3. **ARM64 no se ha medido** en el emulador de Windows, que es donde el proyecto
   podría funcionar o no. No hay afirmación en ninguna dirección.
4. **El único ajuste de escalado medido es 96 DPI.** El resto de la matriz de DPI
   es la del checklist 041, no una medición de esta fase.
5. **No se ha probado ninguna instalación real** de las que publica la 049. Eso es
   trabajo de la 049 y la matriz no lo anticipa.

## Verificación

- `python -m evaluation.portability_gate` → **9/9**, salida 0.
- `python -m evaluation.gate` → PASS 23/23 con el recuento documentado.
- Pruebas: total en `README.md`.
- pyflakes limpio.

## Para la 049

La 049 es distribución e instalación en Windows. Lo que esta fase le deja:

- **una matriz con dos verdades separadas**, *probado* y *probado como sonda*, en
  un formato que `portability_gate` comprueba contra el CI y contra `pyproject`;
- **el rango de Python acotado**, que la 049 debe mantener al cambiar de versión
  publicada;
- **un dato sobre el empaque que la 049 necesita**: el CI construye y **lanza
  solo** el ejecutable one-file, en una carpeta sin hermanos, porque la 2.0.0
  publicó dos `.exe` que no arrancaban;
- y la pregunta que esta fase formula y no resuelve: **`windows-latest` es Windows
  Server, y el usuario tiene Windows 10 u 11.** Instalar sobre el sistema que se
  prueba y sobre el que se usa son dos cosas distintas, y sólo una está cubierta.