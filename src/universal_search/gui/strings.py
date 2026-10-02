"""The interface string catalogue (phase 039).

Why this exists
---------------
A user-visible literal is a string like ``"Iniciar indexador"``. Scattered
through widget code they are untranslatable, unmeasurable and uncheckable:
there is no way to ask "does every visible string have an accessible name?" or
"which of these needs a caption?" without reading the whole GUI.

So every one of them moves here, keyed, and the GUI asks for them by key. That
buys three concrete things rather than tidiness for its own sake:

* **localisation becomes possible** without touching widget code — a locale is
  a dict, not a patch;
* **the catalogue can be checked against the code** —
  :func:`untranslated_literals` finds every visible literal still written
  inline in ``gui/``, so a new button without a catalogue entry fails the
  suite instead of shipping unlocalised;
* **the catalogue is the inventory the accessibility audit works from**: a
  control with no name is a control nobody can reach with a screen reader.

The rule for a key is that it says what the string *is*, not what it says:
``INDEXER.START`` and not ``"start_button"``. A key that embeds the Spanish
text has to change the moment the text does, which is the wrong dependency.

Falsy keys are rejected by :func:`get` rather than silently returning an empty
string: a missing key in a search window is a blank label where a label should
be, and a blank label is exactly what the accessibility audit looks for.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from string import Formatter


@dataclass(frozen=True, slots=True)
class Entry:
    """One catalogue entry.

    There is no ``note`` field on purpose: the key already says where the
    string appears (``MENU.INDEXER.PAUSE`` is the Indexador menu), and a second
    copy of that information is a second copy to forget.
    """

    key: str
    value: str

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "value": self.value}


# The catalogue. Grouped by where the string appears on screen, so a
# translator reads it in the order a user meets it.
ENTRIES: tuple[Entry, ...] = (
    # -- window and menus ----------------------------------------------------
    Entry("APP.TITLE", "Universal Search {version}"),
    Entry("MENU.FILE", "Archivo"),
    Entry("MENU.FILE.COPY_PATH", "Copiar ruta del resultado (Ctrl+C)"),
    Entry("MENU.FILE.EXIT", "Salir"),
    Entry("MENU.SELECTION", "Selección"),
    Entry("MENU.SELECTION.OPEN", "Abrir seleccionados (Ctrl+O)"),
    Entry("MENU.SELECTION.REVEAL", "Mostrar seleccionados (Ctrl+R)"),
    Entry("MENU.SELECTION.COPY", "Copiar rutas seleccionadas (Ctrl+C)"),
    Entry("MENU.SELECTION.FORGET",
          "Olvidar seleccionados del índice (Ctrl+Shift+R)"),
    Entry("MENU.INDEXER", "Indexador"),
    Entry("MENU.INDEXER.START", "Iniciar indexador"),
    Entry("MENU.INDEXER.STOP", "Detener indexador"),
    Entry("MENU.INDEXER.PAUSE", "Pausar indexación"),
    Entry("MENU.INDEXER.RESUME", "Reanudar indexación"),
    Entry("MENU.INDEXER.AUTOSTART", "Iniciar con Windows"),
    Entry("MENU.DIAGNOSE", "Diagnóstico"),

    # -- search workflow (phase 042) ---------------------------------------
    Entry("MENU.SEARCH", "Buscar"),
    Entry("MENU.SEARCH.SAVE", "Guardar la búsqueda actual..."),
    Entry("MENU.SEARCH.SAVED", "Búsquedas guardadas"),
    Entry("MENU.SEARCH.SAVED_EMPTY", "(sin búsquedas guardadas)"),
    Entry("MENU.SEARCH.DELETE_SAVED", "Borrar la búsqueda guardada..."),
    Entry("MENU.SEARCH.APPLY_SUGGESTION", "Usar la sugerencia: {query}"),
    Entry("MENU.SEARCH.NO_SUGGESTION", "(sin sugerencias)"),
    Entry("MENU.HISTORY", "Historial"),
    Entry("MENU.HISTORY.SHOW", "Ver el historial de búsquedas..."),
    Entry("MENU.HISTORY.CLEAR", "Borrar el historial"),
    Entry("MENU.HISTORY.DISABLE", "Dejar de recordar búsquedas"),
    Entry("MENU.HISTORY.ENABLE", "Volver a recordar búsquedas"),
    Entry("MENU.VIEW", "Ver"),
    Entry("MENU.SETTINGS", "Ajustes..."),
    Entry("MENU.VIEW.EXPLAIN", "Explicar por qué coincidió (recalcula)"),
    Entry("MENU.DIAGNOSE.SUMMARY", "Estado del índice"),
    Entry("MENU.DIAGNOSE.CONTROL_CENTER", "Centro de control de indexación..."),
    Entry("MENU.DIAGNOSE.RELATED", "Documentos relacionados..."),

    # -- the search bar ------------------------------------------------------
    Entry("SEARCH.PLACEHOLDER_OR_LABEL", "Búsqueda"),
    Entry("SEARCH.LABEL.CONTEXT", "Contexto:"),
    Entry("SEARCH.LABEL.SOURCE", "Fuente:"),
    Entry("SEARCH.LABEL.TYPE", "Tipo:"),
    Entry("SEARCH.LABEL.SORT", "Orden:"),
    Entry("SEARCH.LABEL.GROUP", "Agrupar:"),

    # -- how results can be organised (phase 042) ---------------------------
    Entry("SORT.RELEVANCE", "Relevancia"),
    Entry("SORT.NAME", "Por nombre"),
    Entry("SORT.MODIFIED", "Modificado"),
    Entry("SORT.SIZE", "Tamaño"),
    Entry("GROUP.NONE", "Sin agrupar"),
    Entry("GROUP.FOLDER", "Por carpeta"),
    Entry("GROUP.TYPE", "Por tipo"),
    Entry("GROUP.SOURCE", "Por fuente"),
    Entry("GROUP.DATE", "Fecha"),
    Entry("GROUP.HEADER", "Agrupado por {label}"),
    Entry("SORT.APPLIED", "Orden: {label}"),
    Entry("SEARCH.RECENTS", "Recientes ▾"),
    Entry("SEARCH.RECENTS_EMPTY", "(sin búsquedas recientes)"),
    Entry("SEARCH.READY", "Listo — escribe para buscar"),
    Entry("SEARCH.HOTKEY_SHOWN", "Atajo global — escribe para buscar"),
    Entry("SEARCH.PICK_FOLDER", "Carpeta a indexar"),
    Entry("SEARCH.QUERY_SYNTAX_HINT",
          "Revisa las comillas y los paréntesis, o pulsa Esc para empezar "
          "de nuevo."),
    Entry("SEARCH.EMPTY_TITLE", "¿Qué estás buscando?"),
    Entry("SEARCH.EMPTY_HINT",
          "Escribe para buscar por nombre o por contenido. Enter abre el "
          "resultado y Ctrl+Enter lo muestra en el Explorador."),

    # -- filters -------------------------------------------------------------
    Entry("FILTER.ALL_SOURCES", "(todas)"),
    Entry("FILTER.ALL_TYPES", "(todos)"),
    Entry("FILTER.SOURCE.LOCAL", "local"),
    Entry("FILTER.SOURCE.ONEDRIVE", "onedrive"),
    Entry("FILTER.SOURCE.NETWORK", "network"),
    Entry("FILTER.SOURCE.REMOVABLE", "removable"),

    # -- results list --------------------------------------------------------
    Entry("RESULTS.LIST_LABEL", "Resultados"),
    Entry("RESULTS.NONE", "Sin resultados para «{query}»."),
    Entry("RESULTS.NONE_HINT",
          "Prueba con menos palabras, o con un filtro menos."),
    Entry("RESULTS.ERROR", "No se pudo buscar: {reason}"),
    Entry("RESULTS.SEARCHING", "Buscando «{query}»…"),
    Entry("RESULTS.COUNT", "{count} documento(s)"),
    # Column headings. Phase 041: a result has five parts and the old list
    # flattened them into one string, so these labels name them for the first
    # time — which is also what makes the pane's hierarchy readable at a
    # glance instead of merely ordered.
    Entry("RESULTS.COLUMN.NAME", "Nombre"),
    Entry("RESULTS.COLUMN.FOLDER", "Carpeta"),
    Entry("RESULTS.COLUMN.KIND", "Tipo"),
    Entry("RESULTS.COLUMN.SOURCE", "Fuente"),
    Entry("RESULTS.COLUMN.SNIPPET", "Coincidencia"),
    Entry("RESULTS.DETAIL_LABEL", "Detalle del resultado"),

    # -- status lines that used to be f-strings ------------------------------
    # Phase 041 routed these through the catalogue. They were invisible to the
    # phase 039 string audit precisely because an f-string is not a literal,
    # so "every visible string is catalogued" was true of every string the
    # checker could see and silent about the rest.
    Entry("STATUS.CONTEXT", "Contexto: {name}"),
    Entry("STATUS.CONTEXT_NONE", "(ninguno)"),
    Entry("STATUS.FILTER", "Filtro: {source} · {type}"),
    Entry("STATUS.RELATED_COUNT", "{count} documento(s) relacionado(s)"),
    Entry("STATUS.PATH_COPIED", "Ruta copiada: {path}"),
    Entry("STATUS.PATHS_COPIED", "{count} rutas copiadas"),
    Entry("STATUS.QUERY_INVALID", "Consulta no válida: {reason}"),
    Entry("STATUS.RELATED_FALLBACK", "relación local"),
    Entry("STATUS.SAVED", "Búsqueda guardada como «{name}»"),
    Entry("STATUS.SAVED_EXISTS", "Ya existía «{name}»; se ha sustituido"),
    Entry("STATUS.SAVED_DELETED", "Búsqueda guardada eliminada: {name}"),
    Entry("STATUS.SAVED_MISSING", "No hay ninguna búsqueda guardada llamada {name}"),
    Entry("STATUS.SAVED_EMPTY_NAME", "Una búsqueda guardada necesita un nombre"),
    Entry("STATUS.SAVED_NEEDS_QUERY", "Escribe una consulta antes de guardarla"),
    Entry("STATUS.HISTORY_CLEARED", "Historial borrado: {count} consulta(s)"),
    Entry("STATUS.HISTORY_EMPTY", "El historial ya estaba vacío"),
    Entry("STATUS.HISTORY_DISABLED", "Ya no se recuerdan las búsquedas"),
    Entry("STATUS.HISTORY_ENABLED", "Las búsquedas vuelven a recordarse"),
    Entry("STATUS.SUGGESTION", "Sugerencia verificada: {query}"),
    Entry("STATUS.EXPLAIN", "Las explicaciones se recalculan en la próxima búsqueda"),
    Entry("PREVIEW.CLOUD_ONLY", "☁ solo en OneDrive (sin descargar)"),
    Entry("PREVIEW.EXPLAIN_TITLE", "Por qué coincidió"),
    Entry("PREVIEW.NO_EXPLAIN", "Activa «Explicar por qué coincidió» para ver el detalle."),
    Entry("PREVIEW.EXPLAIN_SIGNAL", "  {signal}: {value:.3f}"),
    Entry("PREVIEW.EXPLAIN_DETAIL", "  {signal}: {count} detalle(s), abajo"),

    # -- suggestions (phase 042) --------------------------------------------
    Entry("SUGGESTION.TEXT", "¿Querías decir «{query}»? Pulsa Alt+Intro para usarlo."),
    Entry("SUGGESTION.NONE", "Nada parecido en el índice."),

    # -- local history, stated (phase 042) ----------------------------------
    Entry("HISTORY.TITLE", "Historial de búsquedas"),
    Entry("HISTORY.EMPTY", "No hay consultas recordadas."),
    Entry("HISTORY.RETENTION",
          "Retención: las {max_entries} consultas más recientes, de hasta "
          "{max_chars} caracteres cada una, en {file}. Nunca se transmiten."),
    Entry("HISTORY.DISABLED_NOTE",
          "No se están recordando consultas. Lo ya guardado sigue aquí."),
    Entry("SAVED.PROMPT", "Nombre de la búsqueda guardada"),

    # -- related documents ---------------------------------------------------
    Entry("RELATED.TITLE", "Documentos relacionados"),
    Entry("RELATED.LOADING", "Cargando documentos relacionados…"),
    Entry("RELATED.NOTE",
          "Relaciones locales; no cambian la relevancia de la búsqueda."),
    Entry("RELATED.EMPTY", "No hay documentos relacionados."),
    Entry("RELATED.COLUMN.SCORE", "Puntuación"),
    Entry("RELATED.COLUMN.REASON", "Por qué"),

    # -- indexer feedback ----------------------------------------------------
    Entry("INDEXER.STATE_LABEL", "indexador: {state}"),
    Entry("INDEXER.UNKNOWN", "indexador: ?"),
    Entry("INDEXER.ERROR", "Error en el indexador — consulta el registro"),
    Entry("INDEXER.BUSY",
          "El indexador está activo; pausa o detén el trabajador antes de continuar"),

    # -- failures, in the user's words ---------------------------------------
    # Every one of these was written inline before phase 039. They are the
    # strings a user is most likely to read, and they are the ones a
    # translator most needs: a catalogue entry that a person cannot find is
    # not a catalogue.
    Entry("ERROR.SEARCH", "Error al buscar — consulta el registro de errores"),
    Entry("ERROR.OPEN", "No se pudo abrir el archivo — consulta el registro"),
    Entry("ERROR.REVEAL", "No se pudo mostrar en el explorador"),
    Entry("ERROR.ADD_FOLDER",
          "No se pudo añadir la carpeta — consulta el registro"),
    Entry("ERROR.AUTOSTART",
          "No se pudo configurar el inicio — consulta el registro"),
    Entry("ERROR.SAVE_CONTEXT",
          "No se pudo guardar el contexto — consulta el registro"),
    Entry("ERROR.DIAGNOSTICS", "No se pudo generar el diagnóstico"),
    Entry("DIAGNOSTICS.TITLE", "Diagnóstico del índice"),
    Entry("ERROR.RELATED", "No se pudieron cargar los relacionados"),
    Entry("ACTION.CANCELLED", "Cancelado: no se olvidó nada"),
    # The one confirmation whose text a user must read before authorising a
    # change to their index, and it was an f-string with the reassurance
    # ("your files on disk are not touched") buried in the middle of it.
    Entry("ACTION.FORGET_CONFIRM_TITLE", "Olvidar documentos"),
    Entry("ACTION.FORGET_CONFIRM_ONE",
          "Se borrará del índice 1 documento.\n\n"
          "Los archivos del disco no se tocan.\n¿Continuar?"),
    Entry("ACTION.FORGET_CONFIRM_MANY",
          "Se borrarán del índice {count} documentos.\n\n"
          "Los archivos del disco no se tocan.\n¿Continuar?"),
    Entry("ACTION.FORGET_NEEDS_SELECTION",
          "Selecciona al menos un documento para olvidar"),
    Entry("ACTION.RELATED_NEEDS_SELECTION",
          "Selecciona un resultado para ver relacionados"),
    Entry("ACTION.REBUILD_HINT",
          "Usa el centro de control para reconstruir el índice"),


    # -- settings window (phase 043) ------------------------------------------
    # A label and an explanation for every setting, because an advanced
    # switch nobody can understand is a switch nobody should flip. Generated
    # and checked by `tests/test_settings.py`: the phase 039 gate forbids two
    # keys carrying the same value, which is easy to break by hand and
    # obvious to check by machine.
    Entry("SETTINGS.ROOTS.LABEL",
          "Carpetas indexadas"),
    Entry("SETTINGS.IGNORE_DIRS.LABEL",
          "Carpetas excluidas"),
    Entry("SETTINGS.IGNORE_PATTERNS.LABEL",
          "Patrones excluidos"),
    Entry("SETTINGS.RESULT_LIMIT.LABEL",
          "Documentos por búsqueda"),
    Entry("SETTINGS.FUZZY_ENABLED.LABEL",
          "Búsqueda con tolerancia a erratas"),
    Entry("SETTINGS.SEMANTIC_ENABLED.LABEL",
          "Búsqueda por significado"),
    Entry("SETTINGS.HOTKEY.LABEL",
          "Atajo global"),
    Entry("SETTINGS.HOTKEY_ENABLED.LABEL",
          "Atajo global activo"),
    Entry("SETTINGS.INDEXER_INTERVAL_SECONDS.LABEL",
          "Cada cuánto se revisa"),
    Entry("SETTINGS.INDEXER_FILE_DELAY.LABEL",
          "Pausa por archivo"),
    Entry("SETTINGS.ONEDRIVE_DOWNLOAD_MAX_MB.LABEL",
          "Descarga de OneDrive"),
    Entry("SETTINGS.START_WITH_WINDOWS.LABEL",
          "Abrir el indexador al iniciar Windows"),
    Entry("SETTINGS.THEME.LABEL",
          "Tema"),
    Entry("SETTINGS.UI_SCALE.LABEL",
          "Tamaño del texto y los huecos"),
    Entry("SETTINGS.TRAY_ENABLED.LABEL",
          "Icono en la bandeja"),
    Entry("SETTINGS.USAGE_TRACKING.LABEL",
          "Aprendizaje del uso local"),
    Entry("SETTINGS.RECENT_QUERIES_ENABLED.LABEL",
          "Recordar las búsquedas"),
    Entry("SETTINGS.LOG_LEVEL.LABEL",
          "Detalle del registro"),
    Entry("SETTINGS.ROOTS.HELP",
          "Las carpetas cuyo contenido se busca. Segestionan en el centro de "
          "control de indexación."
          ),
    Entry("SETTINGS.IGNORE_DIRS.HELP",
          "Nombres de carpetas que nunca se recorren, por ejemplo una copia de "
          "seguridad que no quieres indexar."
          ),
    Entry("SETTINGS.IGNORE_PATTERNS.HELP",
          "Patrones de nombre de archivo que se saltan, como *.tmp o ~$*."
          ),
    Entry("SETTINGS.RESULT_LIMIT.HELP",
          "Cuántos resultados se piden al buscador. Más resultados Tardan más en "
          "llegar y ocupan más sitio en pantalla."
          ),
    Entry("SETTINGS.FUZZY_ENABLED.HELP",
          "Si está activada, una palabra mal escrita encuentra el documento que "
          "contiene la correcta. Cuesta un poco más de trabajo por consulta."
          ),
    Entry("SETTINGS.SEMANTIC_ENABLED.HELP",
          "Activa la capa local que encuentra documentos parecidos por tema, no "
          "solo por las palabras exactas. Solo se usa cuando la búsqueda normal "
          "no devuelve nada."
          ),
    Entry("SETTINGS.HOTKEY.HELP",
          "La combinación de teclas que abre la ventana desde cualquier sitio. "
          "Debe llevar Ctrl, Alt, Mayús o Windows."
          ),
    Entry("SETTINGS.HOTKEY_ENABLED.HELP",
          "Si está desactivado, la combinación anterior no registra nada y el "
          "indexador no la escucha."
          ),
    Entry("SETTINGS.INDEXER_INTERVAL_SECONDS.HELP",
          "Segundos entre dos revisiones de las carpetas indexadas. Más tiempo "
          "ahorraCPU y llega más tarde a los cambios."
          ),
    Entry("SETTINGS.INDEXER_FILE_DELAY.HELP",
          "Segundos de espera tras cada archivo modificado. Es una forma "
          "cooperativa de no cargar el disco; con valor alto la indexación tarda "
          "mucho más."
          ),
    Entry("SETTINGS.ONEDRIVE_DOWNLOAD_MAX_MB.HELP",
          "Tamaño máximo en MB de un archivo de OneDrive que se descarga para "
          "leerlo. Con 0 no se descarga ninguno."
          ),
    Entry("SETTINGS.START_WITH_WINDOWS.HELP",
          "Registra la aplicación para que el indexador arranque al iniciar "
          "Windows. Se escribe en el registro del usuario y es reversible."
          ),
    Entry("SETTINGS.THEME.HELP",
          "Claro, oscuro, o seguir a Windows. Ninguno de los tres cambia lo que "
          "se indexa."
          ),
    Entry("SETTINGS.UI_SCALE.HELP",
          "Escala tipografía, relleno y alto de fila a la vez. Entre 0,75 y 2,5."
          ),
    Entry("SETTINGS.TRAY_ENABLED.HELP",
          "Muestra un icono junto al reloj para abrir la ventana o salir."
          ),
    Entry("SETTINGS.USAGE_TRACKING.HELP",
          "Registra qué documentos abres para mejorar el orden de los resultados. "
          "Se guarda sólo en este equipo y está desactivado por defecto."
          ),
    Entry("SETTINGS.RECENT_QUERIES_ENABLED.HELP",
          "Guarda las últimas consultas para la lista de recientes. No graba lo "
          "que buscas si lo desactivas, pero lo ya guardado se queda."
          ),
    Entry("SETTINGS.LOG_LEVEL.HELP",
          "Cuánto se registra en el archivo de registro. WARNING sólo anota "
          "problemas, DEBUG anota cada operación y ocupa más."
          ),
    Entry("SETTINGS.TITLE",
          "Ajustes de Universal Search"
),
    Entry("SETTINGS.SAVE",
          "Guardar"
),
    Entry("SETTINGS.SAVED",
          "Ajustes guardados"
),
    Entry("SETTINGS.REFUSED",
          "{count} valor(es) no se han podido guardar"
),
    Entry("SETTINGS.RESET",
          "Restablecer"
),
    Entry("SETTINGS.RESET_TITLE",
          "Restablecer los ajustes"
),
    Entry("SETTINGS.RESET_CONFIRM",
          "Todas las preferencias volverán a su valor de fábrica.\n\nLas carpetas indexadas, las búsquedas guardadas y el historial NO se borran.\n\n¿Continuar?"
),
    Entry("SETTINGS.RESET_DONE",
          "Ajustes restablecidos"
),
    Entry("SETTINGS.RESET_APPLY",
          "Los ajustes restablecidos se verán al reiniciar la aplicación."
),
    Entry("SETTINGS.REPAIR",
          "Reparar"
),
    Entry("SETTINGS.REPAIRED",
          "{count} valor(es) estaban fuera de rango y se han corregido"
),
    Entry("SETTINGS.NOTHING_TO_REPAIR",
          "Ningún valor está fuera de rango"
),
    Entry("SETTINGS.EXPORT",
          "Exportar"
),
    Entry("SETTINGS.EXPORTED",
          "Ajustes exportados a {path}"
),
    Entry("SETTINGS.IMPORT",
          "Importar"
),
    Entry("SETTINGS.IMPORTED",
          "Importados {count} ajuste(s); {refused} rechazados"
),
    Entry("SETTINGS.IMPORT_FAILED",
          "No se ha podido importar: {reason}"
),
    Entry("SETTINGS.CLOSE",
          "Cerrar"
),
    Entry("SETTINGS.RESTART",
          "Requiere reiniciar la aplicación para aplicarse."
),
    Entry("SETTINGS.GROUP.SOURCES",
          "Qué se indexa"
),
    Entry("SETTINGS.GROUP.INDEXING",
          "Cuándo y cómo se indexa"
),
    Entry("SETTINGS.GROUP.SEARCH",
          "Cómo se busca"
),
    Entry("SETTINGS.GROUP.APPEARANCE",
          "Cómo se ve"
),
    Entry("SETTINGS.GROUP.PRIVACY",
          "Qué se recuerda"
),
    Entry("SETTINGS.GROUP.DIAGNOSTICS",
          "Diagnóstico y registro"
),
    # -- control centre ------------------------------------------------------
    Entry("CONTROL.TITLE", "Indexación y fuentes"),
    Entry("CONTROL.ADD_FOLDER", "Añadir carpeta a indexar"),
    Entry("CONTROL.ADD_SOURCE", "Añadir carpeta…"),
    Entry("CONTROL.REMOVE_SOURCE", "Quitar fuente"),
    Entry("CONTROL.RESCAN", "Reexplorar"),
    Entry("CONTROL.RETRY_FAILURES", "Reintentar fallos"),
    Entry("CONTROL.PAUSE", "Pausar"),
    Entry("CONTROL.RESUME", "Reanudar"),
    Entry("CONTROL.REFRESH", "Actualizar"),
    Entry("CONTROL.MAINTENANCE", "Mantenimiento:"),
    Entry("CONTROL.REBUILD_FTS", "Reconstruir FTS"),
    Entry("CONTROL.REBUILD_METADATA", "Reconstruir metadatos"),
    Entry("CONTROL.REBUILD_RELATIONS", "Reconstruir relaciones"),
    Entry("CONTROL.REBUILD_ALL", "Reconstruir todo"),
    Entry("CONTROL.SELFTEST", "Autodiagnóstico"),
    Entry("CONTROL.SUPPORT_BUNDLE", "Paquete de soporte…"),
    Entry("CONTROL.SAVE_SUPPORT", "Guardar paquete de soporte"),
    Entry("CONTROL.TECHNICAL_DETAILS", "Detalles técnicos"),
    Entry("CONTROL.ALREADY_CONFIGURED", "La carpeta ya está configurada"),
    Entry("CONTROL.SOURCE_ADDED", "Fuente añadida; {message}"),
    Entry("CONTROL.SOURCE_REMOVED",
          "Fuente quitada; la eliminación indexada se confirmó aunque el "
          "proveedor informó de un error"),
    Entry("CONTROL.SCAN_ALREADY_RUNNING",
          "Otra operación de indexación está en curso"),
    Entry("CONTROL.NO_FAILURES", "No hay fallos registrados para reintentar"),
    Entry("CONTROL.NEEDS_CONFIRMATION",
          "Se requiere confirmación explícita; esta operación no borra "
          "archivos físicos"),
    Entry("CONTROL.RECOVERY_DONE", "Se completó la recuperación del índice"),
    Entry("CONTROL.SUPPORT_WRITTEN",
          "Paquete de soporte en {path} (sin contenido, sin consultas, sin "
          "credenciales)"),
)

CATALOGUE: dict[str, str] = {entry.key: entry.value for entry in ENTRIES}


class MissingString(KeyError):
    """A catalogue key was asked for and does not exist."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key

    def __str__(self) -> str:  # KeyError would quote and repr the key
        return (
            f"no hay entrada {self.key!r} en el catálogo de la interfaz. "
            "Añádela a gui/strings.py: una cadena visible sin clave es una "
            "cadena que no se puede traducir ni auditar."
        )


