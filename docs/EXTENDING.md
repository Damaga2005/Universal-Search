# Extender Universal Search

Dos puntos de extensión, ambos **registros internos**: los *providers*
(localizan ficheros) y los *extractors* (leen su contenido). Son asuntos
distintos y ni el motor de búsqueda, ni el ranking, ni la GUI saben cuál
participó en un resultado.

## Decisión: sin plugins de terceros en tiempo de ejecución

No se carga código arbitrario al arrancar. Las razones son del propio
proyecto, no criterias de estilo:

1. **Choca con el modelo de privacidad de la fase 018.** Cargar un plugin
   significa ejecutar código de terceros con acceso al índice privado del
   usuario. Aceptarlo obligaría a un modelo de confianza y a una cadena
   de firmas que esta aplicación no tiene ni pretende tener.
2. **No hay un problema que exija dynamicidad.** Los formatos y las
   fuentes que Universal Search admite son pocos y estables; un registro
   estático es comprobable, revisable y no necesita versionar una ABI.
3. **Coste de mantenimiento.** Un sistema de plugins real exige versiones,
   aislamiento, descubrimiento y política de actualizaciones: complejidad
   que este proyecto no puede justificar frente a una pull request.

Añadir un provider o un extractor es un cambio de código, revisado como
cualquier otro. Lo que la fase garantiza es que **no obliga a reescribir
el núcleo**.

## Contrato de provider

```python
class MiProvider:
    key = "mi-nas"              # identificador estable
    version = "1.0"             # versión del provider, no del contrato
    capabilities = frozenset({ENUMERATE, METADATA, IDENTITY, CHANGE_DETECTION})

    def available(self) -> bool: ...
    def discover(self, root: Path) -> Iterable[Document]: ...
```

Capacidades declaradas (`providers/base.py`):

| Capacidad | Significa |
|---|---|
| `enumerate` | Recorre una raíz y produce entradas |
| `metadata` | Aporta tamaño y fechas sin leer contenido |
| `content` | Puede devolver el texto del documento |
| `change_detection` | Permite decidir si algo cambió por tamaño/mtime |
| `availability` | Distingue local, solo-nube e indisponible |
| `identity` | Aporta una identidad estable y única |

Reglas del registro (`ProviderRegistry`):

- Una clave duplicada se **rechaza**, no sustituye al provider que ya
  funciona.
- Capacidades desconocidas se rechazan: una errata no se convierte en
  "sin capacidad" silenciosamente.
- Un provider que falla al responder `available()` se informa como
  **no disponible**, con el motivo. La excepción no sube al indexador.
- `infos()` es la vista inspeccionable; `universal-search extensions` la
  imprime con versiones y capacidades.

## Contrato de extractor

Un extractor es una extensión → función `Path -> ExtractionResult`, con
límite de caracteres declarado. `extract()` es la única vía por la que se
produce texto, y **nunca lanza**: un extractor roto produce un
`ExtractionResult(error=…)` y el documento queda indexado sin texto.
Formatos no soportados devuelven vacío **sin error** y sin abrir el
fichero (un PNG no se interpreta como UTF-8).

`universal-search extensions` lista extractores, extensiones y límites.

## Identidad

`documents.path` es UNIQUE: el índice se indexa por ruta absoluta, así
que

- un fichero que un proveedor de nube sincroniza en una carpeta local es
  **un** documento, no dos (y su `source` se deriva de la ruta);
- la identidad (`document_id_for(source, path)`) es estable entre
  ejecuciones y entre máquinas para la misma ruta y el mismo origen;
- dos fuentes con la misma ruta no pueden coexistir, y eso es intencionado.

## Proveedores incluidos

| Provider | Capacidades | Nota |
|---|---|---|
| `local` | todas | Metadatos antes que contenido; no sigue symlinks |
| `onedrive` | `enumerate`, `metadata`, `availability`, `identity`, `change_detection` | **No** declara `content`: leer un marcador descargaría el fichero |

OneDrive es una *capa* sobre el escáner local (atributos de Windows +
disponibilidad), no un escáner paralelo: por eso no duplica la lógica de
recorrido.

## Añadir una fuente (NAS, disco extraíble, otra nube)

1. Implementa el contrato y **declara solo las capacidades que cumple**.
2. Regístrala en `providers/registry.py` (o en el registro de tu proceso).
3. Si la fuente es un sistema de ficheros montado (NAS, USB), basta un
   provider `enumerate`/`metadata` sobre la ruta: el extractor y el
   ranking no cambian.
4. Si necesitas contenido, delega en los extractores existentes: no
   escribas un lector de formato.

Lo que **no** hay que tocar: `SearchEngine`, `ranking.py`, la GUI, la
base de datos. Un test lo verifica (el motor y el ranking no importan
providers).

## Añadir un formato

1. Escribe `read_x(path) -> ExtractionResult` con su propio límite.
2. Añádelo a `EXTRACTORS` con sus extensiones.
3. Decláralo en `extractors.infos()` para que sea inspeccionable.

## Lo que queda fuera (y por qué)

- **Carga dinámica de plugins**: ver la decisión al principio.
- **Marketplace**: sin carga dinámica no tiene sentido.
- **Aislamiento en subproceso** para extractores: trabajo futuro; hoy un
  PDF hostil puede agotar memoria hasta el límite de 2 MB, no ejecutar
  código.
- **Proveedores de red que reescriban el motor**: una fuente de red que
  necesite *reranking* es un producto distinto; aquí caben como
  enumeradores.
