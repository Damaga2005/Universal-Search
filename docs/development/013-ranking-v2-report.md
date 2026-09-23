# Fase 013 — Search Quality & Ranking v2

## Qué se entregó

1. **Paquete de evaluación** (`evaluation/`, fuera de la aplicación):
   corpus sintético **etiquetado** (16 documentos, 13 consultas), métricas
   **Precision@K / Recall@K / MRR** y captura del desglose por señal de
   cada resultado. Se ejecuta con `python -m evaluation`.
2. **Experimento de punto de vuelco** (`evaluation/experiments.py`,
   `--flip PESO CONSULTA`): responde las dos preguntas que las métricas
   agregadas no pueden responder — *¿la señal es portante?* (ponerla a 0
   ¿cambia algún ranking?) y *¿cuánto margen tiene?* (hasta qué valor
   cambia el top medido).
3. **Fixture de regresión** (`evaluation/baseline.json`): fija el top-3 de
   cada consulta etiquetada y los agregados. Mover un ranking rompe un
   test hasta que se regenere el baseline *y* se justifique con una
   medición.
4. **45 tests nuevos**: `test_ranking_signals.py` (23) — un test por señal,
   robustez (metadatos ausentes, documento de 100k tokens, longitudes,
   clamps) y explicabilidad; `test_evaluation.py` (22) — definiciones de las
   métricas, contrato del corpus, reproducibilidad y el baseline.
5. **Documentación**: `docs/RANKING.md` reescrito con la evidencia medida,
   arquitectura, ROADMAP, índice de docs y README.

**Decisión de fondo, sostenida por mediciones: no se cambia ningún peso.**
La fase entrega el instrumento y la evidencia, no un ajuste de Lottery.

## Por qué P@K no bastaba (y qué se hizo al respecto)

La primera medición dio **P@1 = 1.000 y MRR = 1.000** sobre el corpus
inicial: con esas cifras, *ningún* cambio de pesos puede mejorar la métrica
y *ninguna* métrica puede detectar un cambio dañino. Peor: al repetir el
conjunto con pesos deliberadamente escorzados (`path_match` 0.8→2.0,
`filename_exact` 3.0→6.0, `recency` 0.3→0.0 y →1.5) los agregados y los
top-3 **no se movieron**. El instrumento no era sensible; el corpus tampoco
tenía casos donde los pesos compitan.

Dos arreglos, en este orden:

1. **Corpus con evidencia en disputa.** Se añadieron documentos que fuerzan
   una decisión de peso en lugar de heredarla:
   - `personal/etiquetas/informe.txt` (reclama *informe* con el nombre,
     contenido vacío) frente a `trabajo/bitacora.md` (repite *informe* ocho
     veces, nombre ajeno): decide `filename_exact`.
   - `lecturas/diagrama.md` (10 años) frente a `zzz-almacen/diagrama.md`
     (10 días), contenido idéntico y **orden alfabético opuesto al
     cronológico**: decide `recency` con el desempate por ruta como
     respaldo.
2. **Medición de margen en vez de métrica agregada.** `--flip` biseca el
   valor de un peso y devuelve dónde cambia el ranking medido, en las dos
   direcciones.

## Resultados medidos (corpus etiquetado, 13 consultas)

| Métrica | Valor |
|---|---|
| Precision@1 | **1.000** |
| Precision@3 | 0.718 |
| Precision@5 | 0.523 |
| Recall@3 | **0.923** |
| Recall@5 | 0.987 |
| MRR | **1.000** |
| Consultas cuyo primer resultado es relevante | 13 / 13 |
| Consultas con coincidencias irrelevantes | 1 (`notas`), margen **+0.264** |

Recall@1 (0.603) no es un defecto: varias consultas tienen 4–6 documentos
relevantes, así que un único resultado no puede "recordarlos" todos. Por
eso el MRR y el recall@3 son las cifras de referencia, y por eso el
instrumento añade el **margen de relevancia** (mejor relevante − mejor
irrelevante), que sigue siendo útil cuando las métricas saturan.

## Orden por consulta (top-3) y qué lo decide

