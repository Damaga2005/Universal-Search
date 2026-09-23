# Fase 012 — Advanced Search

## Qué se entregó

1. **Lenguaje de consultas** en `src/universal_search/query/`, separado en
   cuatro etapas con una entrada por etapa:
   `lexer.py` → `parser.py` → `validate.py` → `translate.py`, más
   `nodes.py` (AST inmutable) y `errors.py` (`QueryError`). `parse_query()`
   encadena las etapas 1–3; `translate()` produce el `QueryPlan` que el
   motor consume.
2. **Traducción en el motor** (`SearchEngine._search`): el plan se calcula
   **antes de cualquier E/S**, los filtros del lenguaje se combinan con
   los argumentos `source` / `doc_type` que ya existían (fase 009), y las
   negaciones se izan a un solo operando derecho del `NOT` de FTS5.
3. **Ruta de solo-filtros** (`type:pdf`, `size:>10MB`, …): sin texto que
   puntuar, las filas se resuelven por SQL ordenadas por fecha de
   modificación descendente, con `score = 0.0` y sin snippet.
4. **Feedback compartido CLI/GUI**: el CLI imprime `error: <mensaje>` por
   stderr y sale con código 1; el servicio de la GUI captura el error en
   `last_query_error` y la ventana lo muestra en la barra de estado. Ninguna
   de las dos rutas produce traceback.
5. **91 tests nuevos** (`test_query_parser.py` 72,
   `test_advanced_search.py` 19). Suite completa: **316 en verde**.

## Arquitectura: cinco capas, cinco responsabilidades

| Etapa | Módulo | Responsabilidad | Qué rechaza |
|---|---|---|---|
| 1. Léxico | `lexer.py` | Texto → tokens: palabras (`\w+`), frases con escapes, campos `campo:valor`, `-`, `AND`/`OR`, paréntesis | Nada: aquí no se decide qué *significa* un campo |
| 2. AST | `parser.py` | Tokens → nodos inmutables (`Term`, `Phrase`, `FieldRef`, `Filter`, `Not`, `And`, `Or`) con precedencia | Operadores colgantes, paréntesis desbalanceados, `campo:` sin valor |
| 3. Validación | `validate.py` | Normaliza valores (`.pdf`, bytes, `YYYY-MM-DD`) y aplica la política estructural | Valores malformados, filtros dentro de un `OR`, negación de un filtro |
| 4. Traducción | `translate.py` | AST → `QueryPlan(fts, negations, terms, sql, sql_params)` | Nada: solo decide cómo se expresa en FTS5/SQL |
| 5. Ranking | `ranking.py` (sin cambios) | Puntúa con `plan.terms` | — |

El límite entre capas es la razón de que la suite del parser pueda
comprobar semántica sin base de datos, y la razón de que el motor no
construya nunca SQL ni MATCH a mano.

## Semántica exacta (decisiones documentadas)

**Precedencia:** `-` (negación) > `AND` (explícito e implícito) > `OR`.
`bjt cmos OR mux` es `(bjt AND cmos) OR mux`; `bjt OR cmos mux` es
`bjt OR (cmos AND mux)`.

| Forma | Semántica |
|---|---|
| `bjt mux` | AND implícito (idéntico a antes de la fase 012) |
| `"ebers moll"` | Frase: las palabras deben ser adyacentes **y en ese orden** |
| `bjt AND mux` / `bjt OR mux` | Explicitación, sin distinction de mayúsculas |
| `-cmos` | Exclusión: izada a `(positivos) NOT ("cmos")` |
| `(a OR b) c` | Agrupación explícita |
| `()` | Grupo vacío: no aporta nada (se descarta de su cadena) |
| `-type:pdf` | Rechazado: los filtros no se pueden negar |
| `type:pdf OR bjt` | Rechazado: un filtro dentro de un `OR` no es expresable sin mentir sobre el significado |
| `bjt -cmos OR mux` | Rechazado: la negación quedaría dentro del `OR` |
| `-cmos` / `type:pdf -bjt` | **Sin resultados**: FTS5 no tiene `NOT` unario, así que una exclusión sin término positivo no tiene dónde anclarse (sondeado, no supuesto) |
| `""`, `"*"`, `"==="`, `";"` | Sin resultados: una consulta vacía significa "sin filtro", no "todo" |
| `foo:bar` | Degrada a `foo AND bar` — compatibilidad de la fase 004: texto libre con dos puntos nunca falla |
| `3:15` | Sigue buscando `3` y `15` |
| `C++` | Busca `c` |
| `"notes` | La comilla sin cerrar toma el resto literalmente (compatibilidad 004) |
| `bjt bjt BJT` | Los términos se deduplican para puntuar; la conjunción FTS es idempotente |

**Unicode:** las palabras son `\w+` con semántica Unicode (la misma forma
que usa el tokenizador `unicode61` de FTS5), sin normalización destructiva:
`calibración` se busca tal cual.

**Filtros:**

