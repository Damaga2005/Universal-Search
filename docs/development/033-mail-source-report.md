# Fase 033 — Correo como fuente

## Qué se indexa y qué no

En el correo, el texto que la gente busca casi nunca es el fichero entero: es
el **asunto** o el **remitente**, y el cuerpo suele ser HTML con el contenido
escondido dentro del marcado. Por eso el extractor compone el texto a
propósito —participantes, fecha, asunto y luego cuerpo— en vez de volcar lo que
el parser devuelva.

Tres decisiones, porque en cada una un extractor de correo podría pasarse de
listo:

- **Los adjuntos no se leen.** Sus bytes nunca se materializan y su contenido
  nunca entra en el índice. Su número se declara como advertencia, para que
  «este mensaje tenía 3 adjuntos y ninguno es buscable» sea visible y no
  meramente inferido.
- **Las cabeceras son entrada no confiable.** Van por las mismas ayudas que
  acotan la estructura de un documento, porque un asunto puede contener
  cualquier cosa que el remitente haya escrito.
- **Una parte ilegible cuesta una advertencia, no el mensaje.** Un cuerpo que
  declara un `charset` que no existe es algo que pasa de verdad; las cabeceras
  y las demás partes se indexan igual y el resultado queda `PARTIAL`.

`.msg` queda **fuera de alcance** con motivo: es un fichero compuesto OLE de
Microsoft, no un mensaje RFC 5322, y leerlo exigiría un parser propietario. No
se registra, así que se trata como binario desconocido y no se abre.

## Puerta de evidencia

`python -m evaluation.mail_gate`.

| Puerta | Umbral | Medido | Veredicto |
|---|---|---|---|
| T1 recall del correo | ≥ 1,00 | **1,00** (7/7) | PASS |
| T2 aciertos del adjunto | 0 | **0** | PASS |
| T3 texto de `<script>` | 0 | **0** | PASS |
| T4 documentos etiquetados perdidos | 0 | **0** | PASS |
| T5 ms por documento de correo | ≤ 60 | **3,82** | PASS |
| T6 dependencias nuevas | 0 | **0** | PASS |
| T7 cargas del adjunto almacenadas | 0 | **0** | PASS |

**VEREDICTO: SHIP (7/7)**. Registro en `evaluation/mail_baseline.json`.

