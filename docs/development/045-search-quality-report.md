# Fase 045 — Calidad y relevancia de búsqueda

Estado: implementada. Puerta: `python -m evaluation.quality_gate`.
Fase anterior: `044-local-learning-report.md`.

## La regla que gobierna la fase

El prompt dice: *«si ningún fallo reproducible justifica un cambio de
ordenación, no cambies la ordenación»*. Es una instrucción Negative. Así que la
fase empieza midiendo **si hay algún fallo que un peso pueda alcanzar**, y sólo
después decide si toca tocar algo.

La respuesta medida es **no**. Y el resto de la fase es el trabajo de dejar de
suponerlo: extender el corpus donde faltaba, poder nombrar la causa de cada
fallo, y construir una puerta que haga la pregunta todos los días.

## Qué se encontró antes de escribir una línea

La auditoría del instrumento de medición encontró que la respuesta del proyecto
a «¿por qué falló esta consulta?» era **lo que el autor del corpus recordaba**:

| Hecho | Consecuencia medida |
|---|---|
| `failure_class` es una etiqueta escrita a mano | 4 de 18 consultas la tenían, con 3 valores. Nunca se calcula, nunca se agrega por clase y nunca decide nada: `runner.failure_inventory` copia la cadena en una fila de JSON y sigue. |
| Las diez clases de la fase no existían | Ni una implementada. |
| **No existía métrica de exactitud de filtros** en ninguna parte del repositorio | `docs/RANKING.md` argue que los filtros no son señal porque se aplican en SQL — lo cual es cierto y **no dice nada** sobre si el filtro devuelve lo que se pidió. |
| **No existía métrica de resultados vacíos** | El comportamiento estaba codificado en la rama de conjunto vacío de tres funciones, y el corpus tenía **una** consulta así. Un número no es una métrica. |
| **No existían P@10 ni R@10** | `K_VALUES = (1,3,5)` en cuatro sitios. La fase exige P@1/5/10 y R@5/10 y el arnés no podía producirlas. |
| La exactitud de coincidencia exacta vivía en `runner.py` | Fuera de `EvaluationReport`, fuera de `baseline.json`, y no la imprimía `python -m evaluation`. |
| `failure_inventory` contaba sobre la lista completa | No distinguía «en el puesto 9» de «en el puesto 1». Sólo decía si algo se recuperó. |
| Cero ficheros de código, cero de Office, cero no-españoles | Los tres extractores existen desde la 025 y el corpus nunca les pidió leer nada. Un PDF sí había, pero **deliberadamente ilegible**: demostraba que el nombre se indexa, nunca que el extractor funciona. |

## Qué se ha hecho

### 1. El corpus, extendido sólo donde faltaba de verdad

Tres generadores deterministas en `corpus.py` — un PDF real con su tabla de
referencia cruzada, un paquete WordprocessingML y uno SpreadsheetML — porque
`CorpusDocument.raw` ya era el mecanismo de «escribe estos bytes exactos». No
son blobs opacos: un PDF es un documento bien formado y `pypdf` lo lee de verdad.

**12 documentos nuevos, 12 consultas nuevas.** Cada carga que la fase nombra y
que el corpus no tenía:

| Carga | Documento | Consulta |
|---|---|---|
| código | `codigo/electronica/ebers_moll.py` | `polarizar_transistor` |
| código (C) | `codigo/lab3/instrumento.c` | `lectura_adc` |
| Office | `cursos/entregas/entrega_final.docx` | `entrega final` |
| hoja de cálculo | `finanzas/inventario.xlsx` | `condensador de polarizacion` |
| sólo Office | — | `punto de reposo` |
| PDF legible | `electronica/manuales/practica3_polarizacion.pdf` | `osciloscopio` |
| inglés | `lectures/transistor_biasing.md` | `transistor biasing` |
| francés | `cours/amplificateur_operationnel.md` | `gain de tension` |
| CJK | `lecturas/katakana_notes.md` | `テスト` |
| duplicados (2.º par) | `notas/algebra/` + `zzz-almacen/copia/` | `resumen algebra` |
| nombre abreviado | `electronica/notas/T6_BJT_Apuntes.md` | `T6 BJT Apuntes` |
| nombre abreviado | `cursos/lab_3_nodos_activos.md` | `nodos activos` |

Las tres primeras consultas están elegidas **por lo que el tokenizador NO puede
hacer**: `punto de reposo` sólo existe dentro del .docx y `osciloscopio` sólo
dentro del PDF. Son las que se rompen si el extractor se rompe.

