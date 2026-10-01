# Fase 038 — Puerta de rendimiento reproducible

## La regla que lo gobierna

**Una cifra de latencia sólo puede afirmar lo que el equipo que la midió le
permite afirmar.** Todo lo demás es teatro con una tabla.

La auditoría de 2.0.0 terminó sin poder responder a la pregunta más simple:
*¿ha regresado la latencia?* El mismo código midió **4,86 s** y **7,83 s** en
la misma máquina, con minutos de diferencia y un cliente de juego cargando la
CPU al 94 %. La conclusión honesta fue «no se puede afirmar ni negar», y el
número se publicó igualmente con una nota al pie.

Dos mediciones de este proyecto demuestran que esa nota no basta:

| Medición | CPU al 94 % | Equipo descargado | Mismo código |
|---|---|---|---|
| Puerta 031, T5 (p95 añadido) | **18,70 ms** → FAIL | **7,50 ms** → PASS | sí |

Mismo código, mismos umbrales, veredicto opuesto. Y la puerta de la 037 filtró
su propia simulación y hizo fallar tres tests de otros módulos. Una puerta que
no es de fiar con la máquina tranquila no es de fiar en ningún caso.

## La puerta

`python -m evaluation.perf_gate` · `evaluation/perf_baseline.json`

Códigos de salida: **0 PASS · 1 FAIL · 2 INCONCLUYENTE**. El tercero existe
porque son tres situaciones y dos respuestas no pueden representarlas; la
auditoría publicó una nota al pie justamente porque el estado intermedio no
tenía código.

### Cinco reglas, y por qué cada una

1. **La carga se mide antes que la cifra, y puede vetarla.** Una carga de
   calibración de aritmética pura —sinh xorshift, sin E/S ni reservas— se cronometra
   primero. Si va más lenta que el mejor valor registrado de esta misma máquina,
   la máquina no está en reposo y el veredicto es INCONCLUYENTE.
2. **Cada tiempo es el mejor de N, nunca la media.** La interferencia sólo
   puede *añadir* tiempo, así que el mínimo es la estimación más limpia del
   coste real. Una media informa del trabajo del vecino como si fuera nuestro.
3. **La medición tiene que repetirse antes de poder concluir.** La suite se
   ejecuta dos veces y **la dispersión entre las dos es una puerta en sí
   misma**. Si el mismo build medido dos veces discrepa más que la tolerancia,
   la tolerancia no resuelve nada y declarar PASS sería unautoengaño.
4. **Una máquina ocupada no puede reescribir la línea base.** Refrescar las
   cifras de referencia con el juego de otro corriendo convierte cada
   comparación posterior en una comparación contra ruido.
5. **La máquina se nombra.** La línea base pertenece a una CPU, un Python y un
   sistema. En otra máquina las mismas cifras son indicativas, no comparables,
   y la puerta lo dice en vez de insinuar un veredicto que no puede sostener.

### Dos tolerancias, porque un porcentaje solo no significa nada

Una regresión del 25 % sobre una operación de 2 ms es medio milisegundo y no
justifica romper una build; una del 25 % sobre 6 s de indexado es real. El
cambio permitido es `max(porcentaje, suelo)`: el suelo es el cambio más pequeño
que merece mención, y el porcentaje es lo que escala.

## La puerta encontró tres fallos en sí misma

**No aplicaba la regla que declaraba.** El docstring decía «dos señales
independientes, y la más débil veta» y el código sólo consultaba la
calibración. La primera ejecución informó «0,98× de la referencia» —
concluyente— con la máquina al **88 %** de CPU. Una regla enunciada que el
código no aplica es peor que ninguna regla; ahora ambas vetan.

**Avisaba de «otra máquina» con dos diccionarios idénticos.** `_same_machine`
buscaba `processor` en el nivel superior del fichero en vez de dentro de
`machine`. Una advertencia que siempre es cierta enseña a ignorar las
advertencias.

**Una métrica no medía lo que decía medir.** `db_open_mean_ms` daba 16,9 ms en
la línea base y 9,6 ms en la pasada siguiente: **23 % de dispersión para un
código que no había cambiado**. El primer número de un proceso nuevo carga el
módulo de base de datos, el de SQLite y crea los ficheros WAL. Lo detecta la
comprobación de dispersión —que es justo para lo que existe—, y la métrica
mide ahora la apertura en régimen tras un calentamiento, que es lo que paga el
trabajador en cada arranque.

## Pruebas

`tests/test_perf_gate.py`, **27 tests**. La carga veta por calibración y por
figura del SO, y una figura desconocida **no** veta (negarse a medir donde no
hay número sería tan deshonesto como ignorar uno que sí hay); la ausencia de
referencia previa no es un veto; la carga de calibración es determinista,
no eludible y cara; dos pasadas idénticas repiten, dos diferentes dan
INCONCLUYENTE y la peor métrica se nombra; una mejora nunca falla; el suelo
absoluto protege a las operaciones rápidas y el porcentaje protege a las
grandes; una métrica «más es mejor» se compara al revés; el tamaño del índice
queda fuera de la comprobación de dispersión (dos pasadas producen los mismos
bytes o una está rota); todas las métricas tienen tolerancia y no hay claves
repetidas; y la línea base commiteada declara la máquina de la que salió.

