# Fase 032 — Sugerencias de consulta

## La regla que define la fase

**Una sugerencia es una consulta que se ha ejecutado de verdad y ha devuelto
un documento.** Nada se ofrece porque «parezca una palabra».

No hay diccionario aquí, ni corrector ortográfico, ni red, ni lista de «erratas
típicas». Las únicas palabras que este módulo puede proponer son las que
existen **en el índice del propio usuario**. De ahí se sigue lo importante: una
sugerencia no puede inventar un término, y no puede proponer una corrección
para una consulta que ya era correcta. Si nada verifica, no se ofrece nada.

## Cómo funciona

1. **Lee el vocabulario del índice** con `fts5vocab`, que es una *vista* sobre
   el índice FTS existente: crearla no cuesta nada y guardarla sí, así que se
   crea, se lee y se retira en la misma llamada.
2. **Corrige token a token** contra ese vocabulario, con la misma distancia
   Damerau-Levenshtein acotada de la fase 031 (≤1 para 4–7 caracteres, ≤2 a
   partir de 8) y un presupuesto compartido.
3. **Verifica**: ejecuta la consulta corregida con el mismo motor que el
   usuario está usando y descarta la sugerencia si no devuelve nada.

Ordena por distancia de edición, y a igual distancia por frecuencia en el
índice: una corrección a una palabra que el usuario ha escrito 200 veces gana a
una que ha escrito una.

### Una regla que la puerta añadió

La primera ejecución de la puerta salió con **T3 en rojo**: ofrecía consejo
para `zzz no existe`, porque `existe` tenía una palabra vecina. La puerta tenía
razón, y la regla que falta es:

> Una errata es la mal escritura de algo que el índice contiene. Un token que
> ni está indexado ni tiene corrección **no es una errata de nada**: es otro
> tipo de entrada, y aconsejar sobre ello es ruido.

Con esa regla, `zzz no existe` no recibe consejo, y `zzzz transsitor` tampoco,
porque la consulta corregida seguiría sin devolver nada.

## Puerta de evidencia

`python -m evaluation.suggest_gate` sobre el corpus etiquetado.

| Puerta | Umbral | Medido | Veredicto |
|---|---|---|---|
| T1 recall de correcciones | ≥ 0,60 | **1,00** (3/3) | PASS |
| T2 sugerencias sin verificar | 0 | **0** | PASS |
| T3 consejo donde no hay errata | 0 | **0** | PASS |
| T4 cambio de MRR léxica | 0 | **0** (sigue en 0,8333) | PASS |
| T5 p95 añadida, sesión viva | ≤ 8 ms | **+7,19 ms** | PASS |
| T6 estado persistente | 0 | **0** | PASS |

**VEREDICTO: SHIP (6/6)**. Registro en `evaluation/suggest_baseline.json`.

## Lo que la puerta encontró en T5, y dos errores míos

T5 dio **9,3 ms** y luego **22,1 ms** tras «optimizar» a ciegas. El perfil dijo
que el coste eran conexiones SQLite (~2 ms cada una en esta máquina), no el
algoritmo. Tres cosas:

1. **El caché del vocabulario nunca entraba.** La clave era el tamaño y la
   mtime del fichero de base de datos, y el WAL reescribe el fichero principal
   durante un checkpoint normal: cada pulsación parecía un índice nuevo. La
   clave ahora es **(número de documentos, última `indexed_at`)**, que cambia
   exactamente cuando el vocabulario puede cambiar, y cuesta una consulta
   indexada.
2. **El suggester se creaba nuevo en cada llamada**, así que su caché moría
   antes de servir. Ahora hay uno por motor, en una `WeakKeyDictionary`: la
   sesión de búsqueda (la GUI) lo reutiliza y una invocación suelta de la línea
   de órdenes paga el coste en frío una vez.
3. **La puerta medía mal**, por el mismo motivo: creaba un suggester por
   muestra y reportaba el coste en frío como si fuera el de la sesión viva.
   Ahora mide **los dos** y los publica. El número en frío (23,3 ms) se
   imprime junto al de la sesión viva (7,19 ms) para que nadie tenga que
   suponer cuál es cuál.

Un caché que nunca entra es peor que no tener caché, porque además esconde ese
hecho. Está escrito en el código y en este informe.

## Pruebas

`tests/test_suggestions.py`, **17 tests**:

- el vocabulario sale del índice y **no deja nada detrás** (la vista se retira)
- se ordena por frecuencia de uso
- una errata se corrige a una palabra indexada; una palabra ya indexada no se
  corrige; un token corto no se corrige
- a igual distancia gana la palabra más común
- una corrección se mantiene dentro del presupuesto de ediciones
- **la sugerencia devuelve un documento** (el contrato, ejecutado en el test)
- **el nonsense no recibe consejo inventado**
- una consulta correcta no recibe consejo
- un índice sin palabras parecidas no ofrece nada
- respeta el límite y el orden
- un token sin corrección verificada se deja como estaba
- funciona con vocabulario inyectado, y sobrevive a un build sin
  `fts5vocab` (sin sugerencias, nunca sin búsqueda)

## Integración

- CLI: cuando una búsqueda no devuelve nada, imprime
  `¿Querías decir? <consulta> (de X a Y, N resultado(s))`.
- `--no-suggest` lo desactiva, simétrico a `--no-fuzzy` y `--no-semantic`.
- La GUI lo conectará en la fase 035, cuando tenga operaciones por lotes con las que
  combinar.

## Limitaciones

- Una errata a **tres** ediciones sigue fuera de alcance, como en 031, y una
  palabra que solo aparece en la ruta tampoco.
- La corrección es **por token**: una errata que abarque dos palabras seguidas
  («modlebers» por «modelo ebers») no se detecta.
- El vocabulario se limita a 50 000 términos; por encima, el coste de un
  barrido completo sería inappropriate para una pulsación.
- El coste en frío (23,3 ms en esta máquina) es el que paga una invocación
  suelta de la línea de órdenes. No es interactivo, así que no se ha acotado
  más; la puerta lo publica en lugar de esconderlo.
