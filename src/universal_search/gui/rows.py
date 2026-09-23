"""Result presentation helpers (spec 017).

Pure functions over a :class:`SearchResult`, so what a row looks like can
be tested without a window: filenames, a useful path, the type and a
snippet, in one compact line that does not wrap into noise.
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


def clean_snippet(snippet: str | None, limit: int = SNIPPET_CHARS) -> str:
    """Snippet without FTS highlight markers, collapsed and truncated."""
    if not snippet:
        return ""
    plain = " ".join(snippet.replace("[", " ").replace("]", " ").split())
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1].rstrip() + "…"


def format_result_row(
    name: str,
    path: Path | str,
    snippet: str | None,
    source: str = "",
) -> str:
    """One listbox line: name · type · folder — snippet."""
    pieces = [name, extension_label(name)]
    folder = path_hint(path)
    if folder:
        pieces.append(folder)
    row = "  ·  ".join(pieces)
    if source and source != "local":
        # Only worth showing when it is not the default: a second column
        # of "(local)" on every row is visual noise.
        row = f"[{source}] {row}"
    text = clean_snippet(snippet)
    return f"{row}  —  {text}" if text else row
