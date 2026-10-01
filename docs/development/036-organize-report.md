# Fase 036 — Agrupar, ordenar y búsquedas guardadas

## La regla que lo gobierna

**Organizar los resultados no puede cambiar qué resultados se obtienen.**
Ordenar y agrupar son presentación; en cuanto empiezan a esconder o a
promover documentos dejan de ser presentación y pasan a ser recuperación. Dos
consecuencias:

- **Un orden que no sea por relevancia amplía el conjunto candidato.**
  `--sort name` sobre 20 resultados no puede significar «los 20 primeros
  documentos que coinciden, alfabéticamente», porque la relevancia ya decidió
  cuáles son esos 20. Así que el conjunto se amplía por
  `SORT_POOL_MULTIPLIER` (5×) con un tope de 500, y **el número exacto de
  candidatos examinados se publica**. Sin esto, `--sort modified` devolvería en
  silencio los 20 documentos más relevantes ordenados por fecha, que parece un
  orden por fecha y no lo es.
- **Los empates nunca dependen del orden de entrada.** Cada orden termina en la
  ruta como último criterio, así que los mismos resultados salen siempre igual.

Las búsquedas guardadas son **configuración local**: sin tabla nueva, sin
migración, se borran con el fichero de configuración. Y llevan comando de
borrado, porque una función que solo añade deja basura que el usuario no puede
quitarse.

## Puerta de evidencia

`python -m evaluation.organize_gate`.

| Puerta | Umbral | Medido | Veredicto |
|---|---|---|---|
| T1 documentos de fuera del conjunto | 0 | **0** | PASS |
| T2 el orden es monótono | sí | **sí** | PASS |
| T3 los sin fecha van al final | sí | **sí** | PASS |
| T4 agrupar conserva los resultados | sí | **sí** | PASS |
| T5 el orden es reproducible | sí | **sí** | PASS |
| T6 el conjunto se amplía de verdad | sí | **8 → 40** (tope 500) | PASS |
| T7 ida y vuelta de una búsqueda guardada | sí | **sí** | PASS |
| T8 una búsqueda guardada se puede borrar | sí | **sí** | PASS |
| T9 cambio de MRR léxica | 0 | **0** (sigue en 0,8333) | PASS |

**VEREDICTO: SHIP (9/9)**. Registro en `evaluation/organize_baseline.json`.

## Dos correcciones a la puerta, y las dos importan

**La primera versión de T1 afirmaba algo falso.** Decía «organizar nunca cambia
qué documentos obtienes». Con 19 candidatos falló con 15 combinaciones
distintas, y la puerta tenía razón: pedir orden alfabético y recibir los 8
documentos más relevantes en orden alfabético **no es ordenar, es barajar**.
Elegir otro subconjunto *del conjunto candidato* es exactamente lo pedido.

La invariante real es más estrecha y es la que protege el mecanismo de ampliar
el conjunto:

- el conjunto candidato es el mismo pida lo que pida la presentación;
- **ningún documento mostrado sale de fuera** de ese conjunto;
- el orden por relevancia devuelve exactamente el orden del motor.

Y lo que sí es un coste de la función —cuántos subconjuntos cambia cada
reordenación: **15 de 12 combinaciones**— se **mide y se publica**, porque
ocultarlo sería la mentira.

**La consulta de la puerta era demasiado débil.** La primera versión usaba
`informe`, que en el corpus encuentra **tres** documentos, así que la puerta «el
conjunto se amplió» era técnicamente cierta y evidentemente inútil. Ahora usa
`de`, que devuelve **19 candidatos para una página de 8**.

## Pruebas

`tests/test_organize.py`, **34 tests**: el orden por relevancia no toca el orden
del motor; el nombre ordena sin distinguir mayúsculas; **los empates no
dependen del orden de entrada** (se comprueba invirtiendo la entrada); tamaño
descendente; **la fecha va de la más reciente a la más antigua** y los sin fecha
al final; un orden desconocido se rechaza con `ValueError` en vez de ignorarse;
el conjunto se amplía solo cuando el orden no es por relevancia y tiene tope;
agrupar por carpeta, tipo, fuente y fecha conserva todos los documentos y no
inventa grupos vacíos; el orden de los grupos no depende de su tamaño; una
fecha ilegible no acaba en un año absurdo; guardar, reemplazar sin duplicar,
conservando la posición original, y borrar; una búsqueda sin nombre nunca se
guarda; las entradas inservibles se ignoran en vez de ser fatales; y una
búsqueda guardada **no contiene rutas ni resultados**.

## Integración

- `SearchResult` gana `modified_at` y `size` (el SQL ya seleccionaba la fecha;
  ahora también el tamaño), que hacen falta para ordenar y para que la GUI
  pueda mostrarlos sin una segunda consulta por fila.
- CLI: `--sort {relevance,name,modified,size}`, `--group
  {none,folder,type,source,date}`, `--save NAME`, `--use NAME`,
  `--delete-saved NAME`.
- `query` pasa a ser posicional **opcional**, para que `--use` funcione solo; si
  no hay consulta ni búsqueda guardada, el error **lista las que hay**, que es
  más útil que el mensaje de argparse.
- Búsqueda guardada: **lo escrito en la línea de órdenes manda**. Un flag
  explícito nunca se ignora en silencio porque una búsqueda guardada dijera otra
  cosa.
- `saved_searches` en `AppConfig`: configuración local, sin tabla.

## Limitaciones

- **Agrupar solo imprime en la CLI.** La GUI todavía no tiene selector de
  orden ni de agrupación: es trabajo de 039, que es la fase de interfaz.
- **No hay búsqueda guardada por contexto**: guardar guarda consulta, orden,
  agrupación y filtros, pero no el `Context` activo.
- **Agrupar no agrupa el índice**, solo la salida: no hay facetas sobre todo el
  índice, solo sobre los resultados de una página.
- El conjunto ampliado son 5× el tope de la página, con un máximo de 500. Con un
  índice enorme, `--sort` sobre una página pequeña sigue mirando solo 500
  candidatos, y eso **no** se dice en la salida del CLI.
