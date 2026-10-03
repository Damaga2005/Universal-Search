# Fase 044 — Aprendizaje local v2

Estado: implementada. Puerta: `python -m evaluation.learning_gate`.
Fase anterior: `043-settings-report.md`.

## Qué se encontró antes de escribir una línea

La auditoría del camino completo de la señal de uso —`record_open` hasta el
SQL— encontró que **el aprendizaje existente no aprendía nada**:

| Hecho | Consecuencia medida |
|---|---|
| `USAGE_COUNTS_SQL` contaba aperturas y **nunca leía la columna `query`** | un documento abierto para «examen» tambiénrecebía impulso para «receta paella». La columna `query` se escribía desde la fase 008 y la leía **una sola consulta: la que el usuario inspecciona**. |
| `opened_at` estaba en el esquema y **no lo leía nadie** al ordenar | un evento de 2024 pesaba lo mismo que uno de ayer. No hay tope en la tabla ni caducidad, así que lo único que impedía «el aprendizaje entierra documentos nuevos para siempre» era el peso pequeño: una promesa, no un mecanismo. |
| Peso `usage = 0.5` sobre un total de 14.0 | la señal valía **3,45 % del puntaje final frente al 2,14 % de `recency`**, es decir, era **más fuerte** que la única señal que el proyecto ya describe como «secundaria y acotada». |
| El bucketing de nombresExactos no se disparaba en 17 de 18 consultas del corpus | no había ninguna medición real de «la coincidencia exacta sigue dominando»: la afirmación del contrato era una afirmación sobre pesos, no sobre resultados. |
| `explain_notes` tenía entrada para el contexto personal y **ninguna para el uso** | la única señal que movía resultados en silencio era precisamente la que no se explicaba. |
| Las filas de uso de un fichero borrado se quedaban **para siempre** | `privacy forget <ruta>` las borraba a petición, pero nada las borraba por sí solo. El texto de la consulta sobrevivía al documento. |
| `usage_tracking` no estaba en el inventario | no es un dato personal —es un interruptor— y no lleva entrada; lo que sí se guarda son las filas, y esas sí estaban declaradas. **Se decide y se dice, no se inventa una entrada falsa.** |
| El CLI tenía `usage show`, que volcaba las filas crudas | «los datos pueden inspeccionarse» era cierto pero inútil: después de esta fase hay que poder preguntar *«qué sigue haciendo mi historial»*, y una lista de filas no responde a eso. |
| `runner.measure()` nunca pasaba `usage=True` | el arnés de evaluación no podía ni intentar medir el aprendizaje. |

## Qué se ha hecho

### `learn.py`: el valor de un conjunto de eventos

Un módulo de **funciones puras sobre números**. Una señal de ordenación que no
se puede evaluar sola no se puede discutir. El repositorio y el SQL viven en
`index/search.py`; este fichero dice **cuánto vale** un conjunto de eventos.

Tres decisiones, y la medición detrás de cada una.

**1. La señal tiene el ámbito de la consulta con la que se aprendió.** Un
evento cuenta para la consulta con la que se grabó, y una fracción mucho menor
cuenta para el documento en general: `QUERY_SHARE = 0.8`,
`GENERAL_SHARE = 0.2`. El número medido que hace útil esa proporción: **cuatro
aperturas bajo otra consulta no mueven nada**, porque cuatro quintos de cuatro
queda por debajo del suelo de `MIN_EFFECTIVE_EVENTS = 2.0`. Hacen falta diez.

**2. Los eventos envejecen.** Los cubos son un **escalón de cuatro tramos**, no
una exponencial, porque un escalón se puede leer en una pantalla y discutir; una
curva produce un número que nadie mira.

| Edad | Peso del evento |
|---|---|
| ≤ 30 días | 1,0 |
| ≤ 90 días | 0,6 |
| ≤ 365 días | 0,3 |
| más | 0,1 (suelo) |