| Consulta | Top-3 | Señal que decide |
|---|---|---|
| `BJT` | amplificador, notas, modelo | `term_freq` + `filename_tokens`; el log de 4 000 palabras queda 4.º y la carpeta-only 6.ª |
| `"ebers moll"` | ebers-exacto, bjt-modelo, bjt-amplificador | `filename_exact` (3.0) + `phrase_exact`; el documento con las palabras separadas queda 4.º |
| `ebers moll` | idéntico | conjunción: mismo conjunto, FTS ordena por bm25 |
| `CMOS` | cmos-mux, cmos-logica | longitud de documento (bm25) |
| `MUX` | mux-generico, cmos-mux | ambos relevantes, orden sin intrusos |
| `informe` | informe, informe-etiqueta, bitacora | `filename_exact` > `filename_tokens` > contenido repetido |
| `diagrama` | almacen (nuevo), lecturas (10 años) | `recency`; con peso 0 invierte a alfabético |
| `polarizacion` | bjt-notas, bjt-modelo, bjt-amplificador | sin ayuda del nombre, decide bm25/recencia |
| `notas` | bjt-notas, **notas-generico** | el intruso de ruta genérica entra 2.º y **pierde por 0.264** |
| `presupuesto` | presupuesto | control: los documentos ajenos nunca aparecen |
| `bjt type:txt` | bjt-carpeta | filtro + texto |
| `type:pdf` | bjt-datasheet | solo filtro: sin texto, score 0.0, orden por fecha |
| `zzz no existe` | (nada) | conjunto de relevancia vacío: 1.0 solo si no recupera nada |

## Margen medido de cada peso (la tabla que decide cambios futuros)

