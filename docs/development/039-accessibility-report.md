# Fase 039 — Accesibilidad e interfaz

## La regla que lo gobierna

**Una afirmación sobre accesibilidad que no se puede comprobar con una tecla es
decoración.** «Esta ventana se puede usar con el teclado» sobrevive a años de
revisión sin que nadie pulse una tecla, porque en una captura de pantalla no se
aprecia la diferencia. Así que cada puerta de esta fase es un instrumento, y
las tres preguntas del plan tienen un medidor:

1. **¿Se puede usar sin ratón?** `focus_report` recorre el árbol de widgets como
   hace el Tab y dice qué es alcanzable y qué no.
2. **¿Tiene cada control un nombre?** `name_report_for` lee los widgets
   construidos y dice qué anunciaría un lector de pantalla.
3. **¿Se puede leer?** `contrast_report` calcula las razones de contraste WCAG
   2.1 de los pares que la ventana dibuja de verdad.

Tk no tiene API de accesibilidad, así que (1) y (2) se resuelven leyendo los
widgets. Es honesto sobre sus límites y funciona en cualquier máquina; fingir
más sería una respuesta peor.

## Puerta de evidencia

`python -m evaluation.accessibility_gate`

| Puerta | Umbral | Medido | Veredicto |
|---|---|---|---|
| T1 controles inalcanzables con teclado | 0 | **0** | PASS |
| T2 controles sin nombre accesible | 0 | **0** | PASS |
| T3 cadenas visibles fuera del catálogo | 0 | **0** | PASS |
| T4 fallos de contraste WCAG AA | 0 | **0** | PASS |
| T5 colores de la paleta nunca dibujados | 0 | **0** | PASS |
| T6 errores que parecen un estado normal | 0 | **0** | PASS |

**VEREDICTO: SHIP (6/6)**. Registro en `evaluation/accessibility_baseline.json`.

Nombres accesibles declarados, leídos de la ventana real:

```
Entry=Búsqueda  Combobox=Contexto:  Combobox=Fuente:
Combobox=Tipo:  Menubutton=Recientes ▾  Listbox=Resultados
```

Contraste medido: 7 pares × 2 temas, cuerpo de texto de **5,96:1** a **17,22:1**
en claro y de **6,90:1** a **13,45:1** en oscuro. Todos por encima del 4,5:1
de AA.

## Los cuatro defectos que encontró

**Cuatro controles dependían del foco por defecto de la plataforma.** Los tres
combobox y el botón de recientes no declaraban `takefocus`, así que su
alcanzabilidad dependía del estilo del sistema. La entrada y la lista ya lo
hacían explícito desde la fase 017; estos cuatro no. Corregido, y ahora un test
falla si un control interactivo vuelve a depender del valor por defecto.

**Un error se dibujaba exactamente como un estado normal.** `_set_status`
pintaba todo en el gris apagado, así que «No se pudo abrir el archivo» era
visualmente idéntico a «Listo — escribe para buscar». `danger` y `busy`
estaban declarados en ambas paletas y **no los dibujaba ningún widget**. Ahora
`_set_status` acepta severidad y hay nueve llamadas marcadas.

**Dos colores muertos en la paleta.** `surface` estaba en `Theme`, en `LIGHT` y
en `DARK`, y ningún widget lo usaba. No se le inventó un requisito de contraste:
se eliminó, y T5 vigila para que no vuelva a colarse un color que nada dibuja.

**`cget("text")` devuelve el nombre de la variable Tcl, no el texto.** En un
widget `ttk` configurado con `textvariable=`, `cget("text")` responde `PY_VAR16`.
El primer borrador de `accessible_name` anunciaba eso como nombre accesible,
que es peor que no anunciar nada. Y un combobox que muestra «(todos)» no se
llama «(todos)»: se llama «Tipo:».

## Por qué los nombres se declaran y no se deducen

Tk no tiene `aria-label` ni `labelwidget`: una `Label` junto a un control sólo
está relacionada con él por posición, y en esta ventana **tres etiquetas
comparten el mismo marco padre** («Contexto:», «Fuente:», «Tipo:»). Cualquier
heurística posicional nombraría los tres filtros «Contexto:».