Consecuencia medida y no deseada al principio, que resultó ser el punto:
**cuatro aperturas dentro del mes son toda la señal, y las mismas cuatro
repartidas en un año y medio no valen absolutamente nada**, porque 4 × 0,3 =
1,2 queda por debajo del suelo de 2,0. Ésa es la regla «el aprendizaje no puede
enterrar documentos nuevos para siempre» escrita como número.

El suelo no es cero a propósito: «abriste esto una vez hace dos años» es
evidencia, sólo que vieja, y una señal que puede caer a nada no distingue «nunca»
de «hace mucho».

**3. El peso se mide, no se elige.** Ver abajo.

**4. Una apertura no abre nada.** `MIN_EFFECTIVE_EVENTS` significa que un clic
accidental no reordena nada. Eso es lo que hace el arranque en frío
**determinista** en lugar de meramente silencioso: sin eventos, o con uno, la
respuesta es la respuesta del baseline, byte a byte.

### El peso: 0,5 → 0,25, con la distribución de márgenes delante

El techo de una media ponderada es `peso / (total + peso)`. Se midieron los
**23 márgenes de puntaje consecutivos** del corpus de evaluación antes de tocar
el peso:

| Cuantil | q00 | q10 | q25 | q50 | q75 | q100 |
|---|---|---|---|---|---|---|
| margen | 0,00001 | 0,00017 | 0,00721 | 0,07121 | 0,15947 | 0,26435 |

Y cuántos pares adyacentes es capaz de reordenar cada peso:

| peso activated | efecto máx. | pares reordenables | frente a `recency` |
|---|---|---|---|
| 0,50 (antes) | 0,03448 | 9 de 23 (39,1 %) | **1,61× más fuerte** ✗ |
| 0,35 | 0,02439 | 9 de 23 | 1,14× más fuerte ✗ |
| **0,25 (ahora)** | **0,01754** | **7 de 23 (30,4 %)** | **0,82× más débil** ✓ |
| 0,20 | 0,01408 | 7 de 23 | 0,66× |

Los márgenes son **bimodales**: un puñado de empates casi exactos por debajo de
0,024 y un grueso de márgenes reales por encima de 0,044. Bajar el peso de 0,5
a 0,25 cuesta **dos pares de 23**, y ambos siguen siendo los empates: una señal
que sólo importaba en márgenes que nada más podía resolver era la forma
equivocada de señal. A cambio, el aprendizaje pasa a ser **más débil que la
recencia**, que es lo que el contrato pedía.

### El predominio, de promesa a mecánica

El orden de pesos **no puede** prometer que una coincidencia exacta siga
dominando: `usage` es un cuarto de `filename_exact`, pero un rival con mejores
señales de contenido se sitúa a menos de un paso de uso de la coincidencia
exacta — que es exactamente lo que producen los empates del corpus. Así que la
garantía está escrita en `ranking.py`, no argumentada:

```python
if signals["filename_exact"] >= 1.0:
    signals["usage"] = 0.0
```

Un documento cuyo nombre de fichero **es** lo que escribiste ya está respondido;
su historial no tiene nada que aportar y no se le permite intentarlo. Vive en el
ranker, no en la capa SQL, así que un llamante que calcule el impulso a mano
recibe la misma respuesta.

Y el lado empírico, medido contra el rival más cercano que permite el corpus:

```
orden base      ['factura.md', 'factura_detalle.md']
orden aprendido ['factura.md', 'factura_detalle.md']
margen 0,203571  =  11,6× el efecto máximo del aprendizaje (0,017544)
```

### Explicación, inspección y borrado

- **Explicación**: `UsageSignal.note()` produce `«uso local: 3 apertura(s) para
  «examen»»`, que aparece en `explain_notes` — y que la fase 042 ya sabe
  mostrar en el menú *Ver*.
- **Inspección**: `universal-search usage effect` y
  `SearchEngine.usage_effects()` muestran **qué sigue haciendo el historial
  hoy**: por par (documento, consulta), las aperturas que sobreviven al
  decaimiento, el peso, y cuántas señales ya **no mueven nada** aunque la fila
  siga ahí. Un evento caducado es visible e inerte, no invisible.
