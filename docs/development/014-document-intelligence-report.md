# Fase 014 — Local Document Intelligence

## Qué se entregó

1. **Pipeline de análisis local y determinista**
   (`src/universal_search/intelligence/`), puro: el mismo contenido
   produce siempre el mismo `DocumentAnalysis`, sin reloj, sin azar y sin
   tocar el sistema de ficheros.
   - `language.py` — detección por perfiles de palabras funcionales
     (es/en/fr/de/pt/it). La misma lista de *stopwords* sirve para el
     detector y para las palabras clave: una sola definición.
   - `structure.py` — título, encabezados y número de secciones a partir de
     líneas: markdown, numeración, MAYÚSCULAS y Title Case. Una frase que
     empieza por número **no** es un encabezado.
   - `keywords.py` — términos significativos con recuento, pares de
     co-ocurrencia y ventana deslizante acotada al vocabulario propio.
   - `analysis.py` — orquesta las tres etapas y acota el trabajo por
     documento.
2. **Almacenamiento derivado, versionado y reconstruible**: tabla
   `document_intelligence` (versión por fila, `content_hash` de origen,
   índice por idioma), `SCHEMA_VERSION` 3 → 4, y reconstrucción
   incremental (`rebuild`) que solo recalcula lo que cambió, invalida por
   cambio de versión y borra filas de documentos que ya no existen.
3. **Documentos relacionados** (`related`): coseno sobre los vectores de
   términos acotados, con los términos compartidos en la respuesta.
   Implementado en su propio módulo, **sin tocar la fórmula de ranking**.
4. **Superficie CLI**: `intelligence rebuild | show | related | clear`.
5. **40 tests nuevos** (`tests/test_intelligence.py`). Suite completa:
   **401 en verde**.

## Contrato con el resto de la aplicación

- **La búsqueda nunca lee estos datos.** La tabla es derivada y
  desechable: borrarla no cuesta nada salvo la función de relacionados.
  Hay un test que hace `clear()` y comprueba que la búsqueda sigue
  devolviendo lo mismo.
- **Cada fila lleva versión** (`INTELLIGENCE_VERSION`) y el `content_hash`
  del que se derivó, así que una reconstrucción recalcula exactamente lo
  que cambió y todo tras un salto de versión.
- **Similitud ≠ relevancia.** La similitud es un coseno entre dos vectores
  de 24 términos, en `store.py`, sin pesos de ranking, sin pool de FTS y
  sin lenguaje de consultas. Un test demuestra la independencia: con un
  `Ranker` de pesos todos a cero, la lista de relacionados no cambia.

## Detección de idioma: qué puede y qué no puede

| Entrada | Resultado | Por qué |
|---|---|---|
| Texto español largo | `es` | Marginada sobre las palabras funcionales |
| Texto inglés largo | `en` | Ídem |
| `"hola que tal"` (3 tokens) | `None` | Menos de 12 tokens: no hay evidencia |
| Texto sin palabras funcionales | `None` | Sin evidencia, se devuelve "desconocido" |
| Empate o victoria por 1 punto | `None` | Se exige `MIN_MARGIN = 2` |

La detección es una **etiqueta gruesa para descubrimiento local**, no un
identificador de idioma: un texto español que cite un manual inglés puede
devolver `en` si la muestra inglesa es mayor. Es un límite aceptado y
documentado; la alternativa honesta era no devolver nada nunca.

## Límites de tamaño (todo acotado, nada crece sin control)

| Artefacto | Límite | Motivo |
|---|---|---|
| Caracteres analizados por documento | 200 000 (75 % cabeza + 25 % cola) | Los encabezados están arriba y las conclusiones abajo; tokenizar 2 MB por documento no aporta |
| Términos por documento | 24 | Vector de similitud y palabras clave |
| Pares de co-ocurrencia | 16 | Solo entre términos del vector acotado |
| Encabezados | 32 | Deduplicados, en orden de documento |
| Caracteres de un título/encabezado | 120 | Un encabezado es una etiqueta, no una frase |

## Reconstrucción: comprobaciones del comportamiento real

| Operación | Resultado medido |
|---|---|
| `rebuild` sobre 5 documentos | `5 updated, 0 unchanged` |
| `rebuild` repetido | `0 updated, 5 unchanged` (idempotente) |
| `rebuild` tras cambiar 1 documento | `1 updated, 4 unchanged` |
| `rebuild` tras subir la versión | `5 updated` (invalida todo) |
| `rebuild` tras borrar un documento del índice | `1 removed` |
| `rebuild --limit 2` | `2 scanned, 2 updated` |
| `rebuild --force` | Recalcula todo y vuelve al mismo valor |

