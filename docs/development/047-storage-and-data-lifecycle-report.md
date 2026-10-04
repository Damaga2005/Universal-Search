# Fase 047 — Almacenamiento y ciclo de vida

Estado: implementada. Puerta: `python -m evaluation.storage_gate`.
Fase anterior: `046-indexing-scale-report.md`.

## La pregunta que la fase responde

*«Un usuario puede entender y controlar la huella de almacenamiento de
Universal Search y el ciclo de vida de sus datos derivados sin manipular su base
de datos a mano.»*

La respuesta corta, medida: **antes no podía**, y no por falta de comandos sino
porque la pregunta no tenía respuesta. El inventario decía «índice: 3,8 MB»
mientras el índice real ocupaba 71 MB, y esa diferencia aparecía **entre dos
búsquedas**, sin comando, sin registro y sin aviso.

## El hallazgo

### El 95 % del índice es derivado, y sólo existe después de usarlo

Las tres capas opcionales **no se construyen al indexar**. Medido: tras indexar
1000 documentos, `document_semantic`, `document_fuzzy_*` y
`document_graph_*` tienen **cero filas**. Se construyen la primera vez que una
función las usa.

Por eso la contabilidad por bytes es tan difícil: el número depende de si el
usuario ha usado la búsqueda semántica, la difusa o los documentos relacionados
en los últimos minutos.

| 1000 documentos | bytes | cuota |
|---|---:|---:|
| índice canónico (`documents` + FTS5) | 3.801.088 | **4,8 %** |
| derivado semántico | 55.799.808 | 70,6 % |
| derivado difuso | 4.395.008 | 5,6 % |
| derivado del grafo | 15.130.624 | 19,2 % |
| **fichero completo** | **79.138.816** | |

En filas la proporción es aún más brutal: **4.293 canónicas frente a 293.252
derivadas**.

Y el coste por documento de la capa semántica es **superlineal**: 200 términos
de contenido por documento. Con 400 documentos lo derivado es el 5 % del
índice; con 1000 es el 95 %.

## Dos defectos reales, ambos medidos antes y después

### 1. `maintenance(vacuum=True)` no devolvía nada y lo decía haberlo hecho

En modo WAL un `VACUUM` reescribe la base entera **dentro del WAL**, y el
fichero principal sólo se trunca en un checkpoint posterior. `maintenance`
hacía el checkpoint *antes* de vacuar y nunca después.

Medido sobre el mismobase de datos (4000 filas insertadas y borradas, 1.792.208
bytes de db + WAL):

| | resultado |
|---|---|
| checkpoint, VACUUM | 1.762.464 bytes (**−29.744, 1,7 %**) |
| VACUUM, checkpoint | 196.608 bytes (**−89 %**) |

Y sobre el corpus real, tras purgar 74.944.512 bytes de datos derivados:

```
maintenance(vacuum=True)   78.745.600 bytes   <- devueltos: 0
                            freelist 18.036 -> 0     <- "18.036 páginas recuperadas"
                            pages 19.225 -> 1.125   <- "sólo quedan 1.125 páginas"
```

Las tres cifras del informe eran ciertas y las tres describían una base de datos
que seguía ocupando 78,7 MB. `page_count` se lee por la conexión y ya ve el WAL,
así que el número de páginas correcto convive con un fichero que no se ha
truncado.

Se midieron cuatro mecanismos sobre copias de esa misma base —VACUUM crudo más
checkpoint, VACUUM con `mmap_size=0`, VACUUM en journal `DELETE` y
`VACUUM INTO`— y **los cuatro** devolvieron los bytes. El problema no era
VACUUM: era que el intercambio no se hacía de forma verificable.

### 2. La ruta de borrado declarada no liberaba almacenamiento

Las rutas que el inventario ya prometía —`intelligence clear`,
`SemanticIndex.remove_all()`, `FuzzyIndex.remove_all()`, el `clear()` del
grafo— **no dan ni un byte** al usuario. Liberan páginas; los bytes se quedan.

Medido:

```
indice con las tres capas      71,2 MiB
tras purgar lo derivado        71,2 MiB    <- las filas se fueron
recuperables (paginas libres)  66,7 MiB    <- pero el fichero no se movio
tras compactar                  4,4 MiB
```

## Lo que entra

### `SearchDatabase.compact()` — y `maintenance(vacuum=True)` ahora significa eso

Reescribe el índice en una copia compacta e intercambia, en dos fases:

1. `VACUUM INTO` una copia temporal, con el original intacto;
2. `PRAGMA integrity_check` y **recuento de filas de las 20 tablas**, una a una;
3. checkpoint del original, borrado de `-wal` y `-shm`, e intercambio.

