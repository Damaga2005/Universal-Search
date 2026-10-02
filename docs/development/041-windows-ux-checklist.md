# Fase 041 — Lista de verificación de UX en Windows

El prompt de la fase pide una lista reproducible de UX en Windows y probarla.
Este documento es esa lista, y dice para cada punto **qué lo cubre
automáticamente** y **qué queda por hacer con una persona delante de un
escritorio**.

Las tres Tools que valen:

| Herramienta | Qué contesta |
|---|---|
| `python -m evaluation.ux_gate` | nueve invariantes medidas, con número |
| `python -m evaluation.accessibility_gate` | teclado, nombres, contraste, catálogo |
| `python -m pytest tests/test_ux.py` | el comportamiento, sin ventana |

## Cómo se ejecuta

Con la aplicación instalada y **un índice real** (al menos unas cientos de
documentos, incluidos PDF y Office), porque varios puntos sólo se ven con
contenido que no cabe:

```
python -m evaluation.ux_gate
python -m evaluation.accessibility_gate
packaging\build.ps1
```

La lista está pensada para **Windows 11, 22H2 o posterior**, con la
configuración por defecto y sin herramientas de desarrollo abiertas.

---

## A. Sólo teclado

| # | Paso | Qué se espera | Automático |
|---|---|---|---|
| A1 | Abrir la ventana y **no tocar nada** | El cursor está en la caja de búsqueda y se puede escribir | U3 |
| A2 | Escribir cuatro letras | El *placeholder* desaparece al primer carácter | ✅ prueba |
| A3 | `Tab` cinco veces | El orden es: búsqueda → contexto → fuente → tipo → recientes → resultados | U3, T1 |
| A4 | `Abajo` desde la caja | La selección se mueve **y el panel de detalle la sigue** | ✅ prueba |
| A5 | `Re Pág` / `Av Pág` | Salta lo que se ve en pantalla, no 10 filas fijas | ✅ prueba |
| A6 | `Inicio` / `Fin` | Primero y último resultado | ✅ prueba |
| A7 | `Intro` | Abre el resultado seleccionado | ✅ prueba |
| A8 | `Ctrl+Intro` | Lo muestra en el Explorador | ✅ prueba |
| A9 | `Ctrl+C` con varios seleccionados | Copia todas las rutas, una por línea | ✅ prueba |
| A10 | `Esc` con texto | Borra la consulta; `Esc` otra vez | cierra | U3 |

**Comprobación manual que no la cubre ninguna puerta**: que el anillo de foco
*se vea*. Un `Treeview` de ttk bajo el estilo nativo `vista` dibuja su propio
indicador, y nadie ha comprobado nunca qué aspecto tiene en esta máquina.

## B. Ratón

| # | Paso | Qué se espera | Automático |
|---|---|---|---|
| B1 | Clic en un encabezado de columna | Ordena; el texto del encabezado lo indica | ❌ **no implementado** |
| B2 | Clic simple | Selecciona y rellena el detalle | ✅ |
| B3 | `Ctrl`+clic | Añade a la selección | ✅ |
| B4 | `Mayús`+clic | Extiende la selección | ✅ |
| B5 | Doble clic | Abre | ✅ |
| B6 | Arrastrar el borde inferior del panel de detalle | Lo cambia de alto | ❌ el panel no es reescalable |

> B1 está declarado como **fuera de alcance** en esta fase. El prompt pide
> jerarquía y presentación compacta, no ordenación; y ordenar exigiría decidir la
> semántica de «orden por relevancia», que es una decisión de la 045, no de la
> 041. Anotado en el informe como limitación con nombre.

## C. DPI alto

| # | Paso | Qué se espera | Automático |
|---|---|---|---|
| C1 | Escalar el monitor al 150 % y al 200 % | Ni un recorte, ni un texto solapado | ❌ manual |
| C2 | Cambiar de monitor con escalas distintas con la ventana abierta | La ventana no se descuadra | ❌ manual |
| C3 | `ui_scale` a 2.0 en `config.json` | Fuente, alto de fila, relleno y columnas suben **juntos** | U7 |

C3 es la que la fase arregla y la única automatizada. C1 y C2 dependen del
escritorio.

## D. Claro y oscuro

