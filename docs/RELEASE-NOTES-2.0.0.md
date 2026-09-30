# Universal Search 2.0.0 — notas de publicación

- **Versión**: 2.0.0
- **Fecha**: 2026-09-30
- **Commit**: el commit que publica esta release (ver `CHANGELOG.md` y el manifiesto)
- **Fases cubiertas**: 011–030 (la 1.0.0 publicó 001–010)
- **Plataforma**: Windows 10/11, x64
- **Licencia**: MIT

## Resumen

Universal Search 2.0.0 es un buscador local de archivos para Windows con
índice persistente, ventana de escritorio, indexador en segundo plano y una
capa de búsqueda semántica opcional que funciona sin internet. Todo el
procesamiento ocurre en la máquina del usuario: no hay APIs externas, ni cloud
AI, ni telemetría.

Esta es una versión mayor. El índice pasa del esquema 3 (1.0.0) al esquema 9
con migraciones aditivas, y la superficie del CLI, del indexador y del
diagnóstico crece de forma incompatible para scripts que dependieran de 1.0.0.

## Novedades por area

### Rendimiento y escalado (011)

Perfilado real sobre 2 000 documentos: la indexación inicial bajó de 45,3 s a
2,8 s y la consulta media de 48,6 ms a 34,4 ms. El trabajo de indexación se
desacopló del resto con métricas por operación y compactación.

### Windows e integración de sistema (016, 021, 027)

- Registro en la bandeja de notificaciones, con recuperación tras reiniciar
  Explorer y sin procesos huérfanos.
- Instancia única: un segundo lanzamiento pide que aparezca la ventana
  existente en vez de crear otra.
- `open` y `reveal` desde el CLI y desde la GUI.
- Perimonitor DPI awareness (Windows 10 1703+ con fallback).
- Verbo per-user de Explorer "Search with Universal Search", reversible y sin
  permisos de administrador, registrado por el instalador y retirado por el
  desinstalador.

### Búsqueda avanzada y ranking (012, 013, 026)

- Lenguaje de consulta: comillas exactas, `OR`, negación (`-`), filtros
  (`type:`, `size:`, `after:`, `before:`) y `source:`.
- Ranking con señales explicadas (`--explain`): coincidencia exacta, BM25,
  frescura, frecuencia en el título y la ruta, y contexto personal opcional.
- **Capa semántica local opcional** (026): n-gramas de caracteres con TF-IDF
  y similitud coseno, versionada y reconstruible. Solo se consulta cuando el
  motor léxico no devuelve nada, nunca reordena un resultado no vacío y
  respeta los filtros. Se puede desactivar con `--no-semantic`.
  Se midió antes de decidir: sobre un corpus etiquetado de 27 documentos, el
  recall@5 de las consultas que el motor léxico no alcanzaba subió de 0,179 a
  0,762 sin mover ninguna otra métrica.

### Inteligencia local y grafo de documentos (014, 022)

- Metadatos derivados: idioma, título, encabezados, términos y co-ocurrencias,
  reconstruibles y eliminables.
- Grafo local de documentos relacionados con señales explicables (términos,
  título, encabezados, frases, directorio, referencias), mantenimiento
  incremental y acotado. Nunca modifica el ranking: solo alimenta
  "relacionados".

### Extracción de contenido (025)

Contrato de extracción versionado, con límites explícitos de recursos para
PDF y Office, diagnósticos visibles de truncado y un corpus de pruebas
adversariales (ZIP bombs, hojas de cálculo enormes, DTD oculto tras una
cabecera de 64 KiB, binarios disfrazados).

### Proveedores (007, 019, 024)

- OneDrive con carpetas sincronizadas y estado "solo en la nube" sin
  descargar.
- NAS y unidades extraíbles como proveedores montados.
- Identidad canónica `(source, path)`: dos proveedores pueden ser la misma ruta. Fallos de un proveedor aislados: los demás siguen.

### Diagnóstico y recuperación (015, 023, 028)

- Centro de control con fuentes, salud, almacenamiento y datos derivados, y
  confirmaciones explícitas para lo destructivo.
- `diagnose self-test`: siete comprobaciones (base de datos, FTS, esquema,
  proveedores, extractores, indexador, disco).