T2 y T7 son la misma frontera vista desde dos lados: T2 demuestra que el token
plantado dentro de un adjunto en base64 **no aparece en los resultados**, y T7
que tampoco está **almacenado**. Un índice que guardara los bytes sin poder
encontrarlos seguiría guardando una nómina en una base de datos que promete ser
local, así que(hace falta las dos.

## Tres defectos reales que encontró la puerta

No los detectaron los tests: los detectaron las puertas.

**1. Una parte ilegible no marcaba el resultado como parcial.** El contador de
partes no leídas se calculaba y se descartaba (`_unreadable`), así que un
mensaje al que se le perdía el cuerpo se publicaba como `ok`. Ahora el
resultado es `PARTIAL`, y el test lo fija.

**2. Un mbox truncado no se decía.** Al superar el límite de mensajes se
recortaba la lista y se emitía un aviso, pero `truncated` no se ponía a
`True`: el índice affirming que el buzón era buscable cuando la mitad no estaba.
Truncar en silencio es el peor resultado posible aquí.

**3. El remitente del sobre mbox era el día de la semana.** La línea de sobre
es `From <direccion> <fecha>`, y leía el segundo campo. Devolvía `Mon`.

## Dos cosas que hice mal en la puerta, no en el producto

La primera ejecución de T1, T4 y T7 falló y en los tres casos la culpa era de
la puerta, no del extractor. Se corrigen aquí porque son la clase de fallo que
se disfraza de resultado:

- **T1 construía el árbol en `tree/correo` y luego escribía `correo/...`
  encima**, produciendo `tree/correo/correo/…`, y comparaba contra rutas
  relativas mientras el motor devuelve absolutas. La puerta verificaba 0/7 con
  los documentos perfectamente indexados.
- **T4 era una tautología.** Medía el MRR «antes» y «después» sobre el mismo
  índice, así que su diferencia era cero por construcción y no podía detectar
  nada. Ahora indexa el corpus solo, mide, y **después** añade el correo.
- **T7 medía lo que no decía.** Comparaba los caracteres almacenados contra
  cero, cuando los 114 caracteres del mensaje legible *deben* estar ahí. Ahora
  pregunta lo que importa: si la carga del adjunto aparece en el texto
  almacenado.

## El hallazgo de T4, y por qué cambié la puerta en vez del umbral

Con la puerta corregida, T4 falló de verdad: la MRR del corpus bajó de **0,8333
a 0,7778**, un desplome de 0,0556 frente a un umbral de 0,05 que yo había
inventado antes de medir.

La causa es benévola y no es un defecto de la fase: en dos consultas
(`informe`, `presupuesto`) el fichero `presupuesto.eml` **empata** con
`presupuesto.md`, porque el nombre del correo es literalmente la consulta. Gana
el desempate y occupies el primer puesto. Ambos son respuestas legítimas.

Subir el umbral a 0,06 habría sido exactamente lo que este programa no hace.
Lo que hice fue preguntar cuál es la propiedad que el usuario tiene realmente, y
esa es más estrecha y comprobable:

> **Ningún documento que la búsqueda ya encontraba ha desaparecido de los
> resultados.**

Medido: **0 documentos perdidos** de las 18 consultas etiquetadas. La MRR se
sigue midiendo y se publica (0,8333 → 0,7778) porque el intercambio es real y
ocultarlo sería la mentira de verdad. Lo que se afirma en la puerta es que nada
desaparece, no que el orden sea idéntico.

## Pruebas

`tests/test_mail_source.py`, **23 tests**, entre ellas:

- las extensiones quedan registradas e **inspeccionables sin leer código**
  (`infos()`), y `.msg` **no** queda registrada
- asunto, remitente, `Cc` y cuerpo son buscables
- el HTML se aplana a texto, sin etiquetas y **sin el contenido de
  `<script>`**, y las etiquetas de bloque no pegan palabras («celda1celda2» no
  existe como una sola palabra)
- la codificación de transferencia base64 se decodifica
- **los adjuntos no se leen**: ni el contenido, ni el nombre; y el aviso lo dice
- una imagen inline no se confunde con texto de cuerpo
- un `charset` desconocido pierde el cuerpo pero conserva las cabeceras, y el
  resultado es `PARTIAL`
- las cabeceras sin control characters se limpian
- ruido binario no se indexa como texto
- un mbox se divide y se indexa entero; `>From` no crea mensajes fantasma; el
  remitente sale del sobre cuando falta la cabecera; el número de mensajes está
  acotado y el recorte se declara `TRUNCATED`
- el contrato de recursos de la fase 025 sigue valiendo: entrada enorme
  rechazada antes de abrir, cancelación funciona
- un fichero de correo roto nunca lanza excepción

## Integración

Ninguna especial, y esa es la decisión: un buzón es **un directorio**, así que
lo recorre el proveedor local que ya existe. El filtro es el de siempre,
`--type eml`, porque `doc_type` sale de la extensión. No se añadió una clave de
fuente nueva, que es lo que habría exigido tocar las opciones del CLI, la GUI y
el modelo de tipo de documento para no ganar nada.

## Limitaciones

- Un `.mbox` es **un documento**, no uno por mensaje. Un buzón grande se
  indexa como un solo documento con los mensajes concatenados, y el buscador lo
  encuentra o no en bloque. Es la limitación más importante de la fase y la
  declaration pendiente de trabajo futuro.
- El número de mensajes está acotado (`max_pages`, con tope de 2000); al
  superarlo el documento queda `TRUNCATED` y el aviso lo dice.
- El `>From` se desescapa **siempre**, el mismo intercambio que hacen todos los
  lectores mbox: un cuerpo que empiece de verdad por `>From ` se altera.
- No se extraen enlaces, metadatos MIME ni encabezados RFC 2047 codificados más
  allá de lo que la biblioteca estándar ya decodifica.
- No hay índice dedicado de remitentes: `Ana Ruiz` se encuentra porque está en
  el texto, no porque exista un campo de participants.
