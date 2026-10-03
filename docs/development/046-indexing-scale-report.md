# Fase 046 — Escalabilidad y rendimiento de indexación

Estado: implementada. Puerta: `python -m evaluation.scale_gate`.
Fase anterior: `045-search-quality-report.md`.

## La regla que gobierna la fase

*«Optimiza sólo los cuellos de botella medidos»* y *«no añadas concurrencia a
ciegas»*. Es una fase cuyo resultado honesto puede ser **no cambiar nada**. Y lo
fue: **ninguna optimización entra en este commit**.

Lo que sí entra son **tres defectos de corrección** que la suite entera no podía
ver, y una puerta que hace visible el coste real de indexar.

## Qué se encontró

### Tres defectos reales, todos medidos antes y después

| # | Defecto | Medición del defecto | Consecuencia |
|---|---|---|---|
| 1 | `index_root` enlazaba `cancel=cancel.cancelled` — la **propiedad booleana** del token, no el `CancelCheck` que los extractores esperan | 6 documentos, **6 extracciones fallidas**, contenido vacío en los 6 | una pasada con token real indexaba **todo vacío** y reportaba cero errores |
| 2 | El bucle **no consultaba el token**; `scan_local` no lo recibe | token cancelado antes de empezar → `created=6` | una pasada cancelada indexaba el árbol entero |
| 3 | `ExtractionResult(error=...)` dejaba `status = OK` | `ExtractionResult(error="cancelled").status` → `OK` | **una sola extracción fallida dejaba un documento en blanco para siempre** |

Y un cuarto, que sólo apareció al arreglar el tercero:

| # | Defecto | Medición | Consecuencia |
|---|---|---|---|
| 4 | el camino rápido **reencolaba `error` y `cancelled` por igual** | el PDF deliberadamente ilegible del corpus quedaba en `error`, y cada pasada posterior lo releía, fallaba y marcaba sucio el grafo y la capa difusa | un fichero **permanentemente roto** se releía en cada pasada, para siempre |

El cuarto es el más interesante porque **la mentira del tercero era lo que
sostenía verde una prueba**. El PDF ilegible del corpus se registraba como
`ok` pese a fallar, y por eso una segunda pasada sobre un árbol sin cambios no
tocaba nada. Al corregir el estado, esa prueba pasó a rojo — y no porque el
arreglo fuera malo, sino porque el arreglo **destapó una segunda regla que
nunca se había escrito**: no todo `error` merece reintento.

La regla que queda, y que es transitorio contra permanente, no éxito contra
fracaso:

- **`cancelled`** — nos dijeron que paráramos. Al fichero no le pasa nada, y la
  siguiente pasada es un intento nuevo. **Se reintenta.**
- **`error`** — el extractor falló *con esos bytes*. El mismo extractor sobre la
  misma entrada falla igual, así que reintentar es trabajo para nada. Un cambio
  posterior del fichero mueve su mtime y dispara la reextracción de todos modos.
  **No se reintenta.**

`no_content` —un binario sin capa de texto por naturaleza— tampoco se reintenta,
o cada documento sin texto del corpus se reextraería en cada pasada para
siempre. Hay pruebas que fijan las tres ramas por separado.

El tercero es el que más importa, y la cadena es completa:

```
una extracción cancelada  ->  el documento se escribe sin texto y sin fila FTS
                          ->  el camino rápido compara tamaño y mtime
                          ->  la pasada siguiente lo declara "sin cambios"
                          ->  nunca se vuelve a leer el fichero
```

Medido: una pasada limpia posterior reportaba `unchanged=6` con el documento
en blanco **y** `busqueda 'documento 2': 0`. Invisible para siempre.

La causa de fondo estaba en el **tipo**: `ExtractionStatus` documenta una
precedencia en la que `error` gana a todo, y no la aplicaba. El arreglo es un
`__post_init__` que hace la combinación «error + estado OK» irrepresentable,
porque un campo de estado que puede mentir es peor que no tener campo de
estado, y cualquier arreglo en el punto de llamada deja al siguiente llamante
cometiendo el mismo error.

La consulta que lo habilita trae `extraction_status` a la fila almacenada. El caso
contrario importa igual: `no_content` —un binario que el extractor no puede
leer— **sigue por el camino rápido**, o cada documento sin texto del corpus se
reextraería en cada pasada para siempre. Hay una prueba que lo fija.

