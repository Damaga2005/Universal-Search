"""Result presentation helpers (spec 017).

Pure functions over a :class:`SearchResult`, so what a row looks like can be
tested without a window: filenames, a useful path, the type, the source and
the snippet.

Phase 041 removed ``format_result_row``. It formatted all five of those into
one flat string for a ``Listbox`` that then clipped it at the widget edge, and
once the results pane became a ``Treeview`` with real columns nothing called
it. A formatter with no caller is not a formatter, it is a second place to
change the next time someone decides a row should look different — which is
exactly what had happened with ``TYPE_LABELS``.
"""

from pathlib import Path

from universal_search.gui.theme import PATH_PARTS, SNIPPET_CHARS


def path_hint(path: Path | str, parts: int = PATH_PARTS) -> str:
    """The last ``parts`` directories of a path, without the file name.

    Two components are enough to tell "electrónica/circuitos" from
    "personal/recetas"; the full absolute path is noise in a result list.
    Drive letters and UNC roots are dropped: they are the same for every
    row and carry no information.
    """
    parent = Path(path).parent
    components = list(parent.parts)
    if parent.anchor and components and components[0] == parent.anchor:
        components = components[1:]  # drop "C:\" or "\\server\share"
    return "/".join(components[-parts:]) if components else ""


def extension_label(name: str) -> str:
    """Uppercase extension without the dot, or an em dash when there is none."""
    suffix = Path(name).suffix
    return suffix[1:].upper() if suffix else "—"

# Human names for the types this product actually opens. Phase 041 found two
# answers to "what kind of file is this" living side by side: this extension
# and a `TYPE_LABELS` table in the window, which said DOCX was "Word" and PDF
# was "PDF". Two tables for one question means the row and the detail pane can
# disagree, and here they did. One table, here, and the window asks.
TYPE_NAMES = {
    ".pdf": "PDF",
    ".docx": "Word",
    ".doc": "Word",
    ".xlsx": "Excel",
    ".xls": "Excel",
    ".pptx": "PowerPoint",
    ".ppt": "PowerPoint",
    ".md": "Markdown",
    ".txt": "Texto",
    ".rtf": "RTF",
    ".zip": "ZIP",
}


def type_label(path: Path | str) -> str:
    """The document type as a person would name it, not as a file extension.

    Falls back to the uppercase extension so an unknown type is still
    informative rather than blank.
    """
    return TYPE_NAMES.get(Path(path).suffix.lower(), extension_label(str(path)))


def clean_snippet(snippet: str | None, limit: int = SNIPPET_CHARS) -> str:
    """Snippet without FTS highlight markers, collapsed and truncated."""
    if not snippet:
        return ""
    plain = " ".join(snippet.replace("[", " ").replace("]", " ").split())
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1].rstrip() + "…"


def result_cells(
    name: str,
    path: Path | str,
    snippet: str | None,
    source: str = "",
) -> dict[str, str]:
    """One result as the values of a row in the results pane.

    Phase 041 moved the results pane from a ``Listbox`` to a ``Treeview`` with
    real columns, which is what finally gives the five things a search result
    *is* — name, folder, type, source and why it matched — somewhere to sit
    separately instead of being flattened into one line the widget then clips.

    ``folder`` and ``source`` may be empty: the pane hides a column that has
    nothing to say, so a local file does not spend a column saying "local".
    """
    cells = {
        "name": name,
        "folder": path_hint(path),
        "kind": type_label(path),
        "source": source if source and source != "local" else "",
        "snippet": clean_snippet(snippet),
    }
    return cells
