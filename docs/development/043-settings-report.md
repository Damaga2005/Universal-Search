# Fase 043 — Ajustes y configuración

Estado: implementada. Puerta: `python -m evaluation.settings_gate`.
Fase anterior: `042-search-experience-report.md`.

## Qué se encontró antes de escribir una línea

Se auditaron todas las rutas de configuración que existen hoy. El resultado
tiene dos mitades.

**La que ya estaba bien**: `AppConfig` es un `dataclass` congelado, se guarda
atómicamente, tolera un fichero corrupto, e ignora claves desconocidas en lugar
de fallar. Eso es más de lo que tienen la mayoría de los programas.

**La que no**: la configuración no tenía **contrato**. Concretamente:

| Falta | Consecuencia medida |
|---|---|
| **Ni un límite en ningún número** | `indexer_file_delay: -1.0` llega a `time.sleep(-1)`, que lanza `ValueError` dentro del bucle de indexado y **tira la pasada entera**. Un intervalo de 0 hace que el bucle de espera del indexador gire sin dormir. |
| **Sin versión en el fichero** | «¿qué build escribió esto?» no tenía respuesta, y cada campo adivinaba. |
| **Las claves de una build nueva se destruían** | `save()` reescribía el fichero desde los campos que *esta* build conoce, así que bajar y volver a subir perdía un ajuste. |
| **El temporal tenía nombre fijo** | `config.json.tmp`. La ventana, su servicio, el centro de control y `set_autostart` escriben aquí, y dos de ellos en el mismo segundo escribían el mismo temporal. |
| **Dos fuentes de verdad para el autoinicio** | el registro de Windows y `AppConfig.start_with_windows`, escritos por superficies distintas. `cli indexer autostart on` nunca tocaba el segundo. |
| **Sin `semantic_enabled`, sin `tray_enabled`, sin nivel de registro, sin número de resultados** | cuatro cosas que la CLI tenía por bandera y la ventana no tenía de ninguna manera. |
| **La bandeja anunciaba «Configuración» desde que existe** | y ese comando abría la ventana de búsqueda. `tray.py:42,245`. |
| **La precedencia sólo estaba en prosa** | repartida en tres ficheros, y ninguna comprobable. |

Y un hallazgo que la fase 042 dejó preparado: la ventana leía `theme` y
`ui_scale` **una vez al construirse**, así que cambiarlos exigía reiniciar y no
había forma de hacerlo.

## Qué se ha hecho

### `settings.py`: el esquema

Un módulo sin Tk y sin E/S que dice, para cada ajuste: tipo, valor por defecto,
rango, si es avanzado, si necesita reiniciar y a qué parte del programa
gobierna. 18 ajustes en 6 grupos.

**Lo que deliberadamente NO está aquí**: la etiqueta y la explicación que lee
una persona. Viven en `gui/strings.py` bajo `SETTINGS.<CLAVE>.LABEL` y `.HELP`,
para que el catálogo siga siendo la lista única de texto visible y la auditoría
de la 039 siga cubriéndolos. Un esquema con sus propias cadenas en español sería
un segundo sitio donde traducir.

### Los límites, y por qué existen

| Ajuste | Rango | Por qué |
|---|---|---|
| `indexer_interval_seconds` | 30 … 86400 | por debajo de 30 el indexador gira; por encima, no revisa nunca |
| `indexer_file_delay` | 0 … 60 | **negativo ⇒ `time.sleep` lanza y falla la pasada** |
| `onedrive_download_max_mb` | 0 … 1024 | sin tope, «descargar OneDrive» significa descargarse el OneDrive entero |
| `result_limit` | 10 … 500 | más de 500 no cabe en la ventana y cuesta más que aporta |
| `ui_scale` | 0,75 … 2,5 | el mismo tope que ya tenía `clamp_scale` |

Un valor fuera de rango **se carga** (cargar con elegancia sigue siendo
elección), pero `problems()` lo dice y `repaired()` lo arregla **cuando el
usuario lo pide**. Un cargador que reescribiera en silencio escondería el
problema.

