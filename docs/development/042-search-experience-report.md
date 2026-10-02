# Fase 042 — Experiencia interactiva de búsqueda

Estado: implementada. Puerta: `python -m evaluation.interaction_gate`.
Fase anterior: `041-product-experience-report.md`.

## Qué se encontró antes de escribir una línea

Las cuatro capacidades que esta fase debe integrar —lenguaje de consulta
(012), búsqueda difusa (031), sugerencias verificadas (032) y
agrupación/ordenación/guardadas (036)— **funcionan**. Cada una tiene su puerta y
su puerta está en verde. Lo que ninguna podía comprobar es la **costura**: si la
ventana las llama, y si lo que llega al usuario es lo mismo que se midió debajo.

Al cruzar la lista de piezas con la ventana, seis estaban **existiendo y sin
llegar nunca a la pantalla**:

| Pieza | Dónde estaba | Por qué no se veía |
|---|---|---|
| `SearchService.last_query_error` | se escribía, nadie lo leía | — |
| `organize.sort_results` / `group_results` / `pool_size` | el núcleo entero | la ventana no las importaba |
| `SavedSearch` y sus seis campos | persistidos en `config.json` | la CLI los tenía; la ventana no |
| `QuerySuggester` | implementado y verificado | ningún `import` en `gui/` |
| `explain=True` | el ranking lo calcula desde la fase 004 | **nadie lo pedía jamás** |
| `FuzzySearchEngine` | el CLI lo monta desde la 031 | el servicio se cableó una vez y no se revisó |

Y un defecto vivo, que es el que da nombre a la fase:

> **Un error de consulta no llegaba al usuario.**
> `SearchService.search` se traga el `QueryError` en `last_query_error` y
> devuelve `[]`. El `except QueryError` del hilo de la ventana nunca se
> ejecutaba. El estado «Consulta no válida» que la fase 041 construyó —y que su
> puerta U4 daba por bueno— **era código muerto en producción**.

Esto no lo encontró una lectura: lo encontró una prueba escrita para no simular
nada. Las pruebas de la 041 monkeypatchean `service.search` para que lance, y
eso demuestra que **el manejador** funciona; no que la aplicación llegue a él.
La prueba de la 042 manda una consulta mal formada por el servicio real, el
motor real y el analizador real, y exige verla en la superficie de mensajes.

## Qué se ha hecho

### La costura: `search_or_error`

`SearchService.search` **se mantiene igual** — su contrato está fijado por
`test_advanced_search.py` y por la CLI, que imprime después. Se añade
`search_or_error(query, …) -> (resultados, error | None)`, y `search` pasa a ser
una envoltorura de una línea sobre ella. Una implementación, dos entradas.

La razón de no leer `last_query_error` en la línea siguiente es una carrera, y
es real: **cada pulsación corre en su propio hilo contra la misma instancia del
servicio**. Entre que la búsqueda termina y que se lee el campo, otra pulsación
puede haberlo reescrito. La puerta V1 mide exactamente esto.

### La capa difusa, en la ventana

El servicio monta ahora la pila que monta la CLI: léxico → híbrido → difuso, con
`config.fuzzy_enabled` para desactivarla. `_apply_fuzzy` es el único sitio donde
se ensambla la pila, en las dos direcciones.

### Ordenar y agrupar, con el vocabulario de la 036

Dos desplegables junto a los filtros. Ordenar y agrupar **reeordena lo que ya
está en pantalla**, que es gratis. La excepción: un orden distinto de relevancia
sólo puede elegir entre el conjunto que le dieron, así que ahí **se vuelve a
preguntar con el conjunto ampliado** — que es lo que hace la CLI — en vez de
reordenar en silencio un conjunto truncado y llamarlo orden.

Agrupar usa filas de encabezado con un id que ningún índice de resultado puede
ser (`g:0`), así que todos los ayudantes de selección lo saltan preguntando «¿es
esto un índice?».

### Búsquedas guardadas, historial y sugerencias

- **Guardadas**: guardar, aplicar y borrar desde el menú *Buscar*. Aplicar
  restaura consulta, orden, agrupación y los dos filtros: los seis campos que la
  puerta de la 040 exige, y ni uno más.