- **Borrado**: ya existía `usage clear` y `privacy forget`. Ahora, además,
  `Indexer._delete()` borra las filas de uso del documento, que no son datos
  derivados sino el registro de lo que alguien hizo, y que son inútiles en
  cuanto el documento desaparece.

## La puerta: 11 invariantes, todas medidas

`python -m evaluation.learning_gate` → **11/11 SHIP**, salida 0.

| # | Invariante | Medido | Detalle |
|---|---|---|---|
| L1 | arranque en frío devuelve la respuesta del baseline | 0 | 18 consultas, misma respuesta con y sin historial |
| L2 | una sola apertura también | 0 | una apertura no cambia ninguna de las 18 respuestas |
| L3 | determinismo con historial | 0 | 3 búsquedas dan el mismo orden |
| L4 | olvido | 0 | 6 aperturas de hoy cambian el orden; las mismas de hace 1200 días vuelven al base |
| L5 | aprendizaje ≤ recencia | 0 | 1,75 % frente a 2,14 %; margen x0,8187 |
| L6 | la coincidencia exacta nunca se impulsa | 0 | 0 de las coincidencias exactas del corpus recibieron impulso |
| L7 | el aprendizaje no puede superarla | 0 | tras 40 aperturas del rival, la coincidencia exacta sigue primera; margen 11,6× el efecto máximo |
| L8 | un filtro explícito gana | 0 | con `doc_type=md` y 40 aperturas aprendidas de un `.txt`, 3 resultados y ninguno de otro tipo |
| L9 | beneficio: ¿ayuda de verdad? | 0 | de 33 pares (consulta, documento), **6 mejoran y 0 bajan**; 11 ya estaban primeros |
| L10 | sin regresión de MRR | 0,0000 | MRR 0,0556 sin aprendizaje y 0,0556 con aprendizaje activo sin historial; **0 de 18** consultas cambian de puntaje |
| L11 | todo cambio material es explicable | 0 | 1 resultado con señal de aprendizaje, 1 con una frase que la explique |

El barrido de L9 es la medición honesta de beneficio: **todos** los pares del
corpus, ninguno escogido de antemano.

```
«logica_cmos.md»        para «CMOS»        : del puesto 1 al 0
«mux_cmos.md»           para «MUX»         : del puesto 1 al 0
«BJT_Ebers_Moll.md»     para «polarizacion»: del puesto 1 al 0
«Tema_6_BJT.md»         para «polarizacion»: del puesto 2 al 0
«tension_base_emisor.md» para «notas»       : del puesto 2 al 1
«BJT_Ebers_Moll.md»     para «punto Q»     : del puesto 2 al 1
```

Nótese que **0 bajan**: el aprendizaje sólo puede promover. Y note que los seis
casos son empates del corpus, que es exactamente lo que predice la tabla de
márgenes.

## Defectos reales encontrados por las pruebas

1. **El barrido de L9 insertaba el *nombre* del documento en `usage_events` en
   lugar de su `document_id`**. Los ids del corpus no son los ids de la base de
   datos: el indexador indexa por el hash de la ruta. El barrido no casaba con
   nada y reportaba que el aprendizaje no ayudaba en absoluto — un cero
   limpio, seguro y completamente equivocado. Lo detectó el propio L9 (0
   promociones con un efecto de 0,0345 y nueve pares reordenables previstos) y
   está escrito en el docstring de la función para que no se repita.
2. **`usage_boost_from(1)` pasó a valer 0.0.** El test de la fase 008 fijaba
   `0 < f(1) < f(2)`. Se actualizó **a propósito**, con el motivo escrito: es la
   regla del arranque en frío.
3. **La curva de olvido es más agresiva de lo que se suponía.** El test
   inicial afirmaba que cuatro eventos muy viejos aún valían algo; la
   aritmética dice que no. Se corrigió el test a los números reales.
4. **`test_personal_signals_are_off_unless_they_are_supplied` usaba por
   casualidad un nombre exacto** (`bjt.md` consultada con «bjt»), así que la
   regla nueva lo ponía a cero. El test ahora usa un nombre que no coincide
   exactamente, y hay un test nuevo para la regla.