### Versión, migración y claves ajenas

`CONFIG_VERSION = 2`, con el modelo **copiado del índice de la base de datos**,
que ya lo tenía resuelto (`PRAGMA user_version` + registro de migraciones):
un número, unos pasos y la negativa a rebajar un fichero de una build posterior.
Un fichero sin `version` es de la versión 1, que es lo que escribió todo lo
anterior.

Y `save()` ya no destruye lo que no conoce: lee el fichero, conserva las claves
ajenas y **no rebaja** una versión mayor. Bajar y volver a subir ya no pierde
nada.

### Guardado atómico de verdad

El temporal ahora lleva el pid, como ya hacía el `control-center.json` del centro
de control. Es un cambio de tres caracteres que elimina una colisión entre
procesos.

### La ventana de ajustes

Seis pestañas por intención —**qué se indexa, cómo se busca, cuándo y cómo se
indexa, cómo se ve, qué se recuerda, diagnóstico**— y no por módulo, porque el
módulo es un detalle de implementación y la pregunta que hace una persona es
«¿dónde apago esto?».

Cada control lleva debajo su explicación. Los seis ajustes que se leen una vez
al construir la ventana o el indexador dicen **«Requiere reiniciar la
aplicación para aplicarse»**, en vez de dejar que el usuario lo descubra.

Las carpetas indexadas, las búsquedas guardadas y el historial se **muestran en
la ventana pero no se editan ahí**: pertenecen al centro de control y a la
ventana de búsqueda, y una segunda superficie para cambiarlas sería
justamente lo que esta fase elimina.

`Restablecer` **pregunta**, y la pregunta nombra la excepción: las carpetas, las
guardadas y el historial **no** se borran. Un restablecimiento que olvidara las
carpetas que indexas sería pérdida de datos con una etiqueta amable.

### Exportar e importar

El prompt los marcaba «where justified». Se justifican por dos razones: llevar
los ajustes a otro equipo es algo que la gente hace, y una función que no se
puede abandonar es una función que no se puede comprobar.

El archivo exportado lleva **preferencias y ningún dato**, y **dice** en su
propio texto que no lleva contraseñas ni claves — que es cierto y ahora es
comprobable en vez de una promesa. Importar **rechaza** un archivo de una
versión posterior, aplica lo válido, **rechaza** lo que está fuera de rango e
**ignora** lo desconocido y los datos.

### Los cuatro ajustes que faltaban

`semantic_enabled`, `tray_enabled`, `result_limit` y `log_level`. Los dos
primeros son interruptores de capas opcionales, con el mismo patrón que
`fuzzy_enabled` y el mismo motivo: son trabajo extra por consulta y quien no lo
quiera debería poder decirlo donde ve los demás interruptores. `log_level`
llega a `setup_logging`, que llevaba `INFO` fijo.

### La precedencia, como datos

`appconfig.PRECEDENCE` es una tabla ordenada y explicada, y una puerta la
comprueba. Nada de eso **anula** un ajuste: las variables eligen *dónde* viven
los ajustes, nunca *qué dicen*.

## La puerta: `python -m evaluation.settings_gate`

Trece invariantes, umbrales en cero, **13/13 SHIP, salida 0**. Es la primera
puerta de este programa que no necesita ventana.

| Puerta | Pregunta |
|---|---|
| C1 | ¿cada ajuste tiene un valor por defecto que coincide con la configuración? |
| C2 | ¿cada preferencia está en el esquema, y el esquema no inventa? |
| C3 | ¿un valor fuera de rango se rechaza, y se puede reparar? |
| C4 | ¿un tipo equivocado se rechaza en vez de convertirse? |
| C5 | ¿el fichero declara su versión y no se rebaja una ajena? |
| C6 | ¿una clave de otra build sobrevive a un guardado? |
| C7 | ¿no queda ningún temporal, y su nombre lleva el pid? |
| C8 | ¿restablecer conserva los datos del usuario? |
| C9 | ¿la exportación lleva datos o calla sobre las contraseñas? |
| C10 | ¿importar rechaza lo posterior, aplica lo válido y echa lo demás? |
| C11 | ¿la precedencia está ordenada y explicada? |
| C12 | ¿cada ajuste tiene etiqueta y explicación catalogadas? |
| C13 | ¿los reinicios se dicen en vez de descubrirse? |