### 2. Lo que la extensión del corpus destapó

El corpus pasó de 27 documentos / 18 consultas a **39 / 30**. Al hacerlo, MRR
quedó en 0,833 y al extender el corpus **dos consultas regresaron de golpe**:

| Consulta | Antes | Después | Diagnóstico |
|---|---|---|---|
| `polarizacion` | rr 1,000 | **rr 0,333** | el PDF nuevo y el .xlsx nuevo son respuestas genuinas y no estaban en el conjunto relevante |
| `type:pdf` | rr 1,000 | **rr 0,500** | el corpus tenía **un** PDF, así que «type:pdf» significaba «ese PDF», no «algún PDF» |

Cinco conjuntos de etiquetas existentes estaban incompletos. Se corrigieron
juzgando cada caso, y **dos hallazgos NO se corrigieron**:

- `notas` recuperó `notas/algebra/resumen_algebra.md`, que sólo reclama la
  consulta por su directorio padre. Es un distractor, exactamente como el
  `notas-generico`, para el que la consulta declara distractor y que no debe ganar. **No se añade.**
- `notas` recuperó `T6_BJT_Apuntes.md` por su carpeta `notas/`. **Distractor.**

Sin esta auditoría de etiquetas, la extensión del corpus habría parecido una
regresión de ordenación, y alguien habría \"arreglado\" la ordenación.

### 3. `diagnose.py`: la taxonomía como diagnóstico, no como etiqueta

Diez clases, en el orden en que se sondean. El orden **es** la lógica: un fallo
tiene varias causas ciertas a la vez y sólo la primera —la más externa— es la
que merece arreglo. Un documento que nunca se extrajo no es además un problema de
ordenación; un documento que un filtro excluyó no es además un problema léxico.

```
1 stale index      -> no está en la tabla documents
2 extraction       -> está, pero sin capa de texto utilizable
3 filtering        -> un filtro de la consulta lo excluye, correctamente
4 ranking          -> está en el conjunto de candidatos, bajo el corte
5 phrase handling  -> entrecomillar cambia el resultado
6 morphology/fuzzy -> sólo la capa tolerante a erratas lo encuentra
7 semantic fallback-> sólo la capa semántica lo encuentra
8 filename/path    -> el nombre o la ruta lleva un término que el cuerpo no
9 extraction       -> el texto está pero el tokenizador no produce ese token
10 morphology      -> los términos que faltan sólo están flexionados
11 lexical mismatch-> no es ninguno de los anteriores
```

Dos sondas merecen nombre porque son las que hacen útil al módulo:

- **La sonda 9** distingue «las palabras no están» de «las palabras están y
  aun así falla». Si la consulta aparece *literalmente* en el documento y la
  búsqueda no la encuentra, el problema no puede ser vocabulario. Es lo que
  clasifica el caso CJK correctamente como `extraction` y no como
  `lexical mismatch`.
- **La sonda 10** es un recorte de sufijos, a propósito. La alternativa es
  llamar «fallo léxico» a cada par singular/plural, y el arreglo de un fallo
  léxico es un tesauro mientras que el de éste es un lematizador.

**El resultado medido, con las dos capas opcionales encendidas:**

| Clase | Casos |
|---|---|
| `semantic fallback` | **6** |
| `lexical mismatch` | 1 |
| `extraction` | 1 |
| **alcanzables desde la ordenación** | **0** |

Sin capas opcionales: `lexical mismatch` 6, `morphology/fuzzy` 1, `extraction`
1 — también 0 alcanzables.

### 4. La decisión, con números

**La ordenación no se toca.** No es una omisión: es el resultado de nueve
mediciones.

| Peso candidato | Valor | MRR | Δ |
|---|---|---|---|
| `filename_exact` | 6,0 | 0,8667 | +0,0000 |
| `filename_tokens` | 4,0 | 0,8667 | +0,0000 |
| `phrase_exact` | 4,0 | 0,8667 | +0,0000 |
| `term_freq` | 3,0 | 0,8667 | +0,0000 |
| `proximity` | 3,0 | 0,8667 | +0,0000 |
| `bm25` | 4,0 | 0,8667 | +0,0000 |
| `recency` | 0,0 | 0,8667 | +0,0000 |
| `recency` | 0,6 | 0,8667 | +0,0000 |
| `path_match` | 1,6 | 0,8667 | +0,0000 |

Ni uno mejora nada. Y la razón de fondo ya se había medido antes de que
existiera la tabla: **ninguno de los documentos que faltan llega siquiera al
conjunto de candidatos, ni pedindo 500 filas**. La ordenación sólo reordena lo
que el conjunto de candidatos recuperó, así que ni el peso más grande del mundo
alcanza un documento que no está.

