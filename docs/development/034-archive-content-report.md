# Fase 034 — Contenido dentro de comprimidos

## Lo reutilizado, no reescrito

La maquinaria de la fase 025 ya sabía rechazar a un miembro ZIP hostil **antes
de leer un solo byte**: `member_problem()` rechaza nombres con traviesos, partes
de tamaño declarado excesivo y ratios de expansión implausibles mirando solo el
directorio central, y `read_member_bounded()` se niega a creerse el tamaño
declarado. Esta fase reutiliza ambas en vez de escribir un segundo juego de
reglas, más débil.

Las tres reglas que sostiene el código:

- **Sin recursión.** Un zip dentro de un zip se salta con un aviso. Sin límite
  de profundidad, los anidados son un camino de expansión sin cota y la primera
  cosa que alguien usaría para abusar de esto.
- **Solo formatos de texto registrados.** Cada miembro se lee por sufijo a
  través del mismo registro que cualquier otro fichero, así que un `.exe` o un
  `.png` dentro del archivo **no se interpreta nunca como texto**.
- **Nada se escribe en disco.** Los miembros se leen en memoria, acotados por el
  límite de bytes por parte que ya existía, y se descartan.

## Limitación deliberada: un archivo es un documento, no uno por miembro

Los nombres de los miembros se indexan como texto y el contenido de cada uno va
detrás del suyo, así que buscar dentro del archivo funciona y buscar *por* el
nombre de un miembro también. Pero un acierto se atribuye al `.zip`, no al
miembro: la lista de resultados no puede decir qué fichero dentro|matcheó.

La alternativa — enumerar los miembros como documentos virtuales — necesita una
capa de proveedor, una ruta de contenido que entienda de rutas virtuales y una
acción de «abrir resultado» que materialice un miembro bajo demanda. Es un
cambio mayor que esta fase, y una versión a medias dejaría **abrir un resultado
roto**, que es peor que un límite documentado.

## Puerta de evidencia

`python -m evaluation.archive_gate`.

| Puerta | Umbral | Medido | Veredicto |
|---|---|---|---|
| T1 recall dentro del archivo | ≥ 1,00 | **1,00** (4/4) | PASS |
| T2 tokens hostiles indexados | 0 | **0** | PASS |
| T3 miembros rechazados declarados | ≥ 1 | **4** | PASS |
| T4 miembros binarios indexados | 0 | **0** | PASS |
| T5 contenido anidado indexado | 0 | **0** | PASS |
| T6 documentos etiquetados perdidos | 0 | **0** | PASS |
| T7 ms por archivo | ≤ 250 | **17,99** | PASS |
| T8 dependencias nuevas | 0 | **0** | PASS |
| T9 el límite de ratio aguanta | sí | **sí** | PASS |

**VEREDICTO: SHIP (9/9)**. Registro en `evaluation/archive_baseline.json`.

Lo que hace esta puerta y no hace una de lectura normal: **planta el ataque en
el mismo archivo que el contenido bueno**. Un solo `manual.zip` contiene los tres
miembros buscables, un `.exe`, dos rutas con traviesos (`../../windows/…` y
`/raiz/…`), un zip anidado y un miembro que se expande 407×. Las nueve puertas
miden a la vez que lo bueno se encuentra y que lo hostil no, de modo que las
reglas de rechazo no pueden pasar por suppressar contenido útil:

```
refused: unsafe member name: '../../windows/system32/travieso.txt'
refused: unsafe member name: '/raiz/equis.txt'
refused: part 'bomba.txt' exceeds the 100x expansion limit
        (1160000 bytes from 2855 compressed)
1 nested archive(s) not opened (no recursion)
1 non-text member(s) skipped
3 member(s) refused
```

## Un error mío, repetido

La primera ejecución de T6 dio **4 documentos perdidos** y era mentira. Medía
el «antes» sobre un índice que **ya incluía** el archivo, así que contaba como
perdidas cuatro consultas que nunca se habían encontrado —entre ellas
`zzz no existe`, que es ruido del propio corpus.

Es el mismo error que cometí en la fase 033, en la misma puerta, por el mismo
motivo: **medir el antes y el después sobre el mismo índice no mide nada**, da
cero por construcción o, si solo mides el final, inventa pérdidas. La corrección
es la misma y ahora está en las dos puertas: indexar el corpus solo, medir, y
**después** añadir el archivo.

Con la puerta correcta T6 da 0 perdidos y la MRR no se mueve: **0,8333 → 0,8333**.

## Un test mío que falló porque el código tenía razón

`test_resource_usage_reports_decompressed_bytes` fallaba esperando 5000 bytes
descomprimidos. El motivo era que mi contenido de prueba era `"a" * 5000`, que
comprime a ratio 250 y por lo tanto la comprobación de expansión lo rechazaba
—el comportamiento correcto—. Cambié el contenido a prosa realista.

Lo dejo escrito porque es el caso cómodo de confundir: un test que falla puede
ser el test equivocado, y en este caso lo era. La comprobación de ratio es lo
que impedía que un archivo hostil se materializara.

## Pruebas

`tests/test_archive_source.py`, **22 tests**:

- `.zip` queda registrado e inspeccionable; los directorios no son contenido
- el contenido de los miembros es buscable; **los nombres de los miembros
  también**, y cada uno va etiquetado (`=== nombre (bytes) ===`) para que un
  acierto en el texto se pueda atribuir a un miembro
- los nombres van a la estructura acotada
- los miembros binarios se saltan **sin decodificar**, y se dice
- un zip anidado no se abre, y se declara
- una ruta con traviesos, una ruta absoluta, un tamaño declarado que miente y un
  ratio de bomba: los cuatro se rechazan **y el contenido bueno del mismo
  archivo sigue leyéndose** (estado `PARTIAL`, nunca pérdida del resto)
- no se escribe nada en disco
- un archivo corrupto es error, no excepción; uno vacío es `NO_CONTENT`; uno
  inexistente también
- el contrato de recursos de la fase 025 sigue valiendo: entrada enorme
  rechazada antes de abrir, recuento de miembros acotado, cancelación
- `resource_usage.temp_bytes` informa de los bytes **realmente** descomprimidos

## Limitaciones

- **Un archivo es un documento** (arriba). Es la más importante de la fase.
- **No hay recursión**, así que un zip dentro de un zip no se indexa.
- **Solo `.zip`**. `.7z`, `.rar`, `.tar.gz` y demás quedan fuera de alcance: no
  están en la biblioteca estándar y añadirlos sería una dependencia nueva, que
  este programa no tiene.
- El texto de los miembros se limita por el **presupuesto de caracteres
  compartido** (2 MB): un archivo con muchos miembros se trunca y se declara
  `TRUNCATED`.
- No se indexan los metadatos del ZIP (comentario, fecha de cada miembro): solo
  nombre, tamaño y contenido.