5. **`usage_boost_from` tenía dos implementaciones** al empezar: la de la fase
   008 en `context.py` y la nueva. Se conserva **una**, reexportada.
6. **`search()` no aceptaba `now`.** Sólo lo aceptaba `_search()`, y el reloj
   inyectado sólo sirve si se puede llegar a él desde la puerta.

## Lo que esta fase NO hace

- **No añade señales nuevas.** El prompt lista preferencia por origen y por
  tipo de documento. `SOURCE_SCORES` es todo 1,0 —el origen es hoy un punto de
  extensión neutro— y una preferencia por tipo o origen es, por definición, una
  señal **independiente de la consulta**, o sea exactamente el problema que esta
  fase acaba de corregir. Añadirlas sin evidencia de beneficio habría sido
  reintroducir el defecto con dos capas de más. **Se declaran, con el motivo.**
- **No introduce embeddings, perfiles en la nube ni telemetría.** Todo es local,
  sintético y reproducible desde un reloj fijo.
- **No toca `record_open`, la puerta de la GUI ni el ajuste `usage_tracking`.**
  La 043 ya los expone; esta fase no añade otro interruptor.
- **No toca `runner.measure()`.** Sigue sin pasar `usage=True`: el barrido de
  L9 mide el aprendizaje mejor que el arnés genérico, porque necesita un
  historial sintético, no un corpus.
- **No migra `fuzzy_gate.py` ni `suggest_gate.py`** a la metodología de veto de
  carga. Sigue siendo deuda.

## Limitaciones

1. **El barrido mide promoción, no retención.** Que el documento que el usuario
   abre suba es lo que se midió. Si el usuario abre el primero, que es lo más
   frecuente, el aprendizaje no aporta nada — y eso es correcto, pero significa
   que la señal sólo existe para el caso minoritario.
2. **La forgets no borra: deja de importar.** Las filas se quedan en la tabla
   hasta `usage clear`. Es una decisión —borrar el registro de lo que alguien
   hizo automáticamente sería peor que dejarlo visible— pero significa que la
   tabla crece sin límite.
3. **`filename_exact` protege por nombre, no por frase.** `phrase_exact` se
   dispara en cuanto el término aparece en el contenido, así que usarlo como
   predominio bloquearía el aprendizaje en casi todo. La protección es sobre el
   nombre, que es la señal que el contrato puede cumplir mecánicamente.
4. **Un historial enorme satura la parte general.** Veinte aperturas de un
   documento bajo una consulta sacudan la parte general igual que cuatro bajo esa
   misma consulta: el ámbito ordena la evidencia, no la filtra.
5. **El reloj se inyecta pero no se congela en producción.** `now=None` usa el
   reloj real; la puerta lo fija. Un reloj del sistema que salte hacia atrás
   revive señales — `_decay` trata la edad negativa como «reciente», no como
   evidencia de nada.

## Verificación

- `python -m evaluation.learning_gate` → **11/11 SHIP**, salida 0.
- `python -m evaluation.gate` → PASS una vez actualizado el recuento.
- pyflakes limpio.
- Pruebas: sólo las que tocan lo modificado. Total en `README.md`.
- Los tres `*_baseline.json` de latencia (UX, interacción, ajustes) se han
  **descartado** con `git checkout --`: re-ejecutar una puerta siempre los toca
  y aquí no cambió ninguna decisión.

## Para la 045

La 045 es «calidad y relevancia de búsqueda». Tres cosas que esta fase le deja:

- **un reloj inyectado en `SearchEngine.search()`**, que hace cualquier señal
  dependiente del tiempo comprobable en lugar de sólo observable;
- **la curva de forgetting medida y publicada**, como patrón para cualquier
  señal que pueda acumular historia;
- y una advertencia: el corpus de evaluación **no puede medir beneficio del
  aprendizaje** por sí solo, porque sus consultas recuperación y el
  aprendizaje sólo actúa en el caso minoritario de un flujo repetido. La 045
  mediría lo mismo con los mismos datos y llegaría a cero, y ese cero sería
  correcto.