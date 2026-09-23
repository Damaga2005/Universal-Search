# Fase 017 — Lista manual de UX y accesibilidad (Windows)

Lo que un test **no** puede comprobar: el foco real del sistema, la
legibilidad con escalado del sistema, el contraste en la pantalla del
usuario y el comportamiento con un lector de pantalla. Esta lista es la
comprobación humana, y cada punto indica **cómo** verificarlo.

Ejecutar con una cuenta real de Windows, al menos en 125 %, 150 % y 200 %
de escalado, en modo claro **y** oscuro.

## Flujo principal (1–5 de los principios UX)

- [ ] Lanzar desde el acceso directo: la ventana aparece en menos de un
      segundo y el cursor queda **en el cuadro de búsqueda**, sin hacer clic.
- [ ] Escribir despacio: los resultados se actualizan mientras se escribe y
      la ventana nunca se congela (comprobar con un índice de 10 000+ docs).
- [ ] Los resultados anteriores **no desaparecen** al seguir escribiendo:
      solo el estado dice «Buscando…».
- [ ] Escribir rápido y parar: el resultado que aparece es el de la última
      pulsación, nunca el de una tecla anterior.
- [ ] `Enter` abre el resultado seleccionado; `Ctrl+Enter` lo revela en el
      Explorador; `Escape` limpia la consulta y una segunda pulsación
      cierra la ventana.

## Teclado y foco

- [ ] `Tab` recorre entrada → contexto → fuente → tipo → recientes → lista
      **en ese orden**, y el foco es visible en cada parada.
- [ ] `↑` / `↓` mueven la selección y hacen scroll; en el primer y último
      resultado la selección da la vuelta (no se queda pegada).
- [ ] `Re Pág` / `Av Pág` saltan una pantalla de resultados.
- [ ] `Ctrl+C` copia la ruta del resultado seleccionado.
- [ ] El atajo global (por defecto `Ctrl+Alt+S`) levanta la ventana
      existente y **no** abre una segunda; comprobar con el contador de
      ventanas del Administrador de tareas.
- [ ] Con el atajo conflicto (otra aplicación lo ocupa), `indexer status`
      lo dice y la aplicación sigue funcionando sin atajo.

## Estados

- [ ] Consulta sin resultados: aparece «Sin resultados para «…»», no un
      `0 resultado(s)` pelado.
- [ ] Consulta mal formada (`bjt AND`): mensaje con el motivo, sin ventana
      de error ni traceback.
- [ ] Error de base de datos: estado «Error al buscar», la aplicación no
      se cierra.
- [ ] Indexador trabajando: la barra inferior muestra su estado sin
      interferir con los resultados.
- [ ] Carpeta añadida o filtros cambiados: la consulta se conserva.

## Legibilidad y escalado

- [ ] A 150 % y 200 % de escalado: la ventana sigue cabiendo en pantalla,
      la lista no se corta y el texto de resultados no se solapa.
- [ ] `ui_scale` en configuración (1.25) aumenta texto y lista a la vez.
- [ ] Con fuente de texto grande del sistema, el alto de fila acompaña.
- [ ] Tema oscuro: texto, seleccionado y foco se leen; ningún texto queda
      en negro sobre negro. Alternar `theme` en `config.json` y reiniciar.

## Contraste y color

- [ ] Contraste alto de Windows: la lista mantiene el foco visible.
- [ ] El color nunca es la única señal: el tipo aparece como texto (`PDF`,
      `TXT`) junto al nombre, no como una insignia de color.

## Resultados

- [ ] Cada fila muestra nombre, tipo y las dos últimas carpetas; la ruta
      absoluta no aparece.
- [ ] El fragmento de texto aparece cuando existe y se omite cuando no.
- [ ] Documentos de OneDrive se distinguen (`[onedrive]`) y los locales no
      se etiquetan (sería ruido).

## Lector de pantalla

- [ ] Con Narrator abierto, el cuadro de búsqueda y la lista se anuncian
      como listas editables; Tk no expone ARIA, así que la verificación es
      de tabulación y de orden de foco, no de roles.

## Accesos del sistema

- [ ] El menú «Archivo» y el «Diagnóstico» se abren con teclado (`Alt`).
- [ ] `Ctrl+Q` / `Alt+F4` cierran sin confirmaciones ocultas.
- [ ] La reconstrucción completa **siempre** pregunta antes de borrar.