| Peso | Consulta | ¿Portante? (a 0) | Vuelco | Margen |
|---|---|---|---|---|
| `recency` | `diagrama` | **sí** (invierte a orden alfabético) | — | ≥ 26.7× |
| `path_match` | `BJT` | no | **5.75** | **7.2×** |
| `path_match` | `notas` | no | — | ≥ 10× (neutral: ambos saturan la señal) |
| `term_freq` | `BJT` | no | **6.79** | **4.5×** |
| `bm25` | `BJT` | **sí** (sube el log largo) | — | ≥ 4.0× |
| `phrase_exact` | `"ebers moll"` | **sí** (entra el disperso) | — | ≥ 4.0× |
| `proximity` | `"ebers moll"` | **sí** (entra el disperso) | — | ≥ 5.3× |
| `filename_tokens` | `informe` | **sí** (el documento de contenido toma #1) | — | ≥ 4.0× |
| `filename_exact` | `informe` | **sí** | **3.67** | **1.2×** |
| `doc_type` | `BJT` | no | — | ≥ 16× |

Conclusiones:

- **Ocho de las diez señales textuales son portantes**: ponerlas a cero
  cambia algún ranking medido. `path_match` y `doc_type` son guardarraíles,
  no motores de orden, que es exactamente su papel.
- **La garantía "la ruta no domina al contenido" queda medida**: un
  documento que solo casa por su carpeta necesita que `path_match` pase de
  0.8 a **5.75** (×7.2) para desplazar a un documento con contenido.
- **`filename_exact` es el número más tenso del sistema** (+22 % intercambia
  los dos documentos con nombre exacto). Es también una decisión de diseño
  deliberada: un fichero llamado exactamente lo que buscas va primero.
- **Ningún cambio de pesos está justificado**: mover cualquiera de ellos
  exige pasar una frontera lejana (×4.5–×26.7) o invierte una decisión
  intencionada. Por eso los pesos no se tocan.

## Señales deliberadamente *no* añadidas

- **Estructura de la consulta** (AND/OR/paréntesis/frase): ya está
  representada por `phrase_exact`, `proximity` y por el *pool* de candidatos
  que FTS recupera antes de puntuar. Una tercera bonificación contaría dos
  veces la misma evidencia.
- **Coincidencias de filtro**: `type:`, `source:`, `after:`, `before:` y
  `size:` se aplican en SQL **antes** de ordenar, así que todos los
  candidatos satisfacen todos los filtros pedidos: la señal valdría
  constantes 1.0 y no cambiaría ningún orden. Se ejercitan igual
  (`bjt type:txt`, `type:pdf`) para demostrar que filtro y ranking componen.

Ambas decisiones quedan escritas en `docs/RANKING.md`, no solo aquí.

## Doble conteo detectado y no corregido (con medición)

`path_match` no es la única señal que refleja la ruta: **FTS5 también cuenta
la coincidencia de ruta dentro de `bm25`**. Se ve en el intruso de
`notas`: `notas-generico` no casa por nombre ni por contenido, y aun así
recibe `bm25 = 1.468` de 2.0 posibles, casi todo por la carpeta. Hoy eso no
gana nada (`+0.264` de margen), pero es un doble conteo real.

No se corrige en esta fase: separarlo exigiría una segunda consulta FTS con
filtro de columna (el doble de trabajo de recuperación) para arreglar un
problema que la medición demuestra inexistente hoy. Queda documentado como
deuda consciente, no como descuido.

## Robustez cubierta por tests

- **Metadatos ausentes**: `modified_at` nulo, vacío o inválido → recencia
  neutra; sin contenido → `doc_type` 0.4 y el resto de señales a 0 sin
  excepción; un candidato completamente vacío puntúa solo con la base
  neutra de metadatos (`0.0589`), dentro de [0, 1].
- **Documentos largos**: 100 000 tokens con una mención → `term_freq`
  diluida (0.1), `bm25` acotado, todas las señales en [0, 1].
- **Longitud**: los términos de un carácter no cuentan para `path_match`
  (una letra de unidad no puede dominar).
- **Puntuación normalizada**: con pesos personales activados (0.5/1.0) y
  boosts en [0, 1], el score sigue en [0, 1] porque el denominador crece.
- **Determinismo**: mismas entradas → mismas señales; `clear_caches()` no
  cambia ningún resultado; dos mediciones seguidas no mueven una posición.
- **Inmutabilidad**: `RankingWeights` es `frozen`; `DEFAULT_WEIGHTS.total`
  está fijado a 14.0 por test; los nombres de señal y los campos de peso no
  pueden divergir (mismo test).

## Criterios de aceptación (spec 013)

| Requisito | Estado |
|---|---|
| Ranking medible e inspeccionable | ✅ P@K/R@K/MRR + desglose por señal en el JSON; `--explain` sin cambios |
| Corpus de evaluación etiquetado | ✅ 16 documentos / 13 consultas, sintéticos, deterministas |
| Desempate determinista | ✅ ruta ascendente + test de empates y de repetibilidad |
| Cobertura de regresión de los cambios | ✅ `evaluation/baseline.json` + 45 tests nuevos |
| La búsqueda existente sigue funcionando | ✅ 361 tests en verde, sin cambios en el motor |
| Sin IA ni API externa | ✅ solo biblioteca estándar + SQLite |
| Pesos/configuración centralizados | ✅ `RankingWeights` congelado, invariante 14.0 por test |
| Un test por señal, interacciones, orden, métricas | ✅ 23 + 22 tests |

## Limitaciones (deliberadas)

- El corpus es pequeño (16 documentos) y **sintético**: las métricas
  absolutas (P@3 = 0.718) no son un benchmark de producto, son un
  instrumento de regresión. Un corpus grande y anotado a mano es trabajo de
  producto, no de agente.
- Las métricas agregadas saturan (P@1 = MRR = 1.000). Por eso el
  instrumento insiste en el **top-3 del baseline**, en el **margen de
  relevancia** y en el **punto de vuelco**; las tres siguen siendo
  sensibles cuando las métricas ya no lo son.
- El punto de vuelco se mide sobre el top-3 de una consulta concreta, no
  sobre el comportamiento agregado del sistema.
- `doc_type` y `source` siguen siendo seudos de diseño (0.4 y 1.0): el día
  que exista una ponderación por proveedor tendrá su corpus etiquetado, no
  un número inventado.
- El doble conteo de la ruta en `bm25` sigue abierto (ver arriba).

## Cómo ejecutar

```bash
python -m evaluation                                  # informe completo
python -m evaluation --json results.json              # números + puntos por señal
python -m evaluation --flip path_match BJT            # margen de un peso
python -m evaluation --write-fixture evaluation/baseline.json
.venv\Scripts\python -m pytest tests\test_evaluation.py tests\test_ranking_signals.py
```