| # | Paso | Qué se espera | Automático |
|---|---|---|---|
| D1 | `theme = "dark"` | El panel de resultados, el detalle y el área de mensajes usan el tema oscuro | U8, T4 |
| D2 | En oscuro, los encabezados de columna se leen | Contraste `accent` sobre `background` | T4 (par nuevo) |
| D3 | En oscuro, la fila seleccionada se distingue | Contraste `selection_foreground` sobre `selection_background` | T4 |
| D4 | Cambiar de tema con la ventana abierta | **No** cambia en caliente; hay que reabrir | ❌ limitación declarada |
| D5 | Seguir el sistema (por defecto) con Windows en oscuro | Se resuelve al oscuro al arrancar | ✅ (`resolve`) |

## E. Estados vacíos y cero resultados

| # | Paso | Qué se espera | Automático |
|---|---|---|---|
| E1 | Ventana recién abierta | «¿Qué estás buscando?» + cómo se usa; el panel de resultados no está | U2, ✅ |
| E2 | Escribir `zzzq` | «Sin resultados para «zzzq».» + «Prueba con menos palabras…» | U2, ✅ |
| E3 | Escribir una consulta mal formada | «Consulta no válida: …» + pista de sintaxis, **en rojo** | U4, ✅ |
| E4 | Provocar un fallo de búsqueda (base bloqueada) | «Error al buscar…» + el motivo técnico debajo; **el panel se vacía** | U4, U5, ✅ |
| E5 | recovering de E4 con una consulta válida | El panel vuelve a la normalidad sin reiniciar | ✅ |

E3 y E4 son los dos estados que la 039 dejó a medias y esta fase cierra.

## F. Nombres, rutas y coincidencias largas

| # | Paso | Qué se espera | Automático |
|---|---|---|---|
| F1 | Un fichero con 150 caracteres de nombre | La fila acota; el detalle lo muestra entero | U6, ✅ |
| F2 | Una ruta de 10 niveles | La fila muestra las dos últimas carpetas; el detalle, la ruta completa en monoespaciada | U6, ✅ |
| F3 | Una coincidencia de 600 caracteres | La fila acota a 100; el detalle la muestra entera y con ajuste de línea | U6, ✅ |
| F4 | Reducir la ventana a su mínimo (680×400) | El detalle se ajusta de línea, no se recorta | ❌ manual |
| F5 | Un nombre con acentos, eñes y cirílico | Se lee igual en la fila y en el detalle | ✅ |

## G. Indexación en curso

| # | Paso | Qué se espera | Automático |
|---|---|---|---|
| G1 | Indexar una carpeta grande y buscar durante | La búsqueda responde; la etiqueta de la derecha cambia de estado | ✅ |
| G2 | Pausar y reanudar el indexador desde el menú | La etiqueta refleja el estado en ≤ 2 s | ✅ |
| G3 | Parar el indexador con un worker zombi | La etiqueta dice «?» en vez de mentir | ✅ |

## H. Errores de proveedor e índice

| # | Paso | Qué se espera | Automático |
|---|---|---|---|
| H1 | Quitar el soporte de un origen | La búsqueda sigue con el resto | ✅ |
| H2 | Índice corrupto | Mensaje de error, no pantalla en blanco | ✅ |
| H3 | Ruta eliminada entre indexar y abrir | El error dice que no se pudo abrir | ✅ |
| H4 | Carpeta sin permisos al indexar | La acción falla y se dice por qué | ✅ |

## I. Contrato de accesibilidad heredado de la 039

Todo lo anterior de la 039 sigue vigente y lo verifica su puerta: control
alcanzable por teclado, nombre declarado, contraste AA en los dos temas, todo
color dibujado, todo texto catalogado, fallo dibujado como fallo. La fase 041
**añade** un par de contraste (encabezados de columna) y **no toca** los otros
ocho.

## J. Controles de la fase 042

Estos controles no existían cuando se escribió esta lista, así que tienen su
propia sección. Automático = lo mide una puerta; manual = hace falta un
escritorio.

