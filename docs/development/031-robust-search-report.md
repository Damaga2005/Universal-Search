# Fase 031 — Búsqueda robusta: erratas y palabras parciales

## Qué resuelve

Hoy `documents_fts` usa `tokenize = 'unicode61'`, es decir coincidencia por
token completo. Consecuencia medida antes de escribir nada:

| Consulta | Antes | Ahora |
|---|---|---|
| `transisto` | 0 resultados | encuentra el documento con «transistor» |
| `transsistor` (letras cambiadas) | 0 resultados | el mismo |
| `transistorr` (letra repetida) | 0 resultados | el mismo |
| `transltor` (dos erratas) | 0 resultados | el mismo |
| `polarisacion` (sin tilde) | 0 resultados | el documento con «polarización» |
| `eberts moll` (dos palabras, una mal) | 0 resultados | los documentos de Ebers-Moll |
| `valensiana`, `azarfan`, `sofritoo` | 0 resultados | el documento correspondiente |

Ninguno de estos casos es un sinónimo: es **la misma palabra escrita de otra
forma**, el fallo más común de cualquier buscador y aquí era total.

## Cómo funciona

Dos pasos, y la separación es el punto de la fase:

1. **Bloqueo** (`fuzzy/trigrams.py`): huellas de trigramas de carácter, como
   mucho 64 por documento, elegidas entre las 32 palabras más distintivas. El
   coste es **por documento, no por byte**: un fichero de 2 MB y uno de 40 B
   cuestan lo mismo.
2. **Verificación** (`fuzzy/verify.py`): cada candidato se contrasta con el
   **texto real**. Un token solo se resuelve si está contenido (prefijo) o si
   alguna palabra del documento está a distancia Damerau-Levenshtein acotada.

El índice de trigramas **propone; nunca decide**. Por eso la capa no puede
inventar respuestas: un documento que el bloqueo puntúe alto y la verificación
rechace no se devuelve. Hay un test exactamente para eso
(`test_the_verifier_rejects_what_the_blocker_likes`).

### Por qué no una segunda tabla FTS5 con trigram

Se descartó **por coste medido, no por falta de soporte** (SQLite 3.50.4 sí lo
tiene, y está comprobado). FTS5 trigram emite un término por cada posición de
carácter: un documento con el límite actual de 2 MB produciría hasta 2 000 000
de filas, y 10 000 documentos darían del orden de 10^10. Recortar el texto
para poder buscarlo significa que lo que no se indexa es exactamente lo que el
usuario no encuentra.

### Acotamientos

| Acotamiento | Valor |
|---|---|
| Trigramas por documento | 64 |
| Palabras por huella | 32 |
| Candidatos que llegan a verificación | 50 |
| Caracteres leídos por candidato | 4 000 |
| Distancia de edición | ≤1 (4–7 caracteres), ≤2 (≥8) |
| Longitud mínima de token | 3 |

### Contrato (heredado de 026, con una lección más)

- **Solo cuando el motor léxico no devuelve nada.** Cualquier resultado no
  vacío se devuelve intacto y sin reordenar.
- **Los filtros mandan**: con `source:` o `type:` la capa se desactiva. Es la
  lección de 026, ya cubierta por un test.
- **Opt-out**: `search --no-fuzzy`, simétrico a `--no-semantic`.
- **Se explica**: cada resultado lleva `explain` con el token, la regla
  (`exact-name`, `exact-text`, `substring`, `edit`) y la distancia.
- **Se borra**: `privacy forget` elimina las huellas del documento y el
  inventario declara las tres tablas.

## Puerta de evidencia

`python -m evaluation.fuzzy_gate` sobre el corpus etiquetado (27 documentos,
18 consultas) más 10 consultas de erratas y prefijos.