## Datos relacionados

`related` sobre el corpus de prueba:

- `bjt.md` → `bjt2.md` con **0.878** y términos compartidos
  `activa, base, bjt, colector, corriente`; `cmos.md` y `paella.md` no
  aparecen.
- `paella.md` → sin relacionados (mensaje claro, código 1, sin
  traceback).
- Sin inteligencia reconstruida → lista vacía, nunca una excepción.
- Determinista: dos llamadas seguidas devuelven exactamente la misma
  lista; empate por puntuación resuelto por ruta ascendente.

## Robustez (entradas que no deben romper nada)

- `None` (binario no extraíble), cadena vacía, solo espacios/tabuladores:
  registro válido y vacío, sin excepción.
- Contaminado con `\x00` (extracción fallida): los NUL se convierten en
  espacio y el resto se analiza con normalidad.
- Solo puntuación o símbolos: sin términos, sin problema.
- Documento de 600 000 caracteres: se muestrea a 200 000
  (`truncated=True`, `analyzed_chars=200000`) y el resultado es idéntico
  entre dos ejecuciones.
- Unicode con acentos, ñ y caracteres CJK: se tokeniza como palabras
  Unicode, igual que hace FTS5.

## Privacidad: qué **no** se infiere

El registro tiene exactamente nueve campos —versión, idioma, título,
encabezados, secciones, términos, pares, caracteres analizados y
truncamiento—, y un test fija esa lista para que nadie añada un campo
derivado de la persona sin darse cuenta. No se infiere género, edad,
afiliación ni nada que no sea una propiedad del texto. El pipeline no lee
nada fuera del contenido indexado: ni metadatos del fichero, ni
historial, ni consultas.

## Criterios de aceptación (spec 014)

| Requisito | Estado |
|---|---|
| La búsqueda funciona sin inteligencia | ✅ test: búsqueda antes de reconstruir y después de `clear` |
| Inteligencia reconstruible | ✅ incremental, con `--force` y por versión |
| Resultados deterministas | ✅ análisis y reconstrucción repetidos idénticos |
| Funcionalidad de relacionados probada | ✅ similitud, aislamiento, determinismo, límites, CLI |
| Datos invalidables/reconstruibles | ✅ cambio de contenido, cambio de versión, borrado, `clear` |
| Sin servicio externo | ✅ solo biblioteca estándar; no hay modelo, red ni telemetría |
| Modularidad de la spec | ✅ lenguaje / estructura / palabras clave / análisis / almacén |
| No inferir atributos personales | ✅ contrato de campos por test |

## Limitaciones (deliberadas)

- **Sin GUI.** La superficie es CLI + API. Añadirlo a la ventana (panel
  "relacionados") es trabajo de la fase 017 (UX), no de esta.
- **La similitud es léxica, no semántica.** Dos documentos que hablan de
  lo mismo con vocabulario distinto no se parecen para esta función. Es
  la alternativa honesta a los embeddings sin justificar: determinista,
  local, explicable (la respuesta dice *qué términos* comparten) y sin
  coste de modelo. La fase 013 ya synergiza con el ranking, y los
  embeddings locales siguen sin justificación medida.
- **Sin stopwords por idioma completos.** El perfil es una lista corta y
  revisable de palabras funcionales, no un diccionario Freeze: es
  suficiente para filtrar ruido y demasiado pequeño para mentir sobre lo
  que es.
- **Los documentos binarios no tienen análisis** (sin texto no hay nada
  que derivar); siguen siendo localizables por nombre y ruta, que es lo
  que el índice ya garantiza.
- **La reconstrucción no está en el indexador.** Es un comando explícito:
  el indexador está medido en 4.5 ms/documento (fase 011) y no se le
  añade un coste que la mayoría de los usuarios no pidió. Un
  `rebuild` de 5 documentos tarda menos de un segundo.

## Cómo ejecutar

```bash
universal-search intelligence rebuild            # derivar (incremental)
universal-search intelligence rebuild --force    # recalcular todo
universal-search intelligence show informe.pdf   # ver el análisis
universal-search intelligence related informe.pdf --limit 5
universal-search intelligence clear              # borrar lo derivado

.venv\Scripts\python -m pytest tests\test_intelligence.py
```