### Y una afirmación mía que era falsa

La fase 045 dejó escrito, en el código y en tres sitios de la documentación, que
la dispersión del tamaño del índice se explicaba porque «el indexador recorre
el directorio en orden de enumeración, que no está ordenado».

**Es falso.** `providers/local.py:140` ordena cada directorio con
`sorted(scanner, key=lambda e: e.name.lower())`. Lo comprobé antes de actuar
sobre ello, y la 045 tenía una observación real —16 KiB entre dos pasadas con la
CPU al 2 %— con un mecanismo no medido pegado encima.

La segunda teoría que merecía prueba —la longitud de la ruta absoluta, ya que
cada pasada corre en una raíz `mkdtemp()` nueva— queda **refutada por
medición**: cuatro raíces distintas, cuatro rutas de 79 caracteres, cuatro
totales idénticos.

Lo que mueve esos bytes **sigue sin estar explicado**, y ahora el código lo dice
en lugar de adivinar una tercera vez.

## La curva, medida

Es el encargo central: *«el rendimiento sigue siendo predecible cuando crece el
corpus»*. La puerta mide **la forma**, no un número absoluto — un umbral de
latencia en una máquina compartida es una moneda al aire, y ese papel ya lo
tiene `perf_gate` con su veto de carga y su huella de máquina.

| | 1 000 docs | 4 000 docs | razón |
|---|---:|---:|---:|
| tiempo de indexado | 4,82 s | 31,69 s | — |
| **coste por documento** | 4,82 ms | 7,92 ms | **×1,64** |
| **KiB por documento** | 3,79 | 3,59 | **×0,95** |
| **memoria pico** | 2,42 MiB | 3,36 MiB | **×1,39** |

**El espacio es lineal y la memoria casi constante.** El tiempo crece más que
los documentos, y la puerta lo reporta con un límite de ×2,5 sobre el coste por
documento.

### Dónde está el coste

El perfil de Python **no lo ve**: contabilizó 0,83 s de una pasada de 4,90 s. El
resto es código C. Aislándolo con SQL crudo, sin indexador en medio:

```
10 000 documentos, filas normales:  0,249 s
los mismos, con FTS5:               1,539 s     ->  FTS5 es ~90 % del coste
```

Y no es una curva suave: a 5 000 documentos la misma operación tardó 2,779 s
mientras que a 10 000 tardó 1,539 s. Eso es la firma de los **merges de
segmentos** de FTS5, no de un coste cuadrático.

## Tres hipótesis de optimización, medidas y RECHAZADAS

| Hipótesis | Resultado | Decisión |
|---|---|---|
| `wal_autocheckpoint=20000` | sonda de inserciones: 1,521 s → 0,695 s a 10k (**2,2×**). End-to-end por `index_root`, mejor de 3 a 5k: **51,86 s frente a 50,74 s** — 0,98×, dentro del ruido | **revertida** |
| `cache_size=-64000` | 1,556 s frente a 1,521 s por defecto | rechazada |
| `mmap_size=1GiB` | 1,651 s, y una vuelta tardó **55 s** | rechazada |

La primera merece explicación porque es una trampa útil: la sonda de SQL crudo
**sí** la mejora, porque sólo inserta. El indexador real lee una vez por fichero
*entre* escrituras, así que un WAL mayor penaliza exactamente las lecturas que
la sonda nunca hace. Medir el microbenchmark y extrapolarlo al producto habría
sido cambiar el programa sobre una evidencia falsa.

Los tres números quedan escritos en `database.py`, junto a la constante que
**no** se cambió, para que nadie vuelva a ganar el mismo dato.

La conclusión honesta: **el cuello de botella está dentro de SQLite** y arreglarlo
exigiría rediseñar el índice, que es exactamente lo que la fase prohíbe.

## La puerta: 8 invariantes

`python -m evaluation.scale_gate` → **8/8 SHIP**, salida 0.

| # | Invariante | Medido |
|---|---|---|
| S1 | el coste por documento no se dispara al crecer | ×1,64 (límite 2,5) |
| S2 | el índice escala linealmente | ×0,95 (límite 1,35) |
| S3 | la memoria no escala con el índice | ×1,39 (límite 4,0) |
| S4 | dos índices del mismo contenido coinciden | 0 diferencias |
| S5 | una pasada limpia deja todo localizable | 0 sin texto |
| S6 | una extracción fallida **se repara** | 0 sin reparar |
| S7 | una pasada cancelada **no borra nada** | 0 borrados |
| S8 | una pasada limpia no da errores | 0 |