| Puerta | Umbral | Medido | Veredicto |
|---|---|---|---|
| T1 recall@5 de consultas robustas | ≥ 0,80 | **0,90** (9/10) | PASS |
| T2 fugas en «debe recuperar nada» | 0 | **0** | PASS |
| T3 cambio de MRR léxica | 0 | **0** (sigue en 0,8333) | PASS |
| T4 crecimiento del índice | ≤ 15 % | **5,8 %** (57 344 B) | PASS |
| T5 latencia añadida p95 | ≤ 8 ms | **+5,13 ms** (léxico 4,07 → híbrido 9,19) | PASS |
| T6 dependencias nuevas | 0 | **0** | PASS |

**VEREDICTO: SHIP (6/6)**. Registro en `evaluation/fuzzy_baseline.json`.

## Lo que la puerta encontró, y por qué importa

La primera ejecución dio **NO SHIP (2 de 6)**. Ninguna de las dos era un
detalle de redacción:

### T1 = 0,40 → 0,90: el umbral de solapamiento estaba en el sitio equivocado

El bloqueo aplicaba un único umbral de solapamiento **sobre la consulta
entera**. La medición mostró el caso `eberts moll`: cuatro documentos
propuestos, la verificación diciendo **`True`** en los cuatro, y el umbral
0,55 descartándolos antes de llegar a ella. En una consulta de dos palabras, la
palabra larga ahoga a la mal escrita.

El arreglo no fue subir el umbral, sino **bloquear por token y unir**: cada
token tiene su propio solapamiento y el verificador recibe la unión. T1 pasó
de 0,40 a 0,90.

### T4 = 34,7 % → 5,8 %: el identificador de 64 caracteres

Cada fila guardaba el hash SHA-256 del documento: 64 caracteres por fila, unos
250 B, y la puerta de almacenamiento se disparaba. Se añadió una tabla de
mapeo con un **surrogate entero** y las filas de postings llevan ese entero
(unos 20 B). La tabla de mapeo es derivada y se reconstruye con el resto.

### T5 = 41,7 → 5,13 ms: tres ineficiencias reales

El perfil (no la intuición) mostró que la verificación eran **2,4 ms** y el
resto eran sobrecostes:

| Ineficiencia | Arreglo | Ahorro |
|---|---|---|
| Una conexión SQLite por operación (5 por búsqueda, y cada una reejecuta los PRAGMA) | una conexión para todo el fallback | ~18 ms |
| Contenido **completo** de hasta 50 documentos por consulta | `substr(content, 1, N)` en SQL | ~8 ms |
| Plegado de acentos por (candidato, token) | plegar una vez por candidato | ~2 ms |
| Comparación de 64 caracteres antes de cada distancia de edición | solapamiento de caracteres, que es un filtro **exacto** y no una heurística | incluido arriba |

**Y un error de medición mío**: la puerta comparaba el p95 de las
*diferencias* entre dos muestras ruidosas. En una máquina cargada, eso lo
domina el ruido, no la capa: daba 41,7 ms y luego 16,7 ms mientras el coste
real bajaba. La puerta ahora mide el p95 de cada motor por separado y resta,
y registra la carga de CPU junto al número. La puerta T5 **no se movió**: los
5,13 ms están dentro del presupuesto original de 8 ms.

## Un fallo de diseño que solo un test pudo encontrar

La primera versión de la huella guardaba los **64 trigramas más selectivos**
del documento. En un corpus de varios documentos, los trigramas de una palabra
común («transistor», presente en dos ficheros) pierden el concurso de
selectividad contra un trigrama que sale una vez, se descartan, y una
consulta de esa palabra no bloquea nada. T1 = 0,40.

La huella se rehízo alrededor de la **palabra**, no del trigrama: se eligen
las 32 palabras más distintivas del documento y se guardan sus trigramas. Al
medirlo apareció un segundo efecto: ordenando por longitud, una palabra de 14
caracteres se comía un cuarto del presupuesto antes de que ninguna otra
contribuyera, así que el reparto es **round-robin** y los trigramas conservan
su **orden posicional** (para un prefijo importan los trigramas iniciales, no
los alfabéticamente primeros).