def fields_of(key: str) -> tuple[str, ...]:
    """The placeholder names ``key`` needs."""
    if key not in CATALOGUE:
        raise MissingString(key)
    return tuple(
        field_name
        for _literal, field_name, _spec, _conversion in Formatter().parse(
            CATALOGUE[key]
        )
        if field_name
    )


def get(key: str, **fields: object) -> str:
    """The string for ``key``, with ``{placeholders}`` filled in.

    Raises :class:`MissingString` rather than returning an empty string: a
    blank label in a search window is exactly what the accessibility audit
    looks for, so a missing key must be loud, not invisible.

    Both a missing and an unknown field are errors. ``str.format`` silently
    ignores a keyword it was not asked for, so a caller that misspells
    ``count=`` would put a literal ``{count}`` on screen without anything
    complaining -- which is why the fields are checked here rather than left to
    ``format``.
    """
    if key not in CATALOGUE:
        raise MissingString(key)
    expected = fields_of(key)
    missing = [name for name in expected if name not in fields]
    unknown = [name for name in fields if name not in expected]
    if missing or unknown:
        problems = []
        if missing:
            problems.append("falta " + ", ".join(sorted(missing)))
        if unknown:
            problems.append("sobra " + ", ".join(sorted(unknown)))
        raise MissingString(f"{key} ({'; '.join(problems)})")
    return CATALOGUE[key].format(**fields) if fields else CATALOGUE[key]