## Defectos encontrados en el propio instrumento de medición

1. **La primera curva estaba medida con `tracemalloc` activo**, que instrumenta
   cada asignación: la pasada media el perfil tanto como el indexador. Corregido
   midiendo tiempo y memoria en pasadas separadas.
2. **S6 estaba invertido**: computaba `blank_before - blank_after`, que es el
   conjunto de documentos **sí reparados**, así que la puerta reportó un
   documento sin reparar en una ejecución donde la reparación funcionaba
   perfecta. Un producto verde no puede carregar con una aritmética mala del
   instrumento. La corrección está escrita junto al error.
3. **`perf_gate` afirmaba una causa que no había medido** (ver arriba), y una de
   sus pruebas (`test_index_size_must_be_identical_between_runs`) seguía
afirmaba una igualdad que el código ya no exige. Ambas corregidas.

## Lo que esta fase NO hace

- **No indexa 100 000 documentos.** El perfil de la puerta es 1k/4k para que sea
  ejecutable a mano; la extrapolación a 100k está **declarada como tal**, no
  medida. El `benchmarks` existente ya ofrece `--profile 100000` bajo demanda.
- **No cambia el diseño del índice ni el tokenizador de FTS5**, que es donde
  está el 90 % del coste.
- **No añade concurrencia.** El recorrido es secuencial y sigue así, y la fase
  lo prohíbe explícitamente.
- **No arregla la varianza del WAL.** Se midió (carreras de 1,5 s a 5,6 s sobre
  entrada idéntica) y **no se ha explicado**; queda declarado.
- **NoMigra `fuzzy_gate.py` ni `suggest_gate.py`** al veto de carga. Sigue siendo
  deuda, como se declaró en la 044 y la 045.
- **No toca el recall del grafo por junction**, que la auditoría señaló como el
  único recurso sin cota (`scan_local` no tiene conjunto de visitados). No está
  medido como problema y queda declarado.

## Limitaciones

1. **La forma de la curva está medida entre 1k y 4k.** El comportamiento entre
   10k y 100k no está medido y no se afirma.
2. **S1 tiene un límite de ×2,5 porque la máquina es ruidosa.** Dos corridas de
   la misma operación han dado 4,82 y 7,92 ms por documento. La puerta mide
   forma, no valor, justamente por eso; quien necesite el valor absoluto tiene
   `perf_gate`.
3. **El reparto de FTS5 se mide en una sonda de SQL crudo**, no en el indexador.
   Es la única forma de atribuirlo, y por eso el S1 es «la forma» y no «el
   coste de FTS5 es el 90 %» como afirmación exacta.

## Verificación

- `python -m evaluation.scale_gate` → **8/8 SHIP**, salida 0.
- `python -m evaluation.gate` → PASS 23/23 con el recuento documentado.
- Pruebas: sólo las que tocan lo modificado. Total en `README.md`.
- pyflakes limpio.

Una nota sobre el proceso, porque es el patrón de siempre: el
cuarto defecto **no se encontró leyendo el código**, sino porque una prueba que
pasaba en verde pasó a rojo al corregir otro defecto. Corregir una mentira suele
desmontar la mentira siguiente, que estaba apoyada encima.

## Para la 047

La 047 es «almacenamiento y ciclo de vida». Cuatro cosas que esta fase le deja:

- **la medición de coste real de indexar**, que es FTS5 al 90 % y tres
  hipótesis ya rechazadas con números;
- **el patrón de sonda**: para atribuir un coste hay que medir el subsistema
  solo, porque el perfil de Python no ve el 17 % del tiempo que se le atribuye;
- **un tipo que ya no puede mentir**: `ExtractionResult` con un error y estado
  `ok` es hoy irrepresentable;
- y dos afirmaciones concretas sobre lo que **no** sabemos: qué mueve los 16 KiB
  del tamaño del índice, y por qué el WAL variance tanto sobre entrada
  idéntica. Ambas están escritas en el código como desconocimiento, que es la
  forma correcta de pasárselas a quien venga.