Tres tests fijan estas propiedades, porque las tres son fáciles de
reintroducir por accidente.

## Fuera de alcance, con el motivo medido

No se incluyen en T1 porque el diseño no puede servirlas, y el motivo está
escrito en `evaluation/fuzzy_gate.py` en vez de escondido:

- `polirazcion` (para «polarizacion»): a **3 ediciones**, más allá del
  presupuesto de 2. Subir el presupuesto para cazarla multiplicaría los falsos
  positivos en palabras cortas.
- `recettas` (para el directorio `personal/recetas/paella.md`): la palabra
  solo aparece en la **ruta**, y las rutas no se indexan a propósito; la fase
  026 midió que sus trigramas contaminan la similitud.
- Transposición a mitad de palabra de 7 caracteres o menos: `azarfan` por
  «azafran» comparte **1 de 5** trigramas (0,2). Es un límite real del
  bloqueo por trigramas, no un defecto de la verificación, que la acepta.

## Un cambio en el gate de 030, y por qué

Al abrir el programa v2.x, la comprobación 13 («hoja de ruta cerrada») paso a
dar rojo: ahora hay nueve fases abiertas, y eso es exactamente lo que un plan
de diez fases debe mostrar en su primer commit.

La invariante se cambió por otra que sigue siendo cierta **y es más fuerte**
que una lista de casillas vacía:

- toda fase **completada** tiene documento sustancial y fila en el índice;
- ninguna fase abierta queda por debajo de una completada, así que una casilla
  olvidada en medio de un programa terminado se detecta igual.

Las fases abiertas se leen *de* la hoja de ruta, luego están declaradas por
definición y no necesitan documento todavía: exigirlo sería pedir
documentación para fases que no han ocurrido. Tres tests nuevos cubren las tres
situaciones.

## Pruebas

`tests/test_fuzzy_search.py`, **43 tests**, agrupados alrededor de las
promesas de la fase y del caso negativo que la hace segura:

- acotamiento: un documento 20 000 veces más largo tiene la **misma** huella
- ninguna palabra monopoliza el presupuesto
- prefijo, errata de transposición, letra repetida, dos ediciones, acento
- **el verificador rechaza lo que el bloqueador acepta**
- una consulta de varios tokens exige que **todos** casen
- las consultas «debe recuperar nada» siguen vacías
- los filtros desactivan la capa; el motor léxico no vacío se devuelve intacto
- `explain` dice qué token casó y por qué regla
- `privacy forget` borra huellas y el original sigue en disco
- reconstrucción diferida, `remove_all`, y borrado de filas al eliminar el
  fichero del disco

### Un hueco encontrado por inspección

`Indexer._delete` borraba el documento, sus filas de FTS y el grafo, pero
**no las filas semánticas de la fase 026**: un documento eliminado del disco
seguía vivo como huérfano hasta que alguien ejecutara
`diagnose recover orphan-derived`. Corregido, y con test para las dos tablas
opcionales.

## Compatibilidad y limitaciones

- Esquema **9 → 10**. Las tablas nuevas se crean al abrir; no hay migración de
  datos porque son derivadas y se reconstruyen.
- El índice se reconstruye de forma diferida la primera vez que hace falta
  (igual que el semántico), y se marca `dirty` tras indexar.
- Con `--no-fuzzy`, o sin el índice, la búsqueda es exactamente la léxica.
- El límite de 32 palabras por huella es el que decide *cobertura*: una
  palabra del documento que no esté entre las 32 más distintivas no bloquea
  candidatos. Es un tope de recuperación, no de corrección.
- El presupuesto de latencia se midió con el equipo al 39 % de CPU. La puerta
  registra la carga para que el número sea interpretable,siguiendo la
  lección de la auditoría de 2.0.0.