| Campo | Nivel | Formato | Normalización / semántica |
|---|---|---|---|
| `name:` | Columna FTS | `name:informe`, `name:"informe final"` | Frase sobre la columna `name` |
| `path:` | Columna FTS | `path:universidad` | Frase sobre la columna `path` |
| `type:` | SQL | `type:pdf`, `type:.PDF` | `d.extension = ?` con `.pdf` |
| `source:` | SQL | `local` \| `onedrive` \| `other` | `d.source = ?`; otro valor es error |
| `after:` | SQL | `after:2026-01-01` | `substr(d.modified_at,1,10) > ?` — **estricto**: el día citado queda fuera |
| `before:` | SQL | `before:2026-09-23` | `substr(d.modified_at,1,10) < ?` — **estricto**, igual que `after` |
| `size:` | SQL | `>10MB`, `500KB`, `1000`, `<=1.5GB` | Unidades binarias (1 KB = 1024 B); sin operador, `>=`; sin unidad, bytes |

`size:>1.5GB` se redondea a entero (1 610 612 736 B). Las fechas se
validan con `strptime`: `2026-02-30` es un error con mensaje, no un
resultado silencioso.

## Seguridad: por qué la inyección es imposible

- La cadena `MATCH` se ensambla **solo** con palabras `\w+` entrecomilladas,
  columnas de una lista blanca (`name`, `path`) y los operadores conocidos
  `AND`/`OR`/`NOT`/`(`/`)`/`:`. Ningún carácter del usuario puede llegar a
  la sintaxis de FTS5. Un test parametrizado sobre 10 entradas hostiles
  comprueba el invariante con `re.fullmatch(r'[\w\s"():]+', plan.fts)`.
- El SQL son plantillas estáticas (`d.extension = ?`,
  `substr(d.modified_at,1,10) > ?`, …) con **todos los valores como
  parámetros ligados**; la ruta de solo-filtros recibe únicamente las
  plantillas, nunca texto.
- Cuatro sondas empíricas contra FTS5 real fijaron decisiones que una
  suposición no habría detectado:
  - `NOT a` (unario) **no existe** → de ahí la regla de negative-only.
  - `("a") NOT ("b")` y `("a") NOT (name : "b")` **sí** funcionan → de ahí
    que las negaciones se izen envueltas en paréntesis.
  - `("a") NOT ()` es **error de sintaxis** → nunca se emite (una lista de
    negaciones vacía se detecta antes).
  - `NEAR`, `MATCH`, comillas sueltas y `%00 OR _%` se tratan como
    palabras o se rechazan; `"_"` es una frase válida que simplemente no
    casa con nada, así que ni siquiera hace falta un filtro extra.
- El texto de la consulta **nunca** se pasa crudo a FTS5 (antes de esta
  fase sí se intentaba y se caía a un reintento con `sanitize_query`); esa
  caída defensiva se conserva solo para una discrepancia del tokenizador y
  sin perder las exclusiones. `sanitize_query()` se **elimina** del módulo:
  sin llamantes, y mantener un constructor de MATCH parallelo al
  traductor sería una puerta abierta para saltarse el lenguaje de
  consultas.

## Rendimiento y coste

- **Una consulta inválida no abre la base de datos**: la traducción ocurre
  antes del primer `connect()`. El test del CLI lo comprueba con un
  `--database` a una ruta que no debe existir (`assert not exists()`).
- **Ruta de solo-filtros** sin `MATCH` ni `bm25`: una consulta, un `ORDER
  BY modified_at DESC, path` y un `LIMIT`. Coste lineal en el número de
  filas que casan con el filtro (con el índice único de `path` y el filtro
  de `extension` como única columna no indexada, es la alternativa
  documentada; la fase 013 decidirá si merece un índice).
- **Camino positivo idéntico al anterior**: mismo pool acotado
  (`max(limit×5, 50)`), mismo `JOIN` de `documents` dentro del subquery del
  pool, mismo ranking. `plan.terms` alimenta exactamente las mismas
  señales que antes recibían `query_terms(query)`.
- Sin dependencias nuevas, sin servicios, sin red.

## UX: el error es información, no un fallo

| Superficie | Comportamiento ante una consulta inválida |
|---|---|
| CLI | `error: operator 'AND' needs a term after it` por stderr, salida con código 1, sin traceback, sin abrir la BD |
| GUI | La lista se vacía y la barra de estado muestra `Consulta no válida: <mensaje>`; el siguiente acierto limpia el mensaje |
| Fondo (indexador) | No busca: no afectado. La capa de consulta es única porque CLI y GUI llaman al mismo motor |
| Métricas | Una consulta inválida **no se mide**: `QueryError` se lanza antes de `record_search` (test con un sink propio) |

Los mensajes nombran el problema y dan el ejemplo cuando procede
(`'type:' needs a value (example: type:pdf)`), y están en inglés técnico
consistente con el resto de mensajes del CLI.

## Tests (316 en verde; +91 sobre las 225 de la fase 011)

**`test_query_parser.py` (72)** — suite pura, sin base de datos:

- Precedencia (`OR` vs `AND` implícito/explícito, agrupación), mayúsculas
  en palabras clave, paréntesis que reordenan.
- Negación: izamiento, negaciones múltiples combinadas con `OR` en un
  único operando, negación de un grupo, negative-only sin términos.
- Frases, escapes con `\`, comilla sin cerrar, Unicode.
- Entradas degeneradas: `""`, `"   "`, `()`, `"`, `*`, `';`, `"==="`, y un
  grupo vacío dentro de una consulta.
- Campos: columna FTS, valor entrecomillado con espacios, campo
  desconocido que degrada, `3:15`, y el enrutado de campos conocidos a
  nodos `Filter` normalizados.
- Filtros SQL: normalización de `type`, `source` validado contra los
  proveedores, fechas estrictas, operadores y unidades binarias de
  `size`, combinación con texto.
- 18 casos de feedback estructural (operador colgante, paréntesis
  desbalanceados, negación colgante, filtros dentro de `OR`, negación de
  filtro) comprobando el mensaje, no solo la excepción.
- Invariante de seguridad de la traducción sobre 10 entradas hostiles.

**`test_advanced_search.py` (19)** — extremo a extremo sobre un índice real:

- Cada filtro cambia el conjunto de resultados: `type:`, `source:`,
  `path:`, `name:`, `after:`, `before:` (incluida la exclusive del día
  límite en ambos sentidos), `size:`.
- Solo-filtros: `score == 0.0`, sin snippet, orden por recencia.
- Texto + filtro: intersección, no unión.
- Operadores: `OR`, `AND`, precedencia, agrupación, frases (adyacencia y
  orden, con la nota de que el nombre del fichero no puede demostrar el
  orden porque `unicode61` parte `bjt_amplificador` en dos tokens).
- Negación: término, columna (`-name:2020`), negative-only y
  `mux -cmos`.
- Feedback: `last_query_error` del servicio (se limpia al acertar), CLI
  con `error:` y código 1 sin traceback, y CLI con consulta válida
  imprimiendo resultados.
- Una consulta inválida no llega al grabador de métricas.
- 4 entradas de inyección estilo SQL/operador no rompen ni alteran el
  índice.

## Criterios de aceptación (spec 012)

| Requisito | Estado |
|---|---|
| Búsquedas simples compatibles | ✅ `BJT`, `C++`, `foo:bar`, `"notes`, `()`, `*` — suite histórica intacta |
| Sintaxis determinista y documentada | ✅ tabla de semántica + docstrings por etapa |
| Entrada inválida nunca rompe CLI/GUI/fondo | ✅ `QueryError` con mensaje; test de CLI sin traceback; GUI muestra el motivo |
| Semántica compartida entre CLI y GUI | ✅ una sola capa `query/` + un solo motor |
| Sin SQL/FTS interpolado desde texto crudo | ✅ plantillas estáticas + parámetros ligados + invariante testeado |
| Arquitectura en capas (léxico, AST, validación, traducción, ranking) | ✅ cinco módulos, cinco responsabilidades |
| Suite del parser completa (válidas/inválidas, comillas, Unicode, operadores, precedencia, escapes, metacaracteres, vacías) | ✅ 72 tests |
| Tests de integración de cada filtro | ✅ 19 tests |
| Sin dependencias, red ni servicios nuevos | ✅ solo biblioteca estándar |

## Limitaciones (deliberadas)

- **Negación sin término positivo devuelve vacío.** La alternativa
  correcta (`NOT EXISTS` sobre FTS5 en la ruta de solo-filtros) es viable
  pero añade una segunda ranura de parámetros a `FILTER_ONLY_SQL`; se
  deja fuera hasta que exista una necesidad real, y el comportamiento
  está documentado y testeado en lugar de ser una sorpresa.
- **`NOT` no es una palabra clave.** `bjt NOT cmos` busca las tres
  palabras (como antes de esta fase); la exclusión se expresa con `-`.
  Los operadores del lenguaje son `AND`, `OR` y `-`.
- **Sintaxis FTS5 cruda deja de colarse.** Antes, `match = query` dejaba
  pasar operadores nativos; ahora el motor construye el `MATCH`. Quien
  escribiera `NEAR(a b, 5)` obtendrá una conjunción de palabras, no un
  `NEAR`. Es el precio de la garantía de no-inyección.
- **Fechas por día de calendario**, no por instante: `after:2026-01-01`
  compara los 10 primeros caracteres de `modified_at`, así que depende de
  que el indexador escriba la fecha en formato ISO (verificado).
- **Sin sinónimos ni tolerancia a errores.** El único ensanchado de
  recuperación sigue siendo el de contexto (fase 008), que solo recupera
  más candidatos y nunca puntúa con los términos expandidos.
- El cuadro de búsqueda de la GUI no resalta la sintaxis (queda para la
  fase 017 de UX).

## Cómo ejecutar

```bash
.venv\Scripts\python -m pytest tests\test_query_parser.py tests\test_advanced_search.py
.venv\Scripts\python -m pytest tests\ -q
.venv\Scripts\python -m pyflakes src tests benchmarks

# ejemplos de la nueva semántica
universal-search search "bjt type:txt after:2026-01-01"
universal-search search "bjt -cmos size:>10KB"
universal-search search '"ebers moll" OR "gunn effect"'
universal-search search "type:pdf" --explain   # sin texto: score 0.0, orden por fecha
```
