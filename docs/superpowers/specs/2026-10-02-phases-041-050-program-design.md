# Diseño del programa 041–050 — Universal Search 3.x

Estado: aprobado para implementar
Fecha: 2026-10-02
Plan: `docs/superpowers/plans/2026-10-02-phases-041-050-program.md`
Punto de partida: fases 001–040 completadas y publicadas, commit `375e9cb`

---

## 1. Qué decide este programa

1. **Un buscador puede ser correcto y seguir siendo difícil de usar.** Las
   cuarenta fases anteriores construyeron un motor medible: tolerante a
   erratas, con sugerencias verificadas, un grafo de documentos relacionados,
   una capa semántica opcional, batching, modo portable y un gate de 23
   invariantes. Casi nada de eso se nota al abrir la aplicación. El recorrido
   `lanzar → buscar → seleccionar → abrir` sigue siendo la misma ventana de
   2023 con mejores resultados dentro.

2. **El programa empieza por la experiencia y termina por la evidencia.** 041 a
   044 son de usuario; 045 a 047 son de motor y de datos; 048 y 049 son de
   entrega; 050 es la puerta. Ese orden es deliberado: **no se optimiza lo que
   todavía no se ha medido en uso**, y la calidad de búsqueda de la 045 solo
   puede atacar fallos que la 041–044 hayan dejado al descubierto.

3. **Lo que no se declara soportado, no se anuncia.** Es la fase 048, y es la
   que más incomoda es, porque el proyecto arrastra desde la auditoría de
   2.0.0 una discrepancia sin resolver: **CI ejecuta Python 3.12 y el
   desarrollo local usa 3.14**. Está escrita como limitación desde hace cuatro
   fases. Una limitación escrita es mejor que una mentira, pero una limitación
   que además se puede cerrar se cierra.

## 2. La restricción que lo gobierna

**Extender, no duplicar.** Cada prompt de 041–050 lleva su propia línea
«esto no es una segunda implementación de X», y el plan las convierte en regla
del programa.

La razón es histórica y concreta: este proyecto ya tiene dos capas de búsqueda
opcional que se añadieron sobre el motor léxico —la semántica de la 026 y la
difusa de la 031— y la 037 encontró que la difusa **ni siquiera se conectaba al
camino normal de indexado** hasta que su propia puerta lo detectó. Ese fallo
no fue un descuido de la 031: fue el síntoma de que cada fase se mide a sí misma
y nadie mira las costuras.

Tres medidas, aplicadas a las diez fases:

- Cada fase que toque una superficie existente **dice en su informe con qué
  fases anteriores conecta**, y el gate comprueba que esa declaración sigue
  siendo cierta.
- Cada fase que añada un mecanismo nuevo comprueba que el existente no se
  desactiva, con una prueba de comportamiento y no con un aserto.
- La 050 exige distinguir **capacidades heredadas** de entregables nuevos. Un
  informe que vuelva a contar como nuevo lo que ya existía es un informe que
  no sirve para decidir nada.

## 3. El orden, y por qué no es otro

```
041 UX ─┐
042 búsqueda ─┼─→ 045 calidad ─→ 046 escala ─┐
043 ajustes ─┤                                 ├─→ 048 entornos ─→ 049 distribución ─→ 050 puerta
044 aprendizaje ┘                                047 almacenamiento ─┘
```

- **043 antes que 044.** El aprendizaje local es un ajuste que el usuario
  controla. Construir la señal antes de tener el sistema de ajustes produce
  algo que se puede activar sin poder desactivarse con criterio.
- **045 después de 041–044.** La calidad de búsqueda se mejora ante fallos
  reales. Los fallos de *presentación* que la 041 descubra son fallos de 041.
- **047 antes que 049.** El instalador decide qué hace con los datos del
  usuario. Diseñar esa política sin saber qué datos existen y cuánto pesan es
  adivinar, y ya se adivinó mal una vez en la 2.0.0.
- **048 antes que 049.** No se publica una instalación para una matriz de
  sistemas que no se ha verificado.

## 4. Lo que este programa no va a hacer

- **No añadir dependencias de runtime.** Ni una. El presupuesto es `pypdf` y
  `watchdog`, y el gate lo para.
- **No tocar el contrato de consulta** de la 012 sin una puerta que demuestre
  por qué. Es el contrato más probado del proyecto.
- **No implementar autoactualización** en la 049. Ejecutar código descargado
  exige autenticidad y reversión garantizadas, y no hay ni firma ni
  infraestructura. El prompt lo dice y el plan lo repite: es una decisión, no
  una carencia de tiempo.
- **No convertir la aplicación en multiplataforma.** La 048 prueba el núcleo
  fuera de Windows como sonda, y una sonda no es soporte.
- **No anunciar soporte de firma digital** sin evidencia. Lo que no se puede
  demostrar se escribe como «sin firmar», que es lo que hay hoy.

## 5. Riesgos asumidos

- **Un programa de UX es el más difícil de verificar de todos.** «Se siente
  coherente» no es una puerta. La 041 tiene que traducirlo en algo falsable:
  jerarquía medida, orden de foco, contraste, y un recorrido de teclado que
  la 039 ya sabe medir. Si la 041 no logra eso, su informe lo dirá y no la
  llamará terminada.
- **La 046 corre el riesgo de** paralelizar por reflejo. El prompt lo prohíbe
  («no se añade concurrencia a ciegas») y la regla del proyecto también: solo se
  optimiza lo que está medido como cuello de botella.
- **La 049 depende de un entorno Windows limpio** que este equipo no ofrece de
  forma reproducible. Si no puede validarse, se declara **NO VALIDADO**, como
  se hizo con Inno Setup en la auditoría de 2.0.0.
- **Sin revisión independiente.** Declarado desde la 2.0.0 y mantenido. La red
  de seguridad es la suite completa, pyflakes, el gate de 23 invariantes y las
  puertas por fase.