- **Historial**: mostrar (con las reglas de retención escritas al lado), borrar,
  y desactivar. Desactivar **no borra**: son dos intenciones distintas, y quien
  pausa una semana debe encontrar su historial intacto.
- **Sugerencias**: se ofrecen en el estado vacío y se aplican con `Alt+Intro`.
  Poner la sugerencia es `self.query_var.set(...)`, que es exactamente lo mismo
  que teclear: no hay un segundo camino para ejecutar una consulta.

Las reglas de retención eran explícitas en el código (un tope y un límite de
longitud) e implícitas en todas partes. Ahora hay `MAX_QUERY_CHARS` con nombre, y
la ventana dice los dos números y el fichero donde viven.

### Explicaciones, sólo cuando se piden

`explain=True` cuesta trabajo por resultado, así que es un interruptor en *Ver*,
desactivado por defecto. El panel de detalle muestra la cuenta del ranking —cada
señal con su valor— más las notas y el fragmento.

### Actualización incremental, y contada

`_render` ya no borra cincuenta filas y las reescribe en cada pulsación. Cada fila
se identifica por su posición; la que no cambió **no se toca**. Los contadores
existen porque una afirmación de incrementabilidad sin medir no es una
afirmación: la puerta V10 mide que reordenar N resultados no reconstruye ninguna
fila.

## Defectos reales encontrados por las pruebas de esta fase

Cuatro. Ninguno era un problema de aspecto; tres eran costuras rotas.

1. **`explain` no era uniformemente numérico.** La anotación decía
   `dict[str, float]` y la capa difusa mete `fuzzy_matches`, una lista de
   diccionarios. No se rompía porque nadie leía el campo; al ponerlo en pantalla
   reventó con `abs(): 'list'`. La lista está fijada por un test de la 031, así
   que lo que se corrige es **la anotación** (`dict[str, object]`) y la
   presentación, que dice qué hace con un número y con lo que no lo es.

2. **La capa difusa rompía el aprendizaje local, en silencio.**
   `FuzzySearchEngine` expone `search` y `database`, y nada más. Al envolver con
   ella, `record_open` lanzaba `AttributeError`, lo tragaba el `except` del
   servicio, y **el aprendizaje local dejaba de funcionar** para todo el mundo
   con el único síntoma una línea de registro que nadie lee. Se añadieron las tres
   delegaciones que `HybridSearchEngine` ya tenía. El wrapper sólo es transparente
   si responde a todo lo que responde lo que envuelve.

3. **Leía una variable de Tk desde el hilo worker.** `explain_var.get()` dentro
   de `work()` lanzaba `main thread is not in main loop` y convertía **cada
   búsqueda** en un fallo duro en cuanto alguien pedía explicaciones. Se lee
   ahora en el hilo principal, como las otras variables.

4. **`_on_view_changed` comparaba contra el sitio equivocado.** Comparaba
   `requested_limit <= limit` en vez de contra `pool_size(limit, relevance)`, y
   con un `limit` cambiado a mano la condición no se cumplía y el conjunto no se
   ampliaba. Lo encontró la puerta V6 en su primera ejecución.

Y uno más, de diseño, que conviene tener presente:

5. **La 031 y la 042 se solapan.** Con la capa difusa activa, `capacitos`
   encuentra resultados y **no hay nada que sugerir**: el corrector difuso responde primero. La sugerencia queda como segunda línea de defensa, que es
   donde debe estar. La puerta V3 lo mide con la capa desactivada, y hay una
   prueba que fija el orden con la capa activada. Documentarlo era obligatorio;
   fingir que la sugerencia es la primera línea habría descrito una
   configuración que nadie ejecuta.

## La puerta: `python -m evaluation.interaction_gate`

Once invariantes, umbrales en cero, un número cada una.