Así que la asociación se **declara** donde se construye el widget
(`declare_name`), y la auditoría falla si a un control interactivo le falta una.
Ese es también el dato que consumiría un puente real de lector de pantalla, así
que la información está donde debe estar.

El campo `note` del catálogo se eliminó por el mismo motivo: la clave ya dice
dónde aparece la cadena (`MENU.INDEXER.PAUSE`), y una segunda copia de esa
información es una segunda copia que se olvida.

## El catálogo, y por qué es lo que mantiene todo esto

`src/universal_search/gui/strings.py`, **86 entradas**. Todas las cadenas
visibles de `app.py` y `control_center.py` salen de ahí, y
`untranslated_literals()` encuentra por AST cualquier literal visible escrito
en línea. Un botón nuevo con su etiqueta en crudo falla la suite en vez de
publicar texto intraducible.

Dos correcciones al propio detector, ambas encontradas al usarlo:

- La primera versión comparaba el **nombre de la llamada** con los nombres de
  palabra clave (`text`, `label`…), así que **nunca miró una etiqueta de
  widget**: sólo se comprobaban las llamadas a `_set_status`. La regla trata del
  *argumento*, no del constructor: cualquier llamada que recibe `text=` está
  mostrando algo, sea un `ttk.Button` o lo que sea.
- El detector escaneaba el AST y sus `col_offset` son **desplazamientos en
  bytes UTF-8**, no en caracteres; con acentos y comillas tipográficas
  corrompía el fichero. El script se negó a escribir porque valida con
  `ast.parse` antes de escribir nada, que es lo único que evitó un desastre.

## Pruebas

`tests/test_accessibility.py`, **34 tests** (1 se omite donde la ventana no tiene
foco del sistema operativo, con el motivo). Cobertura del catálogo y que cada
módulo de la GUI está comprobado; sin claves ni valores duplicados; las claves
dicen qué es la cadena y no lo que dice; una clave ausente es ruidosa; un
placeholder ausente **o mal escrito** es error, porque `str.format` pondría un
`{count}` literal en pantalla sin quejarse; contraste que coincide con los puntos
de referencia de la especificación (negro sobre blanco = 21:1, simétrico); **todo
color que la ventana dibuja está comprobado**; AA en los dos temas; recorrido
completo con teclado —escribir, bajar, abrir— sin un solo `invoke()` ni un
`<Button-1>`; anillo de foco que empieza en la entrada y llega a la lista; cada
control con `takefocus` explícito; cada filtro con su propio nombre; los nombres
vienen del catálogo; y un error se dibuja como un error.

**Deliberadamente no se prueba la puerta dentro del proceso.** La primera
versión llamaba a `accessibility_gate.main()` en línea, y esa ventana destruida
dejaba el intérprete de Tcl incapaz de crear otra («Can't find a usable
init.tcl»): catorce tests de este mismo fichero se convertían en omitidos sin
que nadie se enterara. La puerta es un comando y se ejerce como tal, en un
subproceso.

## Limitaciones

- **No hay puente de lector de pantalla.** Los nombres se declaran y se
  comprueban, pero Tk no los anuncia: no hay integración con Narrator ni con
  NVDA. Lo que hay es la información preparada y verificada donde un puente
  podría usarla.
- **El orden de Tab se comprueba por el orden de empaquetado**, que es lo que
  sigue Tk. Un recuadro de diálogo del sistema, con su propio orden, no se
  comprueba.
- **El contraste se comprueba para los pares que la ventana dibuja**, no para
  combinaciones arbitrarias. Un texto rojo sobre la selección azul, por ejemplo,
  no existe en esta interfaz y por tanto no se mide.
- **La puerta T1, T2 y T6 necesitan una ventana real.** Donde Tk no arranca, se
  informan como NO EJECUTADO en vez de darse por buenas: una puerta de
  accesibilidad que no pudo abrir una ventana no ha comprobado nada sobre esa
  máquina.
- **El tamaño de fuente del sistema no se lee.** Los temas usan Segoe UI a 10 y
  14 puntos; si el usuario ha configurado un tamaño muy grande en Windows, el
  contraste del texto no cambia pero el diseño puede desbordarse. La escala de
  interfaz de la 017 cubre parte de esto, no todo.
- **No se midió con un lector de pantalla real**, porque no había ninguno
  disponible en esta máquina. Es la comprobación que faltaría.