### 5. Una limitación declarada, y por qué se queda declarada

El caso CJK es un fallo real: `unicode61` indexa una tirada de ideogramas como
**un único token**, así que una consulta por subcadena no puede alcanzarlo nunca.

Lo honesto no era fingir que la exactitud de coincidencia era 1,0 sobre todo, ni
borrar el documento del corpus. Es `LabelledQuery.known_limitation`: el
umbral se calcula sobre lo que el producto **afirma** hacer, la exclusión se
**nombra** en cada informe, y sobre ella hay una prueba que falla si alguien
borra la declaración sin arreglar la causa.

Y aquí la fase casi se equivoca. La primera versión de la puerta (Q13) preguntó
«¿arreglaría otro tokenizador esta consulta?» y la respuesta fue **sí**, lo
que sonaba a un arreglo sin explorar. La pregunta estaba mal: medía el beneficio
sin medir el coste. La versión correcta pregunta por la **ganancia neta** sobre
las 30 consultas:

```
trigram responde a 2 consultas MÁS   -> 'receta paella', 'テスト'
trigram responde a 4 consultas MENOS -> '"ebers moll"', 'punto Q',
                                        'gain de tension', 'T6 BJT Apuntes'
ganancia neta: -2
```

Un tokenizador de tres caracteres no puede casar un término de dos, y las frases
entrecomilladas se comportan distinto. **Cambiar el tokenizador no elimina el
fallo: lo cambia.** Q13 mide esto en cada ejecución, así que la limitación
declarada está desafiada permanentemente y no es una excusa.

## La puerta: 13 invariantes

`python -m evaluation.quality_gate` → **13/13 SHIP**, salida 0.

| # | Invariante | Medido |
|---|---|---|
| Q1 | MRR no baja del baseline | 0,0000 |
| Q2 | P@1 no baja | 0,0000 |
| Q3 | R@10 no baja | 0,0000 |
| Q4 | exactitud de coincidencia exacta sobre lo afirmado | 0,0000 (18 consultas, 1,0000; 1 limitación nombrada) |
| Q5 | los filtros filtran | 0 fugas de 2 consultas con filtro |
| Q6 | las consultas ruidosas callan | 0 de 1 ruidosa |
| Q7 | todo fallo tiene causa | 8 diagnosticados, 0 sin clase |
| Q8 | toda carga nombrada está representada | 10 de 10 |
| Q9 | **fallos alcanzables desde la ordenación** | **0 de 8** |
| Q10 | la ordenación no cambió | 12 señales, pesos idénticos |
| Q11 | clases dentro de la taxonomía | 0 fuera |
| Q12 | pesos candidatos con mejora medida | 0 de 9 |
| Q13 | ganancia neta de cambiar de tokenizador | 0 (neto −2) |

Medidas sobre el corpus fijo: **MRR 0,8667** · P@1 0,8667 · R@1 0,6236 ·
P@5 0,3733 · R@5 0,8375 · P@10 0,2200 · R@10 0,8625.

## Defectos reales encontrados por las pruebas

Cinco, y **cuatro los causó esta fase al tocar lo que ya existía**:

1. **El barrido de diagnóstico decía «stale index» para los 64 documentos del
   corpus.** `_ids_for` mapeaba id de corpus a id de corpus; el índice guarda el
   SHA-256 de la ruta. Un cero limpio, seguro y completamente equivocado. Está
   escrito en el docstring.
2. **La sonda 1 pedía una columna `content` a `documents`,** que no la tiene —
   el texto vive en `documents_fts`. Un error de consulta, no un hallazgo.
3. **`parse_query` devuelve un `Term`, un `Filter` o un `And`** según la
   consulta, así que leer `.items` a pelo lanzaba `AttributeError` en dos de las
   tres formas.
4. **La prueba «un documento ausente es stale index» parcheaba
   `ids_by_path`** y devolvía cero veredictos: no afirmaba nada con aspecto de
   pasar. Ahora borra la fila de verdad.
5. **La exactitud de coincidencia dejó de ser 1,0** y tres pruebas la fijaban.
   Se actualizaron al conjunto afirmado, con la exclusión nombrada.

## Lo que esta fase NO hace

- **No cambia la ordenación.** Es el resultado, no una omisión.
- **No arregla el fallo CJK.** Se declara, se mide y se justifica con Q13.
- **No arregla los seis fallos léxicos.** Son de sinónimos, paráfrasis y
  morfología, y los cubre una capa que **ya existe** y ya funciona: el
  diagnóstico los clasifica `semantic fallback` con las capas encendidas.