## Lo que quedó sin medir, y por qué

**El criterio de aceptación «dos ejecuciones con el equipo tranquilo dan números
comparables» no se ha podido comprobar en esta máquina.** Durante toda la fase
un cliente de juego ajeno a este repositorio mantuvo la CPU entre el 63 % y el
94 % — el mismo obstáculo, y la misma causa, que en la auditoría de 2.0.0. No
se ha tocado ese proceso.

Lo que sí está medido, con números reales:

| Puerta | Estado | Evidencia |
|---|---|---|
| Un equipo ocupado no mide nada | **medida** | ocho ejecuciones, salida **2**, cero código de medición ejecutado, línea base intacta |
| La línea base registra la máquina | **medida** | `evaluation/perf_baseline.json`, 9 métricas |
| La dispersión detecta ruido real | **medida** | `db_open_mean_ms` varió **23,1 %** entre dos pasadas, y por eso esa métrica se corrigió |
| Veredicto PASS con el benchmark real | **sin medir** | requiere la máquina en reposo |
| Veredicto FAIL | **medida** | `evaluation/perf_gate.py` sobre números sintéticos, y el estado intermedio sin línea base previa |

La fase **queda abierta en el roadmap** por esto. Es la misma regla del
proyecto que la hizo aplicable a sí misma: nada entra sin su puerta de evidencia
medida, y aquí la puerta medida fue «no se puede afirmar». La fase 040 debe
ejecutarla con el equipo descargado antes de publicarla.

## Una referencia provisional, dicha en voz alta

La línea base commiteada contiene `db_open_mean_ms: 16,9 ms`, **medida antes
de corregir el método**. Su referencia arrastra unos 7 ms de arranque en frío
que el código actual ya no mide, así que compararla es benévolo: sólo puede
ocultar una regresión en esa métrica, nunca inventar una falsa.

La alternativa —borrarla— habría dejado la puerta sin referencia y la primera
ejecución conclusionista habría creado otra con el mismo problema. Así que el
fichero declara `"provisional": ["db_open_mean_ms"]`, la puerta lo imprime junto
a la métrica, y un test lo comprueba. Una cifra que se sabe incorrecta y no lo
dice es peor que una que se sabe incorrecta y lo dice.

## Pruebas

`tests/test_perf_gate.py`, **34 tests**. La carga veta por calibración y por
figura del SO, y una figura desconocida **no** veta (negarse a medir donde no
hay número sería tan deshonesto como ignorar uno que sí hay); la ausencia de
referencia previa no es un veto; la carga de calibración es determinista, no
eludible y cara; dos pasadas idénticas repiten, dos diferentes dan
INCONCLUYENTE y la peor métrica se nombra; una mejora nunca falla; el suelo
absoluto protege a las operaciones rápidas y el porcentaje protege a las
grandes; una métrica «más es mejor» se compara al revés; el tamaño del índice
queda fuera de la comprobación de dispersión (dos pasadas producen los mismos
bytes o una está rota); todas las métricas tienen tolerancia y no hay claves
repetidas; y la línea base commiteada declara la máquina de la que salió.

Siete de esos tests cubren la **conexión**: que `main()` devuelva 2 sin ejecutar
la medición y sin escribir la línea base cuando la máquina está ocupada, que
la registre cuando no hay referencia, que dos pasadas idénticas den 0 tras
medir dos veces, que una regresión dé 1, que dos pasadas inconsistentes den 2 y
**no** 0 aunque cada métrica esté dentro de su tolerancia, y que el informe
INCONCLUYENTE diga que no se ha escrito ninguna cifra.

**Deliberadamente no hay un test que ejecute el benchmark real.** Un test de
rendimiento que falla o pasa según la máquina es exactamente el instrumento que
esta fase elimina.

## Limitaciones

- **La línea base es de una máquina.** En otra CPU el veredicto es
  indicativo y la puerta lo advierte; no se puede hacer mejor sin un banco de
  pruebas cerrado, que el proyecto no tiene.
- **El veto del SO (60 %) puede ACTIVarse por culpa propia** si alguien lanza
  la puerta mientras corre otra medición suya. Se mide antes de empezar y se
  queda con la lectura más baja de tres, precisamente para no penalizarse a sí
  misma.
- **`db_open_mean_ms` mide la apertura en régimen**, no la primera de un proceso
  nuevo. El coste de arranque en frío existe y es real; se mide en el
  benchmark de la 011, que no pretende ser una puerta.
- **La dispersión se mide entre dos pasadas de la misma sesión**, así que
  detecta ruido de corto plazo pero no deriva térmica de horas ni una
  actualización de Windows entre mañana y tarde.
- **El umbral del 60 % del SO es un juicio, no una medición.** Se eligió
  porque vetoa sólo condiciones en las que una latencia no significa nada; uno
  más ajustado produciría informes INCONCLUYENTES en un portátil sobre un
  escritorio, y un instrumento que se ignora no es un instrumento.
- **La puerta no está en el CI bloqueante.** El runner de GitHub es una máquina
  compartida y virtualizada: casi siempre daría INCONCLUYENTE. Está documentada
  como instrumento de medición local y es la fase 040 la que decide si algún
  día puede ser puerta bloqueante.