| Puerta | Pregunta | Resultado |
|---|---|---|
| V1 | ¿llega al usuario una consulta rechazada? | sí, por el servicio real, en `danger` |
| V2 | ¿un filtro explícito desactiva las capas? | 0 fugas en las 5 fuentes |
| V3 | ¿una sugerencia cambia la consulta sola? | no; sólo al pedirla |
| V4 | ¿ordenar cambia cuáles resultados son? | no: mismo conjunto, otro orden |
| V5 | ¿agrupar pierde o inventa resultados? | 0 de 0 |
| V6 | ¿se ordena sobre un conjunto truncado? | no: pide 25 donde relevancia pedía 5 |
| V7 | ¿funcionan los controles de historial? | los 5, con retención declarada |
| V8 | ¿una guardada tiene campos de más? | los 6 exactos, y restaura completa |
| V9 | ¿se calculan explicaciones sin pedirlas? | 0 sin pedir, 2 al pedir |
| V10 | ¿se rehicen filas que no cambiaron? | 0 |
| V11 | ¿de pulsación a resultado cabe? | 185 ms (presupuesto 600 ms) |

**11/11 SHIP, salida 0.** El veto de carga de la 038 se comprobó antes de medir:
CPU al 51%, por debajo del 60%. La 041 midió 98,5 ms para 50 filas con el veto
disparado al 69%, así que su U9 sigue **INCONCLUYENTE** y no se ha tocado.

## Lo que esta fase NO hace

- **No toca la semántica.** `parse_query`, `translate`, el `Ranker`, los pesos y
  los cortes de FTS están intactos; `test_query_parser`, `test_ranking` y
  `test_evaluation` siguen verdes sin tocar una aserción.
- **No reimplementa nada de 031, 032 ni 036.** Ordenar es `organize.sort_results`,
  agrupar es `group_results`, sugerir es `QuerySuggester`, guardar es
  `organize.save_search`. Este commit añade superficies, no algoritmos.
- **No añade dependencias.** Ni una; el presupuesto sigue siendo `pypdf` y
  `watchdog`.
- **No toca la puerta de la 040** más que para contar pruebas.
- **No agrupa por relevance.** Elegir eso es decidir qué significa «orden por
  relevancia», y es una decisión de la 045, no de la 042.

## Limitaciones

1. **Guardar y borrar usan `simpledialog.askstring`.** Un diálogo de una línea
   con el nombre de la búsqueda guardada. Es coherente con el resto de la
   aplicación, que no tiene un diálogo propio, y es menor de lo que un gestor
   completo merecería. Declarado, no escondido.
2. **El historial se muestra en una ventana de sólo lectura.** No se edita ni se
   borra desde ahí; se borra desde el menú. Lo que la fase pide es inspeccionar,
   y eso está.
3. **No hay retención por tiempo.** Las entradas no llevan marca de tiempo, así
   que un TTL sería un cambio de esquema con migración, no un ajuste. Lo que sí
   hay es tope de cantidad y de longitud, y está escrito en la interfaz.
4. **La capa difusa se puede cambiar en caliente**, lo que descarta el
   constructor del sugerente porque verificó contra el motor anterior. Está
   comprobado con una prueba.
5. **La ventana de diagnóstico y la de relacionados** siguen sin rediseñar.

## Verificación

- `python -m evaluation.interaction_gate` → **11/11 SHIP**, salida 0.
- `python -m evaluation.accessibility_gate` → **SHIP**, 160 entradas catalogadas,
  8 pares de contraste.
- `python -m evaluation.gate` → PASS una vez actualizado el recuento.
- pyflakes limpio.
- Pruebas ejecutadas: sólo las que tocan lo modificado, por indicación expresa
  (la ventana, el servicio, la configuración, el motor, el analizador y las
  puertas). Recuento total en `README.md`.

## Para la 043

La 043 es «ajustes y configuración tipados». Le deja directamente:

- `AppConfig.fuzzy_enabled` como primer ajuste nuevo que se puede cambiar sin
  reiniciar, con su opción en la interfaz;
- un patrón ya establecido en esta fase: la acción vive en el servicio, la
  ventana sólo llama, y `set_*` es idempotente;
- y tres piezas que la 043 querrá mover a un sistema de ajustes y que hoy están
  repartidas: `ui_scale`, `theme` y `recent_queries_enabled`.