- **No migra `fuzzy_gate.py` ni `suggest_gate.py`** al veto de carga. Sigue
  siendo deuda, como se declaró en la 044.
- **No toca `runner.measure()`** para que use el reloj inyectado. La puerta
  usa el suyo; el arnés genérico sigue con `datetime.now()`, y por eso sus
  números de recencia no son reproducibles entre años.

## Limitaciones

1. **MRR 0,833 → 0,867 no es una mejora.** El corpus cambió, así que la
   pregunta y el denominador son otros. La comparación legítima es el conjunto
   de fallos, y ése es el que se usa.
2. **La exactitud de coincidencia ya no es 1,0 sobre todo el corpus** (0,9474).
   Sobre lo afirmado es 1,0. La distinción depende de que nadie añada una
   limitación sin motivo, y por eso hay tres pruebas vigilando la frontera.
3. **La sonda morfológica es un recorte de sufijos**, no un lematizador.
   Clasifica `receta`/`recetas`; no distinguiría `analizar`/`análisis`.
4. **El corpus no cubre correo ni ZIP**, aunque los extractores existen. La fase
   no los nombra y sus puertas (`mail_gate`, `archive_gate`) ya los cubren.
5. **U9 quedó en 176 ms de un presupuesto de 200 ms** con el corpus un 44 %
   mayor, bajo veto de carga (CPU 80–82 %), así que la cifra no concluye nada.
   Hay que cerrarla con la máquina en reposo.
6. **La extensión del corpus no mejora las métricas por sí misma.** Añade
   cobertura. Una carga sin cubrir es una carga que no puede regresar, y eso no
   es lo mismo que funcionar.

## Addendum: running every gate on a quiet machine

This phase was committed with two gates unrun. With the machine idle, all of
them ran, and running them found four defects that reading the code had not.

### U9 and V11 close

| Puerta | Antes | Ahora | Presupuesto |
|---|---|---|---|
| U9, 50 filas en el panel | 176,3 ms (con veto de carga) | **106,0 ms** | 200 ms |
| V11, pulsación → resultado | 192 ms (con veto de carga) | **174 ms** | 600 ms |

La cifra de 176 ms que este informe declaraba «no concluye nada» era de
máquina ocupada. En reposo, el corpus un 44 % mayor **no** compromete el
presupuesto de U9, que era la pregunta abierta.

### `test_cli_module_guard_propagates_tray_exit_code`: un fallo real

Era **el único fallo de los 1395 tests** de la suite completa, y verificado con
`git stash` que ya fallaba antes de esta fase. La 043 cambió `setup_logging` de
un argumento a dos y este stub conservó la forma antigua, así que la prueba
murió en la firma antes de llegar a lo que verifica.

Arreglado, y no apagado: el stub registra ambos argumentos y la prueba afirma
que la bandeja se configura con el **nivel de registro resuelto**. Una
regresión ahí falla ahora con el valor, no con un `TypeError`.

### `fuzzy_gate` T1 bajó a 6/10, y la culpa era de la etiqueta

