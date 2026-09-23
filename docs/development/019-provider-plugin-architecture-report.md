# Fase 019 — Provider & Extension Architecture

## Qué se entregó

1. **Contratos explícitos** (`providers/base.py`): `DocumentProvider` ahora
   declara `key`, `version`, `capabilities` y `available()` además de
   `discover()`; seis capacidades nombradas (`enumerate`, `metadata`,
   `content`, `change_detection`, `availability`, `identity`) y una versión
   de interfaz (`INTERFACE_VERSION`).
2. **Registro con negociación de capacidades** (`ProviderRegistry`): registro,
   duplicados rechazados, capacidades desconocidas rechazadas, y
   `for_capability()` que solo ofrece proveedores **disponibles**.
   Un proveedor que falla al responder `available()` se informa no
   disponible, con el motivo: el aislamiento de fallos es una propiedad
   del tipo, no una convención.
3. **Registro de los proveedores reales** (`providers/registry.py`):
   `local` (las seis capacidades) y `onedrive` (todas **menos** `content`:
   leer un marcador descargaría el fichero).
4. **Extractores inspeccionables** (`extractors.infos()`): clave,
   extensiones, límite de caracteres y nota por extractor; `extract()`
   sigue siendo la única vía de producción de texto.
5. **`universal-search extensions`**: imprime proveedores con versión,
   capacidades y disponibilidad, y extractores con sus límites.
6. **13 tests nuevos** (`test_extensions.py`) con proveedores falsos.
   Suite completa: **502 en verde**.

## La decisión que la spec pedía evaluar: sin plugins dinámicos

La spec pedía determinar si los plugins de terceros en tiempo de ejecución
son realmente necesarios. **No lo son**, por tres razones del propio
proyecto:

1. **Choca con el modelo de privacidad de la fase 018**: cargar un plugin
   es ejecutar código de terceros con acceso al índice privado.
2. **No hay un problema que exija dynamicidad**: los formatos y las fuentes
   son pocos y estables; un registro estático es comprobable y revisable.
3. **Coste**: un sistema de plugins real exige versiones, aislamiento,
   descubrimiento y política de actualizaciones.

El contrato completo está en `docs/EXTENDING.md`. Lo que la fase sí
garantiza es lo que la spec pedía: **añadir una fuente no obliga a
reescribir el núcleo**, y un test lo verifica leyendo el código de
`search.py` y `ranking.py` para comprobar que no importan providers.

## Hallazgo sobre la identidad

Al probar «mismo fichero por dos proveedores» el test falló con
`UNIQUE constraint failed: documents.path`. No era un bug: es la
semántica real del índice, y es la correcta para un buscador de
ficheros.

- `documents.path` es UNIQUE: el índice se indexa por **ruta absoluta**.
- Un fichero que OneDrive sincroniza en una carpeta local es **un**
  documento, no dos, y su `source` se deriva de la ruta
  (`source_for_path`).
- `document_id_for(source, path)` sigue siendo estable entre ejecuciones.

El test se reescribió para afirmar la propiedad real en vez de un supuesto
equivocado, y `EXTENDING.md` la documenta.

## Un test existente revealó que el contrato era demasiado flojo

`tests/test_provider.py::test_custom_provider_documents_are_searchable`
definía un proveedor con **solo** `discover()` y afirmaba que cumplía el
protocolo. Con el contrato nuevo, `isinstance(...)` falla: el protocolo
tenía una sola exigible. Eso es exactamente lo que la fase formaliza, así
que el test se adaptó al contrato completo (key, version, capabilities,
available) en vez de relajar el protocolo.

## Arreglo de fiabilidad encontrado de paso

`test_start_stop_and_no_duplicate_process` falló dos veces bajo carga: el
`start()` del worker esperaba 5 s a que el proceso publicara su bloqueo, y
una máquina ocupada convertía un worker correcto en «falló al arrancar».
El defecto era del producto, no del test (un usuario en un portátil con
carga vería «falló» con un worker perfectamente sano), así que el valor por
defecto pasó a 10 s con la razón escrita en el docstring.

## Criterios de aceptación (spec 019)

| Requisito | Estado |
|---|---|
| Interfaces explícitas | ✅ protocolo con capacidades, versión y registro |
| Un provider nuevo sin reescribir el núcleo | ✅ test que lo comprueba sobre el código |
| Fallos aislados | ✅ disponibilidad y extractores que fallan se reportan |
| Identidades estables | ✅ test de unicidad por ruta + `document_id_for` |
| Documentación de extensiones | ✅ `docs/EXTENDING.md` |
| Registro inspeccionable | ✅ `universal-search extensions` |
| Formatos no soportados | ✅ sin error y sin abrir el fichero |
| Compatibilidad de configuración | ✅ versiones de provider e interfaz visibles |

## Limitaciones (deliberadas)

- **El registro se rellena en código**, no desde configuración: un
  provider es una implementación, no un parámetro.
- **`interface_version` es 1 y solo se muestra**: no hay migración entre
  versiones de interfaz todavía, porque no ha hecho falta; el campo
  existe para que el día que la haga sea visible.
- **Los extractores no se aíslan en subproceso**: un PDF hostil puede
  agotar memoria hasta el límite de 2 MB. Heredado de la fase 003 y
  documentado en `PRIVACY.md`.
- **La negociación de capacidades no dirige todavía el indexador**: el
  indexer usa el escáner local y trata OneDrive como capa de atributos
  (que es la arquitectura real). La negociación existe y es inspeccionable
  para que el día que haya una NAS no haya que inventar el mecanismo.

## Cómo ejecutar

```bash
universal-search extensions
.venv\\Scripts\\python -m pytest tests\\test_extensions.py
```
