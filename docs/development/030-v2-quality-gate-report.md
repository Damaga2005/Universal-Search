# Fase 030 — Puerta de calidad v2

Esta fase no entrega una funcionalidad. Entrega la manera de saber, con
evidencia, si el arbol actual puede llamarse Universal Search v2.

```
python -m evaluation.gate
```

## Las trece invariantes

| # | Comprobacion | Resultado medido |
|---|---|---|
| 1 | Presupuesto de dependencias | runtime `['pypdf', 'watchdog']`, extra `build` `['pyinstaller']` |
| 2 | Sin imports de red ni de modelo | limpio en todo `src/universal_search` |
| 3 | El nucleo es independiente de la plataforma | 50 modulos del camino de datos, sin Win32 ni Tk |
| 4 | Todo punto de contacto Win32 esta declarado | 1 declarado fuera de la costura, con su motivo |
| 5 | La costura de plataforma existe | `open_path`, `reveal`, `set_autostart`, `notify`, `set_dpi_awareness` |
| 6 | Inventario de privacidad completo | 12 tablas, todas declaradas o exentas justificadas |
| 7 | Las reparaciones no tocan ficheros del usuario | 2 ficheros byte a byte identicos tras 7 reparaciones |
| 8 | Sin depuracion abandonada | limpio |
| 9 | Version con fuente unica | 1.0.0 / esquema 9, coherente en los tres sitios |
| 10 | Cada fase documentada | 30 fases con documento sustancial y fila en el indice |
| 11 | CHANGELOG cubre cada fase | 30 fases trazables (titulos + rango de 1.0.0) |
| 12 | Conteo documentado = conteo real | 919 recogidos (916 passed, 3 skipped), la documentacion coincide |
| 13 | Hoja de ruta cerrada | sin fases abiertas |

`VERDICT: PASS (13 of 13)`.

## Lo que el gate encontro de verdad

El gate no se escribio para pasar. Salio rojo seis veces y tres de esos fallos
eran defectos reales:

1. **`background.py` importaba `winreg`** sin necesitarlo: `_registry()` era
   codigo muerto desde que el autostart delega en la costura de plataforma.
   Eliminado, y con el el nucleo queda sin ningun import de Win32.
2. **`recovery.py` y `observability.py` usaban `with connection`**, que hace
   `commit` pero **no cierra**. En Windows la base de datos seguia bloqueada
   despues de una reparacion, y la siguiente reparacion destructiva se negaba
   con "index.db is in use". Ambos usan ahora `contextlib.closing`. La
   disciplina ya estaba escrita en `docs/ARCHITECTURE.md` ("Windows no borra
   una base de datos bloqueada") y mi codigo no la cumplia.
3. **La puerta de reparaciones era un regex y no servia.** `path.unlink()` en
   un bucle sobre `paths.*` parecia tan sospechoso como `documento.unlink()`.
   Se sustituyo por una prueba de comportamiento: indexar un arbol real, lanzar
   los cuatro casos de recuperacion, `privacy forget` y dos reparaciones
   destructivas, y comparar el arbol byte a byte.

Ademas, el gate detecto dos casos que habrian fallado en silencio:

- `hotkey.py` importa `ctypes` porque `RegisterHotKey` no tiene equivalente
  portable. Eso es una integracion de Windows, no el camino de datos. En vez de
  ampliar la excepcion a mano, la lista `WINDOWS_INTEGRATION_MODULES` obliga a
  **declarar el motivo** de cada excepcion, y el test
  `test_a_new_win32_touchpoint_would_be_rejected` mete un `import ctypes` en
  `index/` y comprueba que el gate lo rechaza.
- La fase 001 no tiene informe separado (predate la division prompt/informe):
  su documento *es* el registro. La comprobacion acepta un documento
  sustancial por fase y prohibe los vacios, en vez de exigir un archivo que
  nunca existio.
- El CHANGELOG documenta las fases 001-010 bajo un unico titulo de release
  (`## [1.0.0] - 2026-09 (phases 001-010)`). La comprobacion acepta el rango
  explicito; reescribir la historia para satisfacer un linter seria peor que
  leer el rango.

## El gate esta gateado

`tests/test_v2_gate.py` (23 tests) ejecuta cada invariante y, donde es barato,
comprueba que la comprobacion **sabe decir que no**: rompe un arbol temporal
(documentacion ausente, conteo equivocado, roadmap con una fase abierta) y
exige el fallo. Un gate sin tests es un gate que se pudre en un verde
permanente.

## TDD log

- RED: `python -m evaluation.gate` -> `VERDICT: FAIL (6 of 12)`, con
  `core is platform independent` y `repairs never touch user files` fallando
  por motivos reales, no por un test mal escrito.
- El chequeo de conteo fallo con "collection failed": `pyproject.toml` ya
  anade `-q` y el gate anadia `-q` otra vez, lo que silencia la linea de
  resumen que el gate leia. Se elimino el `-q` duplicado.
- Al fijar el numero de documentacion aparecio un `TypeError` por mezclar `int`
  y `str` en la misma lista de fallos del chequeo de fases; la logica se
  separo en `missing` (fases) y `thin` (ficheros).
- GREEN: 13/13 y `VERDICT: PASS`, con la suite en **916 passed, 3 skipped** y
  pyflakes limpio.

## Compatibilidad y limitaciones

- El gate es un instrumento de desarrollo, como `benchmarks/` y `evaluation/`.
  No se empaqueta en el ejecutable: no es tarea del usuario final.
- `check_documented_test_count` lanza `pytest --collect-only`, que tarda menos
  de un segundo, pero es el unico check que speak de un subproceso.
- El gate lee el codigo y la documentacion; no sustituye a la suite. La
  combinacion es la que importa: la suite demuestra comportamiento, el gate
  demuestra que las afirmaciones del proyecto siguen siendo ciertas.
- `METADATA_TABLES` (dos entradas: `schema_migrations` y `sqlite_sequence`) son
  las unicas exenciones del inventario de privacidad, y cada una lleva su
  motivo escrito. Anadir una tabla nueva sin declararla hace fallar el gate.
