# Fase 041 — Experiencia de producto

Estado: implementada. Puerta: `python -m evaluation.ux_gate`.
Informe de la puerta anterior: `040-v3-quality-gate-report.md`.

## Qué decide esta fase

Que la aplicación se comporte como un producto y no como una utilidad con
buenos resultados dentro.

El diagnóstico no fue subjetivo. Fue una auditoría de la ventana, línea por
línea, contra el prompt de la fase. Lo que encontró:

- **El panel de resultados no tenía jerarquía.** Era un `Listbox` con una
  cadena por fila: `[fuente] nombre · TIPO · carpeta — coincidencia`. Cinco
  cosas distintas en una línea, sin separador fuerte, sin encabezados, y
  **recortadas por el borde del widget**. Un nombre de fichero largo no se
  acortaba ni se indicaba: simplemente desaparecía.
- **«Sin resultados» era una línea de estado y un rectángulo vacío.** La mayor
  región de la ventana no decía nada en el estado que un usuario encuentra más
  a menudo después de escribir algo que no existe.
- **La caja de búsqueda no decía qué era.** Tenía nombre accesible
  (`SEARCH.PLACEHOLDER_OR_LABEL`) y nada visible.
- **`ui_scale` escalaba la tipografía y nada más.** Los rellenos eran literales
  `(12, 12, 12, 6)`. Un usuario que pedía texto más grande recibía palabras más
  grandes dentro del mismo hueco apretado.
- **Había dos respuestas a «qué tipo de archivo es esto».** `rows.extension_label`
  decía `DOCX`; un `TYPE_LABELS` en la ventana decía `Word`. La fila y el panel
  de detalle podían contradecirse.
- **Nueve líneas de estado visibles eran f-strings**, invisibles para el auditor
  de la 039 porque un `JoinedStr` no es un literal.

Y dos defectos, no sólo problemas de aspecto:

- **Una consulta rechazada se dibujaba como una línea de estado normal.** El
  gemelo exacto del defecto que la 039 corrigió en *su* ruta de error, y que
  dejó en la otra.
- **Un fallo de búsqueda dejaba los resultados anteriores en pantalla.** El
  usuario acababa de escribir algo nuevo, la búsqueda falló, y la lista seguía
  mostrando la respuesta de la consulta anterior como si fuera la respuesta.

## Qué se ha hecho

### El panel de resultados: de una cadena a cinco columnas

`tk.Listbox` → `ttk.Treeview` con columnas reales: **Nombre, Carpeta, Tipo,
Fuente, Coincidencia**, con encabezados visibles. Es la forma que usan las
aplicaciones de escritorio de Windows: lista arriba, detalle abajo.

La decisión no es estética y está medida. Un `Listbox` no puede expresar
jerarquía: una fila es una cadena. Un lienzo (`Canvas`) sí podría, pero no está
en la lista de controles interactivos de la 039, así que **el panel que lleva
todo el producto quedaría fuera de todos los instrumentos de accesibilidad que
este proyecto tiene**. `Treeview` está en esa lista, de modo que la superficie
principal es también la que la puerta mide. Un panel más flexible y ciego no es
una mejora.

Lo que un `Treeview` **no** puede hacer, y conviene decir: la tipografía es por
fila, no por columna. La jerarquía viene del orden de las columnas, de sus
anchos, de los encabezados y del panel de detalle. Se eligió sabiéndolo.

La columna **Fuente desaparece** cuando todos los resultados son locales, que es
la regla que ya existía para la fila («una segunda columna de *local* en cada
fila es ruido») aplicada ahora al nivel de columna.

### El panel de detalle: tres líneas con tres oficios

Lo que la fila no cabe. **Qué es** (nombre · tipo · fuente, en `detail`),
**dónde está** (la ruta completa, en la tipografía monoespaciada que existía en
el tema desde la 017 y no usaba nadie), **por qué coincidió** (la coincidencia
entera, con ajuste de línea al ancho de la ventana). Antes era una sola etiqueta
sin `wraplength`, que recortaba en horizontal.

### Los estados vacíos son un widget

Un área que sustituye al panel cuando no hay nada que listar, con título y
pista, en tres situaciones: **nada escrito**, **cero resultados** y **fallo**.
Una sola superficie, tres mensajes coherentes, todos catalogados. El estado
inicial ya no es un rectángulo vacío: dice qué es esto y cómo se usa.

### Los huecos vienen del tema