Si la verificación falla, `DatabaseCompactionError` y el candidato se descarta:
**el índice del usuario sigue ahí**. Es la razón de que el intercambio sea en
dos fases, y la razón de que el paso 2 exista.

El detalle de Windows: `sqlite3.Connection.__exit__` hace commit pero **no
cierra**, y una conexión viva mantiene su handle sobre el `-wal`. La primera
versión falló con `WinError 32` por un `cursor` sin agotar. Ahora el cursor se
consume, la conexión se cierra explícitamente y el borrado de los ficheros
auxiliares reintenta un número acotado de veces antes de rendirse — porque un
`-wal` viejo junto a una base nueva es como un índice bueno se vuelve corrupto.

### El contrato de ciclo de vida, completo para los 15 datasets

`DataItem` tenía 8 campos. El contrato de la fase pide **siete** cosas por
dataset, y faltaban tres: **responsable**, **esquema/versión** y **ruta de
reconstrucción** (más **comportamiento de migración**). Sin ellas, un dataset
derivado sin ruta de reconstrucción era indistinguible de uno canónico que no
se puede reconstruir.

### Dos datasets que nadie declaraba

El inventario describía 13 categorías mientras el esquema tiene **21 tablas**.
Siete no tenían declaración alguna:

- **las cinco tablas sombra de FTS5** (`_data`, `_idx`, `_docsize`,
  `_content`, `_config`), que son tablas reales en `sqlite_master`, sostienen
  el índice invertido —la mayor cosa individual de la base de datos— y son
  invisibles
  para cualquier cosa que lea `sqlite_master` por nombre declarado;
- **`schema_migrations`**, que es precisamente el registro del que depende
  cualquier afirmación sobre migración;
- `sqlite_sequence`, que SQLite posee y ningún código del proyecto escribe:
  excluido **con nombre**, para que el hueco sea una decisión discutible y no un
  olvido.

### `universal-search storage show` y `storage compact`

```
$ universal-search storage show
index bytes:      46.2 MiB across 11,830 pages of 4,096 B
rows by category (exact; this build cannot attribute bytes per table):
  documents                    600
  content                      600
  fts_shadow                 1,379
  intelligence                 600  (derived)
  relationship_graph         9,793  (derived)
  semantic_index          120,605  (derived)
  fuzzy_index              48,112  (derived)
  canonical 2,580 rows, derived 179,110 rows
reclaimable now:  43.8 MiB (11,218 free pages) -- `universal-search storage compact`
                  -- without it, purging derived data frees pages but not bytes
```

## Lo que esta fase NO hace: bytes por tabla

El reparto de bytes por tabla **no es posible en esta build**, y está medido:

```
dbstat          no (no such table)
sqlite_dbpage   no (no such table)
sqlite_stat1    no (no such table)
sqlite_stat4    no (no such table)

compile_options:  ENABLE_FTS3  ENABLE_FTS4  ENABLE_FTS5  ENABLE_RTREE
```

`compile_options` no lista `ENABLE_DBSTAT_VTAB` ni `ENABLE_DBPAGE_VTAB`.

El informe **no estima**. Devuelve `per_table_bytes: None` y una nota que explica
por qué. Lo que sí devuelve —bytes de fichero, páginas, páginas libres, filas
por categoría— es exacto, y la puerta lo **comprueba contra un
`SELECT COUNT(*)` por tabla** en vez de fiarse de su propio código.

Los bytes marginales por capa (la tabla de arriba) sólo son medibles **offline**,
construyendo el corpus con y sin cada capa. Viven en este informe y en el
registro de la puerta, no en la salida de runtime.

## La puerta: 9 invariantes, 9/9 SHIP

`python -m evaluation.storage_gate` → **9/9 SHIP**, salida 0, 1000 documentos.

| # | Invariante | Medido |
|---|---|---|
| L1 | ninguna tabla sin dataset declarado | 0 |
| L2 | ningún dataset con contrato incompleto | 0 de 15 |
| L3 | toda categoría con tabla declarada | 0 huecos |
| L4 | los recuentos cuadran con `COUNT(*)` por tabla | 0 discrepancias en 297.545 filas |
| L5 | páginas y libres cuadran con SQLite | coinciden |
| L6 | **no** se afirman bytes por tabla sin poder medirlos | 0 |
| L7 | `compact()` devuelve **al menos** lo que el informe prometió | 0 por debajo |
| L8 | compactar no cuesta un documento | 1.000 antes y después, 20 tablas verificadas |
| L9 | compactar no cuesta un resultado de búsqueda | 1.000 coincidencias antes y después |