def keys() -> tuple[str, ...]:
    return tuple(entry.key for entry in ENTRIES)


def values() -> tuple[str, ...]:
    return tuple(entry.value for entry in ENTRIES)


# -- the coverage check -------------------------------------------------------

# Keyword arguments that put a string in front of the user.
# Keyword arguments that put a string in front of the user. A call that
# receives any of these is displaying something, whatever it constructs.
VISIBLE_KWARGS = frozenset({"text", "label", "title", "heading"})

# Method names whose first positional argument is shown to the user.
VISIBLE_METHODS = frozenset({
    "_set_status", "add_command", "add_cascade", "add_checkbutton",
})


def _visible_literals(path: Path) -> set[str]:
    """Every literal in ``path`` that can reach the screen.

    The rule is about the *argument*, not about the constructor: any call that
    receives ``text=`` or ``label=`` is displaying a string, whether it is a
    ``ttk.Button`` or something else entirely. The first version compared the
    call's name against the keyword names, so it never looked at a single widget
    label and reported the interface as covered when it had checked nothing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for value in _visible_arguments(node):
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value.strip()
            ):
                found.add(value.value)
    return found


def _visible_arguments(node: ast.Call) -> list[ast.expr]:
    """The arguments of ``node`` that are shown to the user."""
    function = node.func
    name = (
        function.attr if isinstance(function, ast.Attribute)
        else getattr(function, "id", "")
    )
    keywords = {k.arg: k.value for k in node.keywords if k.arg}
    candidates = [
        keywords[key] for key in VISIBLE_KWARGS | {"message"} if key in keywords
    ]
    if name in VISIBLE_METHODS:
        if node.args:
            candidates.append(node.args[0])
        candidates.extend(
            keywords[key] for key in ("label", "text") if key in keywords
        )
    return candidates


def gui_modules(root: Path) -> list[Path]:
    return sorted((root / "src" / "universal_search" / "gui").glob("*.py"))


def untranslated_literals(root: Path | None = None) -> dict[str, list[str]]:
    """Visible literals written inline instead of taken from the catalogue.

    Returned as ``{literal: ["file.py:line", ...]}``. An empty dict is the
    state this phase wants: every string a user can see comes from
    :data:`CATALOGUE`, so the interface is translatable and auditable as one
    list.

    ``strings.py`` itself is excluded, and so are docstrings and module
    comments: a literal only counts when it is an argument to something that
    displays it.
    """
    base = root or Path(__file__).resolve().parents[3]
    known = set(CATALOGUE.values())
    # Filled in at runtime and not part of the catalogue: a format template
    # built from a ``get()`` result is not a missing translation.
    runtime: set[str] = set()
    for path in gui_modules(base):
        if path.name == "strings.py":
            continue
        for literal in _visible_literals(path):
            if literal in known:
                continue
            # A literal produced by concatenation or a placeholder belongs to
            # a template; only a finished, displayable string has to be
            # catalogued.
            if any(marker in literal for marker in ("{", "}")):
                continue
            runtime.add(literal)
    locations: dict[str, list[str]] = {}
    for path in gui_modules(base):
        if path.name == "strings.py":
            continue
        for literal in _visible_literals(path):
            if literal in runtime:
                locations.setdefault(literal, []).append(path.name)
    return locations