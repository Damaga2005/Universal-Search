# Privacidad y seguridad

Universal Search indexa material privado: rutas, metadatos y el texto
extraído de los documentos. Este documento es el inventario de lo que se
guarda y el modelo de amenazas asumido. No es un documento de marketing:
cada punto corresponde a una comprobación automática
(`tests/test_privacy.py`) o a una limitación explícita.

## Dos reglas

1. **Nada sale de este equipo.** No hay código de red en la aplicación: un
   test falla si alguien importa `socket`, `http`, `urllib`, `requests`,
   `ftplib` o `smtplib` en el paquete.
2. **Todo se puede borrar** sin reinstalar: artículo por artículo
   (`privacy forget`, `usage clear`, `intelligence clear`) o en bloque
   (`diagnose repair all`).

## Inventario de datos

Cada elemento está declarado como datos en
`universal_search/privacy.py::INVENTORY` (no en una prosa que se pueda
quedar vieja) y se muestra con `universal-search privacy show`.

| Elemento | Qué contiene | Para qué | Retención | Cómo se borra | ¿Sale? |
|---|---|---|---|---|---|
| `documents` | ruta, nombre, tamaño, fechas, hash de contenido | saber qué indexar, detectar cambios, abrir el fichero | hasta que el fichero desaparece o se olvida/reconstruye | `privacy forget <ruta>`, `diagnose repair all` | No |
| `documents_fts` | texto extraído (nunca el binario), máx. 2 MB por documento | búsqueda y fragmentos | con la fila del documento | `forget`, o `index` tras borrar el fichero | No |
| `document_intelligence` | idioma, encabezados, 24 términos, 16 pares | documentos relacionados (fase 014) | hasta reconstruir, limpiar u olvidar | `intelligence clear` | No |
| `usage_events` | id de documento + **texto de la consulta** abierta | mejora opcional del ranking (fase 008) | hasta limpiar; **desactivado por defecto** | `usage clear`, `diagnose repair all` | No |
| `config.json` | preferencias, contextos, consultas recientes | configuración y menú Recientes | hasta limpiar o borrar el fichero | `recent clear` | No |
| `universal-search.log` | eventos, niveles y rutas; **nunca texto del documento** | diagnóstico; cada mensaje se corta a 500 caracteres | 1 MB × 3 rotados | borrar el fichero | No |
| `metrics.jsonl` | latencias, recuentos, duraciones; **sin texto de consulta** | visibilidad de rendimiento (fase 011) | compactado al pasar de 512 KB | borrar el fichero | No |

## Modelo de amenazas (priorizado por plausibilidad)

| Amenaza | Mitigación | Comprobado por |
|---|---|---|
| **Inyección SQL/FTS5** desde la consulta | Todo el SQL va con parámetros ligados; la cadena `MATCH` se construye solo con palabras entrecomilladas y columnas de lista blanca (fase 012) | `test_query_parser`, `test_privacy` (8 entradas hostiles) |
| **Fichero malformado o enorme** | Extracción con límite de 2 MB; el texto se normaliza (NUL fuera); un fallo se cuenta y el documento queda sin texto, sin tumbar el índice | `test_privacy` (documento patológico, NUL, basura) |
| **Rutas maliciosas o con traviesos** | Las rutas vienen del escaneo del sistema; los nombres hostiles se indexan como texto inerte; ningún nombre se interpola en SQL | `test_privacy` (`a'b; --.md`, `../../etc/passwd`) |
| **Symlinks / reparse points** | El escaneo **no desciende** en directorios enlazados ni indexa ficheros symlink | `test_privacy` (omitido si el SO no permite crear enlaces) |
| **Base de datos corrupta** | Diagnóstico: `check()` la marca *fatal* sin lanzar; las reparaciones cierran conexiones antes de borrar | `test_diagnostics`, `test_privacy` |
| **Procesos concurrentes** | WAL para lectores/escritores; bloqueo `O_EXCL` del indexador; el PID de la ventana con instancia única | `test_background`, `test_platforms` |
| **Fuga por el registro** | Mensajes acotados a 500 caracteres; ninguna ruta de código registra texto de documento ni consultas | `test_privacy` (secreto + consulta privada) |
| **Windows multiusuario** | Todo vive bajo `%LOCALAPPDATA%` del usuario; ningún dato compartido ni claves de HKLM | `test_release` |
| **Integridad de paquetes** | Fuera del alcance de la aplicación: la verificación de firmas del instalador es responsabilidad de la cadena de distribución | ver *Limitaciones* |
| **Rutas accidentales sensibles** (`.ssh`, gestores de contraseñas) | Reglas de exclusión configurables (fase 002) + `privacy forget` | `test_providers` / `test_privacy` |

## Supuestos (dichos, no escondidos)

- El equipo y la cuenta de Windows son de confianza: cualquiera con acceso
  a la sesión puede leer el índice. Esto no es un producto multiusuario
  con aislamiento.
- El índice no está cifrado en reposo: está en el perfil del usuario y el
  disco del equipo puede estar cifrado (BitLocker), pero la aplicación no
  añade su propia capa.
- La extracción de PDF/DOCX/XLSX/PPTX confía en `pypdf` y `python-docx`;
  un archivo hostil puede agotar el proceso, no ejecutar código. Aislar la
  extracción en un proceso separado es trabajo futuro.
- `usage_events` guarda el **texto de la consulta** asociada al documento
  abierto. Por eso está desactivado por defecto y se borra con una orden.

## Controles de privacidad

```bash
universal-search privacy show                  # inventario + tamaños reales
universal-search privacy forget informe.pdf    # olvida un documento y sus derivados
universal-search usage clear                   # borra las señales de uso
universal-search usage off                     # desactiva el aprendizaje
universal-search intelligence clear             # borra lo derivado
universal-search diagnose repair all --root … --yes   # borra todo y reindexa
```

## Limitaciones

- **El log de la GUI** puede incluir rutas (metadatos), nunca contenido.
  Las rutas son datos personales potenciales: por eso el diagnóstico los
  limita a tres ejemplos.
- **La carpeta de origen no se cifra** y las rutas se guardan en claro
  porque hacen falta para abrir el fichero.
- **No hay verificación de firmas** en tiempo de ejecución: una
  instalación modificada sigue funcionando (correcto para desarrollo,
  insuficiente como garantía de distribución).
- **Los extractores corren en proceso**: un PDF hostil puede agotar
  memoria hasta el límite de 2 MB por documento, pero no ejecutarse.