## Tres defectos en el propio instrumento de medición

Todos del mismo tipo: algo parecía funcionar y no se alcanza.

1. **La puerta nunca llamaba a `use_every_layer`.** La función estaba escrita y
   documentada, y no invocada. La puerta imprimió «índice con las tres capas
   3,7 MiB», 108 KiB derivados, y **los nueve invariantes en SHIP**. Una
   medición que no ejerce lo que describe es la forma más antigua de
   equivocarse.
2. **L7 exigía igualdad donde sólo cabía una cota.** Pedía que los bytes
   devueltos coincidieran con los anunciados al 2 %, y falló al 76 %: el informe
   ofrecía 20,0 KiB y `compact()` devolvía 84,0 KiB. El informe no mentía: es
   una **cota inferior**, porque VACUUM también defragmenta páginas que SQLite
   nunca liberó. Exigir la igualdad habría obligado a inventar un factor de
   desfragmentación. El invariante correcto es unidireccional: **lo anunciado,
   devuelto *al menos***.
3. **El primer comando era inalcanzable.** El bloque se insertó dentro de
   `_privacy_command`, y como el despacho es una cadena por `args.command`,
   escribir `storage` caía en la rama final de búsqueda y moría con
   `AttributeError: 'Namespace' object has no attribute 'context'`. El
   analizador tenía el comando; el código no. Lo cuenta el orden de las filas:
   el parser se registró antes que el despacho.

Y dos de las sondas iniciales afirmaron cosas falsas antes de refinarse:

- una afirmaba que «`maintenance()` sí hace el VACUUM», siendo su valor por defecto
  `vacuum=False`;
- otra concluyó que `compact()` rompía la búsqueda con `búsqueda
  'documento': 0 resultados`. La palabra **no está en ese corpus**; el mismo
  `MATCH` directo devolvía 0 **antes** de compactar.

## Deuda declarada, no tocada

- **La varianza del WAL entre corridas** (fase 046) sigue sin explicarse: el
  mismo trabajo idéntico dio 1,5 s y 5,6 s.
- **Qué mueve los 16 KiB del tamaño del índice** (fase 046) sigue sin
  explicarse.
- ~~**`fuzzy_gate.py` y `suggest_gate.py` siguen sin la metodología de veto de
  carga** que tiene `perf_gate`~~. **Cerrado en la 047b**: las tres puertas usan
  `perf_gate.load_gate()`, y una máquina ocupada produce INCONCLUYENTE en vez de
  un veredicto sobre otra cosa.
- **El coste superlineal de la capa semántica** —200 términos por documento,
  55,9 MB para 1000 documentos— **no se ha optimizado**. Es el mayor coste
  medido del proyecto y es deliberado: medir el vectorizado y decidir si
  200 términos por documento es el número correcto requiere saber para qué se
  usan, y eso no lo decide esta fase.
- **El caso de las junctions en `scan_local`** sigue sin conjunto de visitados.
- **No hay política de retención automática** para logs ni métricas más allá de
  la rotación por tamaño que ya existía; se declara su ciclo de vida, no se
  cambia su comportamiento.

## Cierre posterior: 047b

Las tres puertas de latencia que quedaban sin cerrar lo estaban por la máquina,
y una de ellas —`suggest_gate`— cerraba **mal**. Un veto de carga añadido a
`suggest_gate` y `fuzzy_gate` con la ayuda de 12 pruebas inyectando las señales.
El detalle, con las mediciones y los tres defectos del propio instrumento, está
en el `CHANGELOG` de la 047b y en `tests/test_load_veto.py`.

## Verificación

- `python -m evaluation.storage_gate` → **9/9 SHIP**, salida 0.
- `python -m evaluation.gate` → PASS 23/23 con el recuento documentado.
- Pruebas: total en `README.md`.
- pyflakes limpio.

## Para la 048

La 048 es el paquete de aplicación y distribución. Lo que esta fase le deja:

- **un comando que responde a la pregunta que el usuario ya se hacía** y que
  nadie podía contestar;
- **el patrón de no estimar**: `per_table_bytes: None` con una nota que explica
  la limitación es más útil que un número inventado con dos decimales;
- **un intercambio en dos fases verificado**, que es lo que cualquier operación
  destructiva sobre el índice debería hacer;
- y una pregunta que la fase formula y no resuelve: si el 95 % del índice es
  derivado y se construye al usarlo, **¿debería seguir construyéndose al
  usarlo?** Eso es una decisión de producto, no de almacenamiento.