`theme.Spacing` y `theme.spacing(escala)`. Todo relleno, alto de fila y ancho de
columna fija salen de ahí. Una prueba AST falla si reaparece un literal de
relleno distinto de cero.

### Un solo lugar que sabe qué tipo es un archivo

`rows.type_label()` y su tabla. `TYPE_LABELS` desaparece de la ventana y
`rows.format_result_row` desaparece del módulo: sin callers, un formateador es
un segundo sitio donde cambiar la próxima fila.

### Las nueve f-strings, al catálogo

Y con ellas el diálogo de confirmación de «olvidar documentos» — la cadena más
importante para la seguridad que el usuario debe leer antes de autorizar un
cambio en su índice, y que estaba enterrada en medio de un f-string.

## Defectos reales encontrados por las pruebas de esta fase

Estos no se buscaron; los encontraron pruebas que se escribieron para afirmar
cosas distintas:

1. **`Treeview.selection_clear()` sin argumentos no borra nada.** Tcl lee la
   lista de ítems ausente como «no hay nada que borrar». Con eso, arrowing por
   los resultados **acumulaba** selección en lugar de colapsarla, y el panel de
   detalle seguía mostrando el primero. Está medido en
   `tests/test_ux.py::test_the_pane_binds_the_keys_and_the_detail_follows`.

2. **`Treeview.bbox()` sólo describe filas visibles**: devuelve cadena vacía
   para una fila desplazada fuera de pantalla y lanza `TclError` para una que
   no existe. El tamaño de página se calculaba con la última fila, así que con
   200 resultados devolvía siempre el valor de reserva. Ahora cuenta hacia
   arriba desde la primera hasta que el panel dice «no más».

3. **La puerta de la 039 dijo que `accent` dejaba de dibujarse** en cuanto el
   `Listbox` desapareció (su `highlightcolor`). La fase 039 lo detectó por su
   propia regla, y no se resolvió inventando un uso: `accent` dibuja ahora los
   encabezados de columna y el borde del campo de búsqueda al enfocar, y se
   añadió el par de contraste nuevo que eso implica.

4. **La ventana de diagnóstico quedó fuera del sistema de espaciado** con
   `padx=8, pady=8` literales y un título en línea. Lo encontró la prueba AST
   de rellenos.

5. **La regla del *placeholder* no se podía probar.** Dependía de
   `focus_get()`, que la 039 ya declaró no afirmable desde un test: pasa en la
   máquina de quien desarrolla y falla en CI. Se extrajo a
   `gui_app.placeholder_visible(query, focused)`, que es una regla.

## La puerta: `python -m evaluation.ux_gate`

Nueve invariantes, umbrales en cero, todos con su número.

| Puerta | Pregunta | Resultado |
|---|---|---|
| U1 | ¿cada parte de un resultado tiene su columna con nombre? | 0 sin columna — 5 columnas, 5 encabezados |
| U2 | ¿el área vacía dice algo? | título y pista presentes |
| U3 | ¿el recorrido necesita ratón? | 0 de 6 pasos lo necesitan |
| U4 | ¿un fallo se parece a un estado normal? | 0 de 2 rutas |
| U5 | ¿un fallo deja respuestas viejas? | 0 de 2 rutas |
| U6 | ¿hay algo recortado e inalcanzable? | 0; detalle con 149 de ruta y 647 de coincidencia |
| U7 | ¿los huecos siguen a la escala? | sí: fila 26 px, padding 12 px, 14 pt |
| U8 | ¿cambiar de tema se ve en el panel? | 6 de 6 roles con color propio |
| U9 | ¿rellenar 50 filas cabe en el presupuesto? | **INCONCLUYENTE** — ver abajo |

### U9: inconclusa, y por qué no se maquilla

El veto de carga de la 038 se reutiliza tal cual: `measure_load()` +
`os_cpu_load()` + `load_verdict()`. En esta máquina el veto se dispara porque
hay un cliente de juego (`LeagueClient`, `TFTClient`) consumiendo CPU, y la
puerta sale con **código 2**, el mismo de «no lo sé» de la 038.

Lo que **sí** se midió, y va en el informe porque un número sin leer es peor que
un número:

- insertar 50 filas: **2,5–5,6 ms**;
- `selection`/`focus`/`displaycolumns`: **0,2 ms**;
- un `Treeview` sin estilo propio, 50 filas enteras: **0,8–1,1 ms**;
- el mismo árbol con `rowheight`, `font` o `fieldbackground` por separado:
  **4,4–8,8 ms** cada uno;