## Defectos reales encontrados por las pruebas

Cuatro, y tres de ellos los causes esta fase al tocar lo que ya existía:

1. **`GROUP_LABELS` en el esquema**: un segundo sitio donde traducir, en el
   módulo cuyo propio docstring dice que no lo haya. Lo detectó la prueba que
   comprueba que no exista.
2. **Las claves del catálogo en minúsculas**: `SETTINGS.roots.LABEL` no es un
   identificador, y la regla de la 039 es que una clave lo es. 36 claves
   corregidas.
3. **`Menu.add_command` devuelve `None`** (041) y **`setup_logging` cambió de
   firma** (aquí): los dos tests que fijaban la firma antigua se actualizaron a
   propósito, no por sorpresa.
4. **`SettingsWindow(parent, service)`** tomaba el servicio posicionalmente, que
   Tk interpretaría como un widget. Ahora `parent` va primero y `service` es
   sólo de palabra clave, igual que `ControlCenterWindow`.

## Lo que esta fase NO hace

- **No toca la semántica de búsqueda** ni los límites de la base de datos. El
  esquema cubre preferencias, no datos.
- **No unifica las dos fuentes del autoinicio.** El registro y
  `start_with_windows` siguen siendo dos. La GUI escribe los dos; el CLI
  escribe sólo el registro. Queda declarado, y es una fase entera de trabajo
  (registro de Windows, reversibilidad) que no cabe aquí.
- **No toca el modo portable**, que vive fuera de `config.json` (el marcador
  junto al ejecutable) y por tanto no es un ajuste sino un estado del programa.
- **No reescribe la geometría de la ventana al abrir ajustes**: `window_geometry`
  es de la ventana principal y se guarda al cerrarla.
- **No hay tema en caliente.** Se sigue necesitando reiniciar, y ahora la
  ventana lo dice.

## Limitaciones

1. **La ventana de ajustes no muestra los datos, los resume.** Las carpetas
   indexadas aparecen como texto y se editan en el centro de control. Es una
   decisión —un solo sitio para cada cosa— pero significa que «un sitio» son
   dos ventanas.
2. **Importar no pregunta antes de aplicar.** Aplica lo válido y reporta lo
   rechazado. Dado que un archivo exportado no puede contener nada que rompa
   nada, la confirmación habría sido ruido; si algún día el formato crece, habrá
   que añadirla.
3. **No hay historial de cambios de ajustes.** Guardar es atómico; deshacer no
   existe.
4. **`log_level` afecta al proceso que lo lee al arrancar.** Cambiarlo necesita
   el reinicio que la ventana declara.

## Verificación

- `python -m evaluation.settings_gate` → **13/13 SHIP**, salida 0.
- `python -m evaluation.accessibility_gate` → SHIP, 221 entradas catalogadas.
- `python -m evaluation.interaction_gate` → SHIP.
- `python -m evaluation.gate` → PASS una vez actualizado el recuento.
- pyflakes limpio.
- Pruebas: sólo las que tocan lo modificado. Total en `README.md`.

## Para la 044

La 044 es «aprendizaje local v2». El plan de la fase dice que el aprendizaje
tiene que ser **siempre secundario** respecto a la intención explícita. Tres
cosas que esta fase le deja:

- `usage_tracking` es ahora un ajuste visible con su explicación, y dice en
  pantalla qué se guarda y dónde;
- existe un patrón para «un ajuste que cambia el comportamiento de una capa
  opcional»: el esquema lo declara, la puerta comprueba que llega, y el servicio
  lo aplica en un único sitio;
- y una advertencia que la 044 hereda: `log_level` y `theme` se leen al
  arrancar, así que cualquier ajuste que una capa consultaría en caliente tiene
  que decidir si lo hace así o lo declara como «requiere reiniciar».