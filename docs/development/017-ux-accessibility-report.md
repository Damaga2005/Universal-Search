# Fase 017 — UX & Accessibility

## Qué se entregó

1. **La búsqueda ya no bloquea la interfaz.** Antes, `SearchEngine.search`
   se ejecutaba en el hilo de Tk: con un índice grande, escribir podía
   congelar la ventana. Ahora la consulta corre en un hilo worker y
   entrega el resultado por una cola que el hilo principal vacía cada
   40 ms. Cada búsqueda lleva un número de generación: un resultado de
   una pulsación ya superada **se descarta** en lugar de pisar la
   respuesta nueva.
2. **Estados explícitos** (antes solo «N resultado(s)»):
   - *cargando*: «Buscando «x»…» **sin** borrar los resultados anteriores
     (comparar dos consultas es el uso real de un buscador);
   - *sin resultados*: «Sin resultados para «x»»;
   - *consulta inválida*: el motivo (fase 012) sin traceback;
   - *error*: mensaje y registro, sin cerrar la aplicación.
3. **Tema centralizado** (`gui/theme.py`): una paleta clara y otra
   oscura, completas y verificadas, más la escala tipográfica. Ya no queda
   ni un `#666` suelto por el código: un test compara los colores de los
   widgets con el tema resuelto.
4. **Escalado**: `ui_scale` en configuración (0.75–2.5, nunca menos de
   8 pt) aplicado a la entrada, la lista y la vista previa.
5. **Filas de resultado con información útil** (`gui/rows.py`): nombre ·
   tipo · las dos últimas carpetas — fragmento, sin la ruta absoluta, sin
   el prefijo `(local)` que solo añadiría ruido, y con el prefijo
   `[onedrive]` cuando aporta algo.
6. **Foco explícito** en entrada y lista (`takefocus=True`), para no
   depender del valor por defecto del tema.
7. **Lista manual de comprobación** (`017-ux-checklist.md`): lo que un
   test no puede ver (foco real del sistema, contraste, escalado del
   escritorio, Narrator).
8. **18 tests nuevos** (`test_gui_ux.py`) y los tests de GUI existentes
   adaptados al contrato asíncrono. Suite completa: **465 en verde**.

## Bug encontrado y corregido durante la fase

`_render()` perdió el estado para el caso **con** resultados al añadir el
mensaje de "sin resultados": la barra se quedaba en «Buscando…» para
siempre aunque los resultados estuvieran en pantalla. Lo detectó el test
de estados al exigir que el número de resultados apareciera en la barra.
Instrumenté el flujo real antes de tocar código (volcando el estado tras
cada fase) en lugar de suponer: el dato.clearó la causa.

## Qué se comprobó y qué no

| Comprobado por test | Comprobado por persona (checklist) |
|---|---|
| El hilo de la UI no se bloquea (< 0.2 s) | Foco real del escritorio |
| Un resultado obsoleto no pisa al nuevo | Contraste en el modo del usuario |
| Los cuatro estados y su texto | Escalado 125/150/200 % |
| Colores iguales al tema centralizado | Narrator y orden de tabulación |
| Formato de fila, recorte y rutas | Atajo global sin ventanas duplicadas |
| `takefocus` declarado | Contraste alto de Windows |

## Atajos de teclado (sin cambios: ya eran correctos)

`Enter` abrir · `Ctrl+Enter` revelar · `Escape` limpiar y luego salir ·
`↑`/`↓` navegar con vuelta · `Re Pág`/`Av Pág` por pantalla ·
`Ctrl+C` copiar ruta. La fase 017 añade lo que faltaba alrededor, no
teclas nuevas.

## Criterios de aceptación (spec 017)

| Requisito | Estado |
|---|---|
| Flujo principal centrado en teclado | ✅ foco explícito, atajos verificados, checklist |
| La UI permanece responsiva | ✅ búsqueda en hilo worker + test de no bloqueo |
| Escalado legible | ✅ `ui_scale` acotado + tipografía centralizada |
| Estados de error comprensibles | ✅ cuatro estados con texto propio |
| Estilos centralizados | ✅ `gui/theme.py`; ningún color suelto |
| Limitaciones documentadas | ✅ este informe + checklist |

## Limitaciones (deliberadas)

- **Tk no es accesible por roles**: no hay ARIA niNombre accesible real.
  Lo verificable es el orden de tabulación, el foco visible y los
  textos; queda dicho en la checklist para no prometer más de lo que la
  herramienta permite.
- **El modo oscuro es una paleta, no un tema nativo de ttk**: los
  controles que usan el tema del sistema (combos, scrollbar) siguen
  siguiendo al sistema. Es una limitación honesta de ttk, no del diseño.
- **`ui_scale` es un multiplicador propio**, no el escalado por monitor
  de Windows: combinar ambos es trabajo de DPI por monitor, fuera de
  alcance aquí.
- **Sin animaciones**: nada que reducir movimiento, por diseño (spec 017
  lo pide como comportamiento correcto, no como requisito a añadir).
- El estado «cargando» es un texto, no un spinner: con búsquedas de
  ~30 ms (medido en 011) un spinner parpadearía más de lo que informa.
- El foco real del SO no se puede comprobar en un test automatizado; por
  eso existe la checklist y no un test que dé una falsa confianza.

## Cómo ejecutar

```bash
.venv\Scripts\python -m pytest tests\test_gui_ux.py
# checklist manual: docs\development\017-ux-checklist.md
# configuración: {"theme": "dark", "ui_scale": 1.25} en config.json
```
