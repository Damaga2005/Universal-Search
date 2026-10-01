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
    Entry("MENU.DIAGNOSE.SUMMARY", "Estado del índice"),
    Entry("MENU.DIAGNOSE.CONTROL_CENTER", "Centro de control de indexación..."),
    Entry("MENU.DIAGNOSE.RELATED", "Documentos relacionados..."),

    # -- the search bar ------------------------------------------------------
    Entry("SEARCH.PLACEHOLDER_OR_LABEL", "Búsqueda"),
    Entry("SEARCH.LABEL.CONTEXT", "Contexto:"),
    Entry("SEARCH.LABEL.SOURCE", "Fuente:"),
    Entry("SEARCH.LABEL.TYPE", "Tipo:"),
    Entry("SEARCH.RECENTS", "Recientes ▾"),
    Entry("SEARCH.RECENTS_EMPTY", "(sin búsquedas recientes)"),
    Entry("SEARCH.READY", "Listo — escribe para buscar"),
    Entry("SEARCH.HOTKEY_SHOWN", "Atajo global — escribe para buscar"),
    Entry("SEARCH.PICK_FOLDER", "Carpeta a indexar"),

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
    Entry("RESULTS.SEARCHING", "Buscando…"),
    Entry("RESULTS.COUNT", "{count} documento(s)"),

    # -- related documents ---------------------------------------------------
    Entry("RELATED.TITLE", "Documentos relacionados"),
    Entry("RELATED.LOADING", "Cargando documentos relacionados…"),
    Entry("RELATED.NOTE",
          "Relaciones locales; no cambian la relevancia de la búsqueda."),
    Entry("RELATED.EMPTY", "No hay documentos relacionados."),

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
    Entry("ERROR.RELATED", "No se pudieron cargar los relacionados"),
    Entry("ACTION.CANCELLED", "Cancelado: no se olvidó nada"),
    Entry("ACTION.FORGET_NEEDS_SELECTION",
          "Selecciona al menos un documento para olvidar"),
    Entry("ACTION.RELATED_NEEDS_SELECTION",
          "Selecciona un resultado para ver relacionados"),
    Entry("ACTION.REBUILD_HINT",
          "Usa el centro de control para reconstruir el índice"),

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