- el ruido de la máquina durante la medición: **±90 ms**.

Conclusión: el coste del panel es del orden de 20–30 ms por página de 50
resultados y es inherente a dar estilo propio al árbol (que es lo que hace que
`rowheight` —la densidad compacta que pide el prompt— y el tema oscuro existan).
No es un problema. Lo que no era medible en esta máquina es el total, y eso se
declara en vez de publicarse.

## Lo que esta fase NO hace

- **No toca el motor.** Ni backend, ni ranking, ni proveedores. Las 041–044 son
  de usuario; el motor se mide en la 045.
- **No reconstruye la 039.** Se extiende: `declare_name` sigue declarando
  nombres, `focus_order` sigue midiendo el anillo, `CONTRAST_PAIRS` crece en un
  par porque hay ahora un par dibujado de verdad.
- **No toca `services.indexer_summary`**, que sigue con sus etiquetas de estado
  en español dentro de la capa de servicio. Está declarado como limitación, no
  escondido: es línea de estado, no flujo principal.
- **No implementa el ajuste de tema en caliente.** Elegir tema requiere reiniciar
  la ventana; ya era así.

## Limitaciones

1. **U9 no está cerrada** en esta máquina. Se cierra sola cuando el veto deje
   de dispararse; el mismo camino que siguió la puerta 038.
2. **Bajo el estilo nativo `vista`, ttk ignora algunas opciones de estilo.**
   Que `style.lookup` devuelva el valor configurado no prueba que el *widget* lo
   pinte. Está medido para `fieldbackground`/`background`/`foreground` en los
   tres estilos disponibles (una sonda temporal, no un test), pero la
   comprobación visual es manual y está en
   `041-windows-ux-checklist.md`.
3. **La tipografía es por fila, no por columna.** Consecuencia directa de elegir
   `Treeview`, explicada arriba.
4. **El panel de detalle ocupa alto vertical** mientras hay selección. Es un
   intercambio: se ve más de cada resultado a cambio de menos filas visibles.
   No medido contra la alternativa.
5. **La ventana de diagnóstico y la de relacionados** se trajeron al sistema de
   espaciado y al catálogo, pero no han sido rediseñadas: son superficies
   secundarias y esta fase es el flujo principal.

## Verificación

- `python -m evaluation.ux_gate` → **8 PASS, U9 INCONCLUYENTE, salida 2**.
- `python -m evaluation.accessibility_gate` → **6/6 SHIP**, con 8 pares de
  contraste en vez de 7.
- `python -m evaluation.gate` → **PASS**, los 23 invariantes.
- Suite completa y pyflakes: los recuentos están en `README.md`.

### Flakes preexistentes, documentados y no escondidos

Con el equipo saturado por procesos ajenos, fallan de forma intermitente tres
tests que lanzan procesos reales. **No los subió esta fase y no se les cambió un
umbral para disimular.** Se dejan escritos con la evidencia:

| Test | Qué pasa | Evidencia |
|---|---|---|
| `test_worker_keeps_index_current_and_stops_cleanly` | el worker tarda más que su espera | pasa aislado |
| `test_start_stop_and_no_duplicate_process` | el hijo sale con `3221225786` (`0xC000013A`, terminado) | aislado: **2 de 3 intentos pasan** |
| `test_process_death_releases_the_tray_process_lock` | bloqueo de proceso bajo carga | pasa aislado |
| `test_concurrent_starts_only_claim_the_generation_they_presented` | generación perdida bajo carga | pasa aislado |

Ninguno toca `background.py`, `indexer.py` ni `tray.py`, que esta fase no
modificó. Es el mismo patrón que la 020 dejó escrito como limitación con nombre,
y que la 038 resolvió declarando la puerta inconclusa en vez de subir el
número.

## Para quién sea que decida sobre 042–050

La 042 («búsqueda como experiencia interactiva») empieza con el panel que esta
fase deja. Lo que hereda y conviene saber antes de tocarla:

- cinco columnas con nombres, una de las cuales se oculta sola;
- un área de mensaje única para vacío, cero resultados y fallo;
- una regla pura (`placeholder_visible`) en vez de lógica de widget;
- y un panel de detalle de tres líneas que se rellena con un `SearchResult` y
  tres llamadas a `rows`.