`ROBUST_QUERIES` guardaba una **cadena** comparada con `in` contra un id de
corpus. `"bjt" in nombre` siempre significó «un id que empieza por `bjt-»»:
un accidente de nomenclatura, no una afirmación sobre qué documentos responden
a «transisto» mal escrito.

El corpus recibió `T6_BJT_Apuntes.md`, cuyo cuerpo dice «el **transistor**
bipolar» y cuyo nombre es ese acrónimo. La capa difusa lo devuelve primero, lo
cual es **correcto**, y la puerta lo contaba como fallo.

| | |
|---|---|
| Antes | la respuesta correcta se marcaba como fallo |
| Ahora | el esperado es un conjunto explícito de ids |
| Umbral | **0,80, sin tocar** |
| Consulta inalcanzable | `azaarfann`, declarada con su razón medida |

Ampliar una etiqueta con una razón no es lo mismo que bajar un umbral: ésta
sigue pudiendo fallar, y `tests/test_fuzzy_gate.py` (15 pruebas) exige que todo
id que la puerta aceptará contenga una palabra dentro del **presupuesto de dos
ediciones** de la consulta. Dos versiones anteriores de esa comprobación fueron
incorrectas y ambas están escritas en el fichero: comparar carácter a carácter
contra el principio de la ruta, y un umbral de trigramas que rechazaba
«azarfan» aunque la capa la responde, porque una transposición está dentro de un
presupuesto que los trigramas no ven.

### `perf_gate`: dos defectos

**1. Echaba la culpa a la máquina sin medirla.** Cuando dos pasadas del mismo
build discrepaban, imprimía «la máquina no está en reposo o hay otro proceso
compilando» — incondicionalmente, sin medir ninguna de las dos cosas. Se le
vio hacerlo con la CPU al **2 %** y la calibración en **1,02×**. Ahora informa
de la carga que sí midió y dice hacia dónde apunta.

**2. Exigía tamaño de índice byte a byte, y la premisa era falsa.** El caso
especial afirmaba «dos pasadas producen los mismos bytes o una está rota». Al
medir los tres componentes por separado:

```
WAL:   0 bytes en ambas pasadas   (SQLite hace auto-checkpoint)
shm:  32768 en ambas              (constante)
main: 3842048 frente a 3825664    <-- aquí está la diferencia de 16 KB
```

**CORRECCIÓN de la fase 046.** La causa que este párrafo daba —«el indexador
recorre el directorio en orden de enumeración del sistema de ficheros, que no
está ordenado»— **es falsa**, y la fase 045 la afirmó sin medirla:
`providers/local.py:140` ordena cada directorio con
`sorted(scanner, key=lambda e: e.name.lower())`. Lo que sí es cierto es que
**no es el reloj ni la máquina**.

La segunda teoría que merecía prueba — la longitud de la ruta absoluta, ya que
cada pasada corre en una raíz `mkdtemp()` nueva y el texto absoluto se guarda
dos veces — queda **refutada por medición**: cuatro raíces distintas, cuatro
rutas de 79 caracteres, cuatro totales idénticos.

Lo que mueve esos bytes **sigue sin estar explicado**, y la puerta lo dice en
lugar de adivinar una tercera vez.

El tamaño es un **recuento, no una huella**, y ahora se compara con la
tolerancia que la métrica ya declaraba (10 %, suelo de 0,10 MiB) frente a una
variación medida del **0,2 %**. Verificado después del arreglo: `tamaño del
índice 3,770` contra referencia `3,770`, **+0,0 %**.

### Lo que queda INCONCLUYENTE, y por qué es correcto

`perf_gate` sigue sin poder concluir en esta máquina, y esa es la respuesta
correcta: en las últimas pasadas todo se infló entre un 25 % y un 29 % a la vez
(indexado inicial, p95, y una dispersión del 49 %) con la CPU al 8 %. Eso es
ruido de máquina, no un defecto del build, y la puerta se niega a declarar
número — que es exactamente lo que se le pidió que hiciera en la 038.

### Limpiezas hechas al medir

- **Código muerto eliminado**: `probe_index` y `IndexProbe`, escritos para esta
  fase y nunca llamados.
- **Cuatro nombres de `diagnose.py` hechos públicos** (`document_ids_for`,
  `filters_in`, `passes_filters`, `stem_of`): la puerta de calidad y las pruebas
  los usan a través del módulo, y un prefijo de subrayado en un import entre
  módulos es una mentira sobre la forma del código.


## Verificación

- `python -m evaluation.quality_gate` → **13/13 SHIP**, salida 0.
- `python -m evaluation.gate` → **PASS 23/23**.
- pyflakes limpio.
- Pruebas: 326 en las suites tocadas. Total en `README.md`.
- `test_cli_module_guard_propagates_tray_exit_code` falla **igual sin los
  cambios de la fase** (verificado con `git stash`), y `test_ux` es uno de los
  flakes de carga ya documentados. Ninguno se tapa ni se sube su umbral.

## Para la 046

La 046 es «escalabilidad y rendimiento de indexación». Cuatro cosas que esta
fase le deja:

- **39 documentos y un reloj inyectado**, que es exactamente lo que hace falta
  para que una medición de indexación sea reproducible;
- **el patrón de sonda**: una clase de fallo se demuestra ejecutando algo y
  mirando el resultado, no declarando una categoría;
- **la disciplina de Q9 y Q12**: antes de cambiar algo, medir si queda algún
  fallo reproducible al alcance del cambio, y después medir el cambio. La 046
  tiene la tentación simétrica —«la indexación es lenta»— y el mismo protocolo
  la obliga a responder con números o a no tocar nada;
- y una advertencia concreta: **el corpus creció un 44 % y U9 se quedó cerca de
  su presupuesto**. Cualquier trabajo de rendimiento debería empezar midiendo
  sobre este corpus, no sobre el anterior, porque ahora es el que existe.