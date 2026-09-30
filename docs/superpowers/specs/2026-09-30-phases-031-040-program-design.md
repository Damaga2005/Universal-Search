# Diseño del programa 031–040 — Universal Search 2.x

Estado: aprobado para implementar
Fecha: 2026-09-30
Punto de partida: `v2.0.0` (fases 001–030), commit de release `76d8a8e`,
cierre de auditoría `b1bbccf`.

---

## 1. Qué decide el usuario

1. **La calidad de búsqueda lidera el programa**: 031 y 032 atacan el hueco más
   visible — hoy `transisto` no encuentra nada, porque FTS5 usa `unicode61` y
   no hay trigram ni tolerancia a errores.
2. **El trabajo no-motor entra, agendado al final**: distribución (037),
   rendimiento (038) y accesibilidad/interfaz (039).

## 2. Restricciones que no se mueven

Siguen siendo las del diseño aprobado para 022–030:

- Sin IA externa, sin APIs, sin cloud, sin telemetría, sin modelo descargado.
- Dependencias de runtime: exactamente `pypdf` y `watchdog`. Cualquier otra
  cosa la para `evaluation.gate`.
- El núcleo (dominio, consulta, ranking, índice, extracción, providers, DB) no
  importa Win32. Cada excepción se declara con su motivo.
- Una fase = un commit `feat:`, con pruebas, informe y documentación.
- Nada se publica sin medición previa: **evidence-first**. Si una mejora no
  supera su puerta, no entra, y el informe dice por qué.
- No se tocan ficheros del usuario. Ninguna reparación puede hacerlo, y el
  gate lo demuestra con una prueba de comportamiento.

## 3. El problema que abre 031, medido

`documents_fts` usa `tokenize = 'unicode61'`. Eso significa coincidencia por
token completo:

| Consulta | Resultado hoy | Lo que el usuario quiere |
|---|---|---|
| `transisto` | 0 resultados | el documento que dice «transistor» |
| `transsistor` (error) | 0 resultados | el mismo documento |
| `transistorr` (error) | 0 resultados | el mismo |
| `ebers mol` | 0 resultados | el documento de Ebers-Moll |
| `polarisacion` | 0 resultados (hay «polarización») | el documento |

Ninguno de estos casos es un synonym: es **la misma palabra escrita de otra
forma**. Es el fallo más común de cualquier buscador y aquí es total.

## 4. Por qué NO un segundo FTS5 trigram

La solución obvious —una segunda tabla `documents_fts_trigram` con
`tokenize='trigram'`— se descarta después de medir el coste:

- FTS5 trigram emite **un término por cada posición de carácter**. Un documento
  con el límite actual de 2 MB de texto produce hasta 2 000 000 de filas.
- Con 10 000 documentos, del orden de 10^10 filas. SQLite no sobrevive a eso y
  el índice deja de caber en un disco de usuario.
- Recortar el texto a 4 000 caracteres sigue dando 4 000 filas por documento:
  4 x 10^7 filas para 10 000 documentos, cientos de megabytes de derived data
  para buys tolerar dos erratas.

Truncar el contenido para poder buscarlo es un mal negocio: lo que no se indexa
es exactamente lo que el usuario no encuentra. La regla del proyecto ya lo
dice en `docs/ARCHITECTURE.md`: «la indexación **se acota**, la búsqueda
**se acota con límites que no truncan lo indexado**».

## 5. El diseño de 031: bloquear con huellas, verificar con la verdad

Dos pasos, y el segundo es el que decide.

### Paso 1 — Bloqueo barato (huellas de trigramas)

Una tabla derivada, versionada, reconstruible y eliminable:

```
document_fuzzy_terms(ngram, document_id, weight, version, preprocessing_version)
document_fuzzy_metadata(key, value)          -- count, version, dirty
```

Por documento se guardan **como mucho 64 trigramas distintos**, elegidos por
selectividad (los menos frecuentes en el corpus pesan más). El coste es
acotado por documento, no por su tamaño: un documento de 2 MB y uno de 40 B
ocupan lo mismo. 64 x 10 000 documentos = 640 000 filas: perfectamente viable.

La consulta de bloqueo cuenta, para cada documento candidato, cuántos trigramas
distintos de la consulta encuentra. Eso da un **coeficiente de solapamiento**
barato y sin falsos negativos: cualquier texto que contenga la cadena pedida
compartirá todos sus trigramas.

### Paso 2 — Verificación contra el texto real

El solapamiento **no decide nada**. Cada candidato se contrasta con el
contenido real leído de la base de datos, y solo sobrevive si cumple una de
estas dos condiciones, por token de la consulta:

- **Subcadena**: algún token de la consulta (≥ 3 caracteres) aparece tal cual
  dentro del nombre o del texto del documento. Esto cubre los prefijos:
  `transisto` esta contenido en `transistor`.
- **Distancia de edición acotada**: distancia Damerau-Levenshtein ≤ 1 para
  tokens de 4–7 caracteres, ≤ 2 para tokens de 8 o más, comparando el token de
  la consulta contra las **palabras** del documento. Esto cubre las erratas:
  `transsistor` a distancia 1 de `transistor`.

Un token solo cuenta como RESUELTO si cumple alguna de las dos. Una consulta de
varios tokens exige que **todos** estén resueltos, o el documento no entra.

**Consecuencia que hace esto seguro**: no puede haber falsos positivos
introducidos por el heurístico. Si un documento no contiene la cadena ni está
a distancia 1–2, no se devuelve, diga lo que diga el solapamiento. El índice
trigram es solo una forma barata de no leer 10 000 documentos.

### Acotamientos (los mismos de 025 y 026, sin excepciones)

| Acotamiento | Valor | Por qué |
|---|---|---|
| Trigramas por documento | 64 | El coste no crece con el tamaño del fichero |
| Candidatos verificados | 50 | Techo de trabajo por consulta |
| Caracteres leídos por candidato | 4 000 | La verificación lee texto, no el documento entero |
| Distancia de edición | ≤1 (4–7 car.), ≤2 (≥8) | Erratas de tecleo, no sinónimos |
| Longitud mínima de token | 3 | Por debajo no se puede distinguir |

## 6. Contrato de la capa difusa (igual que 026)

- **Solo cuando el motor léxico no devuelve nada.** Cualquier resultado no
  vacío del motor léxico se devuelve sin tocar, sin reordenar. Coincidencia
  exacta, frases, filtros y operadores siguen siendo autoritativos.
- **Los filtros mandan**: con `source:` o `type:` presentes la capa se
  desactiva, porque no puede reproducir su plan. Es la lección de 026, ya
  escrita en un test.
- **Opt-out explícito**: `search --no-fuzzy`, igual que `--no-semantic`.
- **Se explica**: cada resultado lleva `explain` con el token que casó, la
  regla (subcadena o distancia) y el solapamiento.
- **Se borra**: `privacy forget` elimina las filas difusas del documento, el
  inventario lo declara, y `diagnose recover orphan-derived` las limpia.

## 7. La puerta de evidencia de 031

Se mide sobre el corpus etiquetado, **antes** de decidir:

| Puerta | Umbral | Por qué ese umbral |
|---|---|---|
| T1 · Recall@5 de las consultas de typo/prefijo | ≥ 0,80 | Es lo que la fase promete |
| T2 · Ninguna consulta «debe recuperar nada» recibe resultados | 0 fugas | Es el contrato de no alucinar |
| T3 · MRR y P@1 léxicos no cambian | exactamente iguales | La capa no puede tocar lo que ya funciona |
| T4 · Coste de almacenamiento añadido | ≤ 15 % del índice | Si no, no vale la pena |
| T5 · Latencia p95 de una consulta sin resultado | ≤ +8 ms | Una búsqueda fallida no puede ser lenta |
| T6 · Sin dependencia nueva | solo `pypdf` y `watchdog` | Regla dura del proyecto |

Si T1 o T2 fallan, **la fase no entra**. El informe lo dirá con los números, y
se buscará otra vía (por ejemplo, expansión de prefijo en la capa léxica) antes
de renunciar.

## 8. Fases

| Fase | Entrega | Depende de |
|---|---|---|
| 031 | Búsqueda robusta: typos y palabras parciales | — |
| 032 | Sugerencias de consulta y «quizás querías» | 031 |
| 033 | Correo como fuente (`.eml`, `.mbox`) | — |
| 034 | Contenido dentro de archivos comprimidos | — |
| 035 | Operaciones por lotes en la GUI | — |
| 036 | Agrupación, orden y búsquedas guardadas | 035 |
| 037 | Distribución: modo portable y un ejecutable | — |
| 038 | Puerta de rendimiento reproducible | 031 |
| 039 | Accesibilidad e interfaz (teclado, lector, contraste, i18n) | — |
| 040 | Puerta de calidad v3: re-medir y publicar | 031–039 |

Cada fase entrega código, pruebas, un informe en `docs/development/`, las
actualizaciones de `README.md`, `docs/README.md`, `docs/ROADMAP.md`,
`docs/ARCHITECTURE.md`, `CHANGELOG.md` y un único commit `feat:`.

## 9. Riesgos asumidos

- **Falsos positivos por distancia de edición**: una palabra corta a distancia
  1 puede ser otra cosa. El riesgo se acota con el mínimo de 4 caracteres y con
  exigir que todos los tokens casen. La puerta T2 lo mide.
- **Coste de la verificación**: leer 4 000 caracteres por candidato es trabajo
  real. Con el techo de 50 candidatos, el peor caso está acotado y medido.
- **Un corpus pequeño no generaliza**: el corpus etiquetado tiene 27 documentos.
  La puerta T1 se cruzará además con un corpus sintético grande, para que la
  conclusión no dependa de 27 ejemplos.
- **Sin revisión independiente**: ya declarado en las notas de 2.0.0. Se
  mantiene durante este programa.
