# Fase 018 — Privacidad y Seguridad

## Qué se entregó

1. **`docs/PRIVACY.md`**: modelo de amenazas priorizado, inventario de
   datos con propósito/retención/borrado para cada categoría y supuestos
   explícitos (equipo de confianza, sin cifrado en reposo, extractores en
   proceso).
2. **Inventario como dato, no como prosa**
   (`universal_search/privacy.py::INVENTORY`): siete categorías con su
   ubicación, para qué sirve, cuánto tiempo se guarda y cómo se borra. Un
   test exige que todas tengan los cuatro campos y que
   `leaves_machine` sea `False` en todas: no basta con decir "es local",
   tiene que ser una propiedad comprobable.
3. **`privacy show` y `privacy forget`**: el primero muestra el inventario
   con los tamaños reales medidos; el segundo quita un documento del
   índice **y todo lo derivado de él** (fila FTS, inteligencia y sus
   señales de uso), dejando el fichero intacto.
4. **`docs/PRIVACY.md` enlazado** desde README y arquitectura.
5. **24 tests nuevos** (`test_privacy.py`). Suite completa: **489 en
   verde** (uno omitido donde el SO no permite crear symlinks).

## El hallazgo importante: `snippet()` de FTS5 era un DoS local

Al probar el límite de tamaño de documento, la suite **se colgó**. En vez
de rebajar el tamaño del test hasta que pasara, medí el coste por paso:

| Documento (término repetido) | Indexar | Buscar |
|---|---:|---:|
| 80 KB (~20k tokens) | 0.04 s | **6.16 s** |
| 400 KB (~100k tokens) | 0.20 s | **> 150 s** |

Separando dentro de SQLite, con 20k tokens:

| Operación FTS5 | Coste |
|---|---:|
| `MATCH` | 0.00 s |
| `MATCH` + `bm25` | 0.00 s |
| `MATCH` + `ORDER BY rank` | 0.00 s |
| `MATCH` + `snippet()` | **5.78 s** |

Causa: `snippet()` recorre **todas las instancias de la frase**, así que su
coste crece con cuántas veces aparece el término, no con el tamaño del
documento. Medida por forma de contenido:

| Contenido | Coste de `snippet()` |
|---|---:|
| 8 KB, término 3–10 veces (prosa real) | 0.3–0.4 ms |
| 40 KB, término 10 000 veces (log patológico) | **1881 ms** |

**Corrección**: el fragmento lo genera ahora `build_snippet()`, que ya
existía como reserva y es una única pasada lineal. Se elimina la llamada a
`snippet()` del SQL. Ninguna interfaz mostraba los marcadores `[…]` de
FTS5 (la GUI los elimina y el CLI los mostraba por accidente), así que la
pérdida es invisible y la latencia deja de depender de cuántas veces
aparece un término.

Consecuencia honesta: las cifras de latencia de la fase 011 (20–30 ms)
eran sobre documentos de 1.3 KB; esta fase añade la clase de entrada que
faltaba (un único fichero enorme) y la hace verificable.

## Segundo arreglo: escrituras de estado en Windows

El test del worker falló dos veces en la sesión con
`PermissionError [WinError 5]` al hacer `os.replace` del fichero de
estado. No era un problema del test: el worker escribe ese fichero
continuamente y un antivirus o el propio lector pueden bloquearlo unos
milisegundos. `write_status` ahora reintenta cinco veces con 20 ms de
espera antes de rendirse, y el fallo real sigue logged y no silencioso.

## Verificaciones de endurecimiento (todas ya existentes, ahora con test)

| Propiedad | Test |
|---|---|
| SQL siempre parametrizado, MATCH sin inyección | 8 entradas hostiles (Fase 012) |
| Fichero inaccesible no tumba el pase | Se usa el seam `read_content` del indexador: el fichero se indexa **sin texto**, el error se cuenta, la búsqueda por nombre sigue funcionando |
| Rutas con comillas, punto y coma y `..` | Se indexan y se recuperan como texto inerte |
| Symlinks | El escaneo no desciende en directorios enlazados (omitido si el SO no permite crearlos) |
| Documento patológico / corrupto | Limitado a 2 MB; el índice sigue respondiendo |
| Registro sin contenido ni consultas | Un secreto dentro de un PDF corrupto no aparece en el log |
| Sin red | Test que falla si alguien importa `socket`/`http`/`urllib`/`requests`/`ftplib`/`smtplib` |

## Criterios de aceptación (spec 018)

| Requisito | Estado |
|---|---|
| Modelo de amenazas documentado | ✅ `docs/PRIVACY.md` con prioridades y supuestos |
| Flujo de datos solo local verificado | ✅ test de ausencia de imports de red + `leaves_machine=False` |
| Registro seguro por defecto | ✅ acotado a 500 caracteres + test con secreto y consulta privada |
| Borrado y reconstrucción funcionan | ✅ `privacy forget`, `usage clear`, `intelligence clear`, `diagnose repair all` |
| Entrada malformada no mata el servicio | ✅ extracción aislada por documento, errores contados |
| Regresiones de seguridad | ✅ 24 tests nuevos |
| Sin dependencia de red innecesaria | ✅ ninguna añadida; una test lo vigila |

## Limitaciones (deliberadas)

- **El índice no se cifra**: está en el perfil del usuario. Cifrado en
  reposo es responsabilidad del sistema (BitLocker) y una capa propia
  impediría la búsqueda sin descifrar por consulta.
- **Los extractores corren en proceso**: un PDF hostil puede DoS el
  proceso, no ejecutar código. Aislarlos es trabajo futuro.
- **Sin verificación de firmas** en ejecución: la cadena de distribución
  (instalador) es la responsable, no la aplicación.
- **El equipo es de confianza**: cualquiera con la sesión iniciada puede
  leer el índice. Esto no es un producto multiusuario.
- **`usage_events` guarda el texto de la consulta** que abrió un
  resultado. Desactivado por defecto y borrable, pero si alguien lo activa
  debe saberlo: está en el inventario y en el README.
- La fase no añadió cifrado, auditoría de accesos ni sincronización:
  ninguna de las tres es coherente con "local, sin cuentas, sin servidor".

## Cómo ejecutar

```bash
universal-search privacy show
universal-search privacy forget C:\Users\me\Docs\informe.pdf
universal-search usage clear

.venv\Scripts\python -m pytest tests\test_privacy.py
```