| # | Paso | Qué se espera | Automático |
|---|---|---|---|
| J1 | Escribir una consulta mal formada (`algo AND`) | «Consulta no válida» en rojo, y el panel se vacía | V1, ✅ |
| J2 | Escribir `capacitos` con la capa difusa activa | El resultado difuso aparece y **la caja no cambia** | ✅ |
| J3 | Desactivar la difusa y repetir J2 | Aparece la sugerencia; `Alt+Intro` la aplica | V3, ✅ |
| J4 | Cambiar *Orden* a «Por nombre» | Se reordena; si el conjunto era el de relevancia, se repregunta | V6, ✅ |
| J5 | Cambiar *Agrupar* a «Por carpeta» | Aparecen encabezados de grupo; no se puede abrir un encabezado | V5, ✅ |
| J6 | `Inicio` / `Fin` con agrupación | Van al primer y al último **resultado**, no a un encabezado | ✅ |
| J7 | *Buscar → Guardar la búsqueda actual…* | Pide nombre; el menú *Búsquedas guardadas* la lista | V8, ✅ |
| J8 | Aplicar una guardada | Restaura consulta, orden, agrupación y los dos filtros | V8, ✅ |
| J9 | *Ver → Explicar por qué coincidió* | El panel de detalle lista cada señal con su valor | V9, ✅ |
| J10 | *Buscar → Historial → Ver…* | Lista las consultas **y los números de retención** | V7, ✅ |
| J11 | *Historial → Dejar de recordar* | Se desactiva **sin borrar** lo ya guardado | V7, ✅ |
| J12 | Recorrer la fila de filtros con `Tab` | Orden, agrupación y recientes entran en el anillo con nombre accesible | T1, T2 |

## K. Ventana de ajustes (fase 043)

Se abre desde *Diagnóstico → Ajustes...*. La bandeja también la anuncia desde
que existe; hasta la fase 043 ese botón abría la ventana de búsqueda.

| # | Paso | Qué se espera | Automático |
|---|---|---|---|
| K1 | Abrir Ajustes | Seis pestañas por intención, no por módulo | ✅ prueba |
| K2 | Recorrer la ventana con `Tab` | Todo control es alcanzable y tiene nombre accesible | ✅ prueba |
| K3 | Poner «Documentos por búsqueda» a 999 | Se rechaza, no se guarda, y el estado lo dice | C3, ✅ |
| K4 | Cambiar el tema y guardar | Aparece «Requiere reiniciar»; al reabrir, el tema es el nuevo | C13, ✅ |
| K5 | Pulsar *Restablecer* | **Pide confirmación** y nombra lo que NO borra | ✅ prueba |
| K6 | Tras restablecer | Las carpetas y las guardadas siguen ahí; las preferencias voltou a su valor | C8, ✅ |
| K7 | *Exportar* y abrir el fichero | Preferencias, ningún dato, y un texto que dice que no lleva contraseñas | C9, ✅ |
| K8 | *Importar* un fichero alterado a mano | Aplica lo válido, rechaza lo que no lo es, ignora lo desconocido | C10, ✅ |
| K9 | Buscar un ajuste por su nombre | Todas las etiquetas y explicaciones están en el catálogo | C12, ✅ |
| K10 | Redimensionar la ventana de ajustes | Las explicaciones se ajustan de línea; nada se recorta | ❌ manual |

---

## Lo que queda sin hacer y por qué

## Lo que queda sin hacer y por qué

Ninguno de estos puntos puede decidirse sin una persona delante de un
escritorio, y están escritos aquí para que la omisión sea una decisión
registrada y no un olvido:

1. **El aspecto del anillo de foco bajo el estilo `vista`.** La puerta mide que
   el control está en el anillo y que su color tiene contraste; no mide si se ve
   bien.
2. **Que ttk pinte el `fieldbackground` configurado.** Una sonda temporal
   confirmó que `style.lookup` devuelve el valor bajo `vista`, `winnative` y
   `clam`, pero eso no prueba el píxel.
3. **C1, C2, B6, F4**: escalado de DPI real, cambio de monitor, reescalado del
   panel de detalle y reducción de la ventana.
4. **B1: ordenación por columna.** No implementada, y no por falta de tiempo:
   está escrita arriba como fuera de alcance.

## El punto que más conviene mirar

Si algo de esta lista va a fallar en la revisión de la 050, será **A**: el
recorrido completo con el teclado. Es la única puerta de esta fase que toca el
camino principal de uso, y las otras ocho son propiedades de la ventana. Una
ventana se ve en cinco segundos; un recorrido se usa todos los días.