- `diagnose export`: paquete de soporte JSON que **declara** no contener
  contenido de documentos, ni texto de consultas, ni credenciales.
- `diagnose recover`: cuatro casos con nombre (`orphan-derived`,
  `dirty-derived`, `stale-coordination`, `reset-derived`). Ninguno puede
  borrar un fichero del usuario; los destructivos exigen `--yes`.

### Empaquetado y CI (020, 029, 030)

- `install.ps1` / `uninstall.ps1` sin permisos de administrador, con manifiesto
  que distingue programa de datos.
- Puertas de CI verificadas por test: borrar una puerta, permitir fallo en un
  trabajo que bloquea o declarar una tercera dependencia rompe la suite.
- `python -m evaluation.gate`: trece invariantes ejecutables, incluido un
  smoke real de los dos ejecutables congelados.

## Privacidad (018, 028)

- Nada sale del equipo. El inventario de privacidad declara cada tabla y cada
  fichero, con su propósito, retención y cómo borrarlo.
- `privacy forget` elimina el documento y **todo** lo derivado de él,
  incluidos los vectores semánticos y el grafo.
- Los logs llevan metadatos, nunca texto de documento. La auditoría
  pre-release de esta versión corrigió dos fugas reales: la GUI escribía la
  consulta en claro al fallar una búsqueda, y el redactor de eventos no
  cubría los campos `snippet` ni `document_text`.
- El aprendizaje de uso opcional guarda el texto de la consulta y está
  **desactivado por defecto**.

## Limitaciones conocidas

1. **No hay embeddings semánticos aprendidos.** La capa semántica es n-gramas
   de caracteres: cubre variantes morfológicas, acentos y solapamiento parcial
   de términos, pero no sinónimos puros sin forma compartida. Cerrar esa
   brecha exigiría descargar un modelo, añadir una dependencia y aceptar una
   licencia.
2. **La capa semántica es opcional y local.** Nunca reordena resultados ni
   ignora filtros; `--no-semantic` la desactiva.
3. **Sin APIs externas, sin cloud AI, sin telemetría.**
4. **CI valida únicamente Python 3.12.** El desarrollo local usa 3.14; el
   workflow de bloqueo no se ha ejecutado en 3.13 ni en 3.14.
5. **`hotkey.py` es la excepción declarada** a la regla de "nada de Win32 en
   el núcleo", porque `RegisterHotKey` no tiene equivalente portable. No forma
   parte del camino de datos.
6. **Sin firma digital.** Los ejecutables se publican sin firmar.
7. **Sin autoactualizador.** La actualización es manual, por decisión.
8. **El índice es local.** No se sincroniza entre máquinas.
9. **Sin revisión independiente en las fases 026–030.** El servidor de
   desarrollo reinició repetidamente y abortó los subagentes revisores; la
   revisión de 026 y la implementación de 027–030 se hicieron en la misma
   sesión. La red de seguridad fue la suite completa, pyflakes, el gate de
   030 y el smoke de los ejecutables reales, pero conviene saberlo antes de
   tomar el código como revisado por un tercero.
10. **Inno Setup sin validar.** `packaging/installer.iss` está presente y
    versionado, pero la ruta de instalación probada es `install.ps1`; no se
    afirma que el instalador Inno esté validado.
11. **Actualización desde 1.0.0 verificada con una migración real** ejecutada
    durante esta auditoría: se creó un índice con el código de la 1.0.0
    publicada, se abrió con el ejecutable 2.0.0 y se comprobaron el sello de
    esquema, el historial de migraciones, la búsqueda posterior y el rechazo
    del downgrade. No se probó una doble instalación sobre el equipo de un
    usuario final.

## Compatibilidad

- **Actualizar desde 1.0.0**: migraciones aditivas del esquema 3 al 9. El
  índice se conserva; `diagnose health` lo verifica.
- **Downgrades**: rechazados a propósito (`UnsupportedSchemaVersion`). Un
  build antiguo no toca un índice nuevo.
- **Windows**: 10 y 11. El trabajo de Ubuntu es un sondeo informativo y no
  bloquea la publicación, porque GUI, registro, atajo e instalador son de
  Windows por diseño.
