# Fase 035 — Operaciones por lotes

## La regla que sostiene el módulo

**Un lote nunca afirma más de lo que hizo.** Es la clase de fallo que ninguna
prueba unitaria ve: la barra de estado dice «listo» después de abrir tres
ficheros de doscientos.

Tres formas de mentir, las tres tratadas explícitamente en vez de con una
excepción:

- **El lote está acotado.** Abrir cinco mil ficheros de golpe es un ataque de
  denegación de servicio contra la propia máquina del usuario, así que hay un
  tope duro. Lo que queda fuera **se cuenta y se dice**, nunca se descarta en
  silencio.
- **Un fallo no detiene el lote.** Una ruta que ya no existe no puede costar
  las otras cuarenta y nueve operaciones, ni quedar oculta: el informe lista lo
  que falló y por qué.
- **Nada destructivo sin confirmación.** Olvidar un documento borra su
  contenido indexado, así que exige `confirm=True` explícito; por defecto no
  hace absolutamente nada.

## El hallazgo de la puerta, y era un defecto real

La primera ejecución de la puerta pasó 8 de 9. La que falló fue T8, y era un
error de la puerta: buscaba la **palabra** `clipboard` en el fichero y la
encontró en un docstring que dice que el módulo está *libre* de ella. T8 ahora
mira **imports**, no palabras.

Pero el detalle de esa ejecución tenía un problema real detrás, y es lo que
importa:

```
[T6] Olvidando: 0 de 4  ·  4 sin procesar por el limite de 50
```

Cuatro documentos, muy por debajo de 50. El informe culpaba al **límite de tamaño** de
algo que en realidad no se había hecho porque **no se confirmó**. Eso es
literalmente la dishonestidad que esta fase existe para evitar, escrita en el
propio módulo que la evita.

De ahí el `skip_reason` en `BatchReport`: «sin procesar» sin motivo empieza a
mentir. Ahora un lote que se detiene porque el usuario canceló, o porque no se
confirmó nada, no imprime las mismas palabras que uno que se detiene en el tope,
y T9 lo comprueba.

## Puerta de evidencia

`python -m evaluation.batch_gate`.

| Puerta | Umbral | Veredicto | Lo que midió |
|---|---|---|---|
| T1 lote completo lo dice completo | sí | PASS | `Abriendo: 5 de 5` |
| T2 lote parcial es distinguible | sí | PASS | `15 de 20 · 5 con error (…)` |
| T3 el lote está acotado | sí | PASS | 50 abiertos, 30 saltados |
| T4 un fallo queda aislado | sí | PASS | 9 de 10, un `FileNotFoundError` |
| T5 las cuentas cuadran exactamente | sí | PASS | `succeeded + failures == attempted` |
| T6 olvidar exige confirmación | sí | PASS | sin confirmar quedan 4 indexados |
| T7 olvidar sí borra del índice | sí | PASS | quedan 2, ficheros intactos |
| T8 el núcleo no depende de la vista | sí | PASS | solo `dataclasses`, `pathlib`, `universal_search` |
| T9 ningún «listo» ni motivo falso | sí | PASS | `4 sin procesar por que no se confirmo` |

**VEREDICTO: SHIP (9/9)**. Registro en `evaluation/batch_baseline.json`.

Los informes, tal y como los vería el usuario:

```
Abriendo: 5 de 5
Abriendo: 15 de 20  ·  5 con error (doc0.md: FileNotFoundError; …; y 2 mas)
Abriendo: 50 de 80  ·  30 sin procesar por el limite de 50
Olvidando: 0 de 4  ·  4 sin procesar por que no se confirmo
Olvidando: 2 de 2
```

## Pruebas

`tests/test_gui_batch.py`, **22 tests**, con una plataforma falsa que registra
las llamadas y puede fallar por ruta. Cubre: lote completo, selección vacía, el
tope y que se declare lo saltado, el tope configurable, el tope cero (no hace
nada en vez de todo), aislamiento de fallos, que un lote parcial no se lea como
completo, que un fallo total se diga como fallo, que muchos fallos se abrevien
pero **nunca se cuente mal**, que las cuentas cuadren, el texto del
portapapeles con y sin duplicados, que olvidar sin confirmación no haga nada, que
olvidar con confirmación sí borre del índice **sin tocar el fichero**, que sin
base de datos falle ruidosamente, que olvidar también esté acotado, y que el
informe sea inmutable.

## Integración

- La lista de resultados pasa a `selectmode="extended"`: Ctrl+clic añade,
  Mayús+clic extiende.
- Un menú **Selección** con: abrir (Ctrl+O), mostrar (Ctrl+R), copiar rutas
  (Ctrl+C) y olvidar del índice (Ctrl+Mayús+R).
- Retorno, Ctrl+Retorno y doble clic pasan a operar sobre la selección
  completa. **Una sola ruta de código** para uno y varios: una rama aparte
  «abre el primero» es donde los dos comportamientos se separan y donde un clic
  acaba significando algo distinto de una selección única.
- La señal de uso y la consulta reciente solo se registran cuando la
  selección es **un** documento y la apertura funcionó: un lote no entrena al
  ranking.
- Olvidar pide confirmación explícita y dice que **los ficheros del disco no se
  tocan**.

## Limitaciones

- **Tope de 50 por lote.** No hay opción de cambiarlo en la GUI (solo en el
  núcleo): un tope visible en el menú sería una invitación a desactivarlo.
- No hay «seleccionar todo» ni selección por rango con el teclado: el Listbox
  de Tk no lo da, y añadirlo exigiría un modelo de selección propio.
- Abrir 50 documentos deja 50 ventanas abiertas. Es lo que pidió el usuario, y
  el informe dice cuántos, pero no hay ni reversing ni aviso.
- No hay deshacer para «olvidar». Es coherente con el resto del programa —la
  reconstrucción del índice existe—, pero no es instantáneo.
