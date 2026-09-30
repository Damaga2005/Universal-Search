# Fase 029 — Release engineering y CI

## Que se entrego

1. **Las puertas de CI son un contrato verificado**
   (`tests/test_ci_gates.py`, 13 tests): ningun trabajo que bloquea el release
   puede llevar `continue-on-error`, el workflow debe ser de solo lectura y
   serializado (`concurrency` + `cancel-in-progress`), y cada puerta
   obligatoria (pyflakes, suite completa, migraciones, fiabilidad, calidad de
   busqueda, privacidad, observabilidad/recuperacion, shell, contrato de
   release) debe aparecer nombrada en el workflow. Si alguien borra un paso,
   la suite falla.
2. **Contrato de dependencias verificado**: las unicas dependencias de
   runtime son `pypdf` y `watchdog`; el extra `build` es opcional y disjunto.
   No se puede colar un cliente HTTP, un modelo o un SDK de telemetria sin que
   un test lo diga.
3. **Humo del paquete mas completo**: el trabajo `package` comprueba la
   existencia de **los dos** ejecutables, ejecuta `diagnose self-test`,
   `diagnose export` y `diagnose recover orphan-derived`, comprueba que la
   recuperacion destructiva se niega sin `--yes`, y **arranca la ventana** del
   ejecutable GUI para detectar un build que muere al importar. Los hashes y el
   paquete de soporte se adjuntan con `if: always()`: un fallo de humo no se
   lleva por delante la evidencia.
4. **Puertas por fase nombradas en el log**: los tests de cada fase se ejecutan
   ademas de la suite completa, para que una regresion diga su nombre.

## El defecto real que encontro el humo

El humo empaquetado fallo la primera vez, y el fallo era cierto: la consulta
`recetas` no encontraba `receta.md`. La causa no era el empaquetado, eran dos
defectos de la fase 026:

- **idf sin suavizar**: `log(n / df)` vale exactamente 0 para todo n-grama
  presente en *todos* los documentos. Con un solo documento indexado —el
  estado real justo despues de una instalacion limpia— toda la capa semantica
  se apagaba en silencio. Ahora es `log((n + 1) / (df + 1)) + 1`: monotona en
  `df` (ordena los n-gramas igual que antes) pero nunca cero.
  `NGRAM_VERSION` pasa a 2, asi que los vectores incompatibles se reconstruyen
  en vez de reinterpretarse.
- **Puerta de precision con interseccion exacta**: exigia que la palabra de la
  consulta fuera *identica* a la del documento, de modo que bloqueaba
  exactamente la variante morfologica (`receta`/`recetas`) que la capa promete
  recuperar. Ahora acepta un prefijo cuando ambas palabras tienen 5 caracteres o
  mas. El suelo de longitud es lo que mantiene el contrato "no debe recuperar
  nada": un fragmento de 3-4 letras (`nad` de `nada`) sigue sin contar.

### Medicion tras el arreglo (no una afirmacion)

Corpus etiquetado de la fase 026, 27 documentos, 18 consultas:

| Metrica | Lexico | Hibrido | Antes (026) |
|---|---|---|---|
| R@5 medio | 0.817 | **0.947** | 0.947 |
| R@5 de las consultas labelled como fallo | 0.179 | **0.762** | 0.762 |
| Correccion de coincidencia exacta | 1.0 | **1.0** | 1.0 |
| Regresiones top-1 preexistentes | 0 | **0** | 0 |
| `zzz no existe` / `noexistenadaquienadie` | vacio | **vacio** | vacio |

Ninguna cifra se movio. El arreglo cambia *cuando* la capa esta disponible, no
*que* recupera. `evaluation/semantic_baseline.json` registra ahora la version
del modelo, la formula de idf y la regla de la puerta, y
`test_committed_semantic_baseline_records_the_measured_gate` falla si el
baseline describe un modelo que ya no se envia.

## Pruebas

- RED: `tests\test_ci_gates.py` -> 5 fallos (sin `concurrency`, sin pasos por
  fase, sin `self-test`/`export` en el humo, ejecutable GUI no comprobado).
- RED: `test_precision_gate_accepts_morphological_variants` fallo con `[]`:
  primero por la puerta de token exacto, y despues —al corregirla— por el idf
  sin suavizar. Los dos fallos eran reales y distintos; el segundo solo se
  vio al reintentar.
- GREEN: suite completa **891 passed, 3 skipped**; pyflakes limpio.
- Humo del paquete: `smoke029.py` sobre los `.exe` reales -> **OK**.

## Limitaciones

- El trabajo de Ubuntu sigue siendo un sondeo no bloqueante (decision de la
  fase 020): GUI, registro, atajo e instalador son de Windows por diseno.
- La ventana GUI del humo se lanza 5 s y se mata; no hay interaccion. Detecta
  un build que muere al importar, no un fallo de layout.
- `tests/test_ci_gates.py` lee el workflow como texto: PyYAML no es una
  dependencia y no se aniade solo para esto. Las aserciones cubren las
  invariantes que de verdad se rompen (pasos borrados, `continue-on-error` en
  una puerta, dependencia nueva), no la sintaxis completa.
- La matriz de Python sigue siendo 3.12 en el gating; el entorno local es
  3.14. Anadir mas versiones es trabajo de 030, no de esta fase.
