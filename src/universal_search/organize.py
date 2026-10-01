"""Grouping, sorting and saved searches (phase 036).

The rule that shaped every decision here: **organizing the results must not
change which results you get.** Sorting and grouping are presentation, and the
moment they start hiding or promoting documents they stop being presentation
and start being retrieval. Two consequences:

* **A non-relevance sort widens the candidate pool.** ``--sort name`` over 20
  results cannot mean "the 20 alphabetically first documents that match",
  because relevance has already chosen which 20 those are. So the pool is
  widened by :data:`SORT_POOL_MULTIPLIER` and capped, and the *exact* number of
  candidates examined is reported. Without this, ``--sort modified`` would
  silently return the 20 most relevant documents in date order, which looks like
  sorting by date and is not.
* **Ties never depend on input order.** Every sort ends with the path as the
  final tiebreak, so the same results always come out in the same order.

Saved searches are plain local configuration — no new table, no database row,
nothing to migrate — and they carry a delete command, because a feature that can
only add leaves the user with clutter they cannot remove.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from universal_search.index.search import SearchResult

# -- vocabulary ---------------------------------------------------------------

SORT_RELEVANCE = "relevance"
SORT_NAME = "name"
SORT_MODIFIED = "modified"
SORT_SIZE = "size"
SORT_FIELDS: tuple[str, ...] = (SORT_RELEVANCE, SORT_NAME, SORT_MODIFIED, SORT_SIZE)

GROUP_NONE = "none"
GROUP_FOLDER = "folder"
GROUP_TYPE = "type"
GROUP_SOURCE = "source"
GROUP_DATE = "date"
GROUP_FIELDS: tuple[str, ...] = (
    GROUP_NONE, GROUP_FOLDER, GROUP_TYPE, GROUP_SOURCE, GROUP_DATE
)

# How much wider the candidate pool is when the user asked for an order that
# is not relevance. Without it, "--sort name --limit 20" would mean "the 20
# most relevant documents, alphabetically", which is not sorting by name.
SORT_POOL_MULTIPLIER = 5

# An upper bound on that widening: a pool of a million is not a sort, it is a
# full scan, and the user asked for a page of results.
MAX_SORT_POOL = 500


def pool_size(limit: int, sort: str) -> int:
    """How many candidates to fetch for a given request."""
    if sort == SORT_RELEVANCE:
        return max(1, int(limit))
    return min(MAX_SORT_POOL, max(1, int(limit)) * SORT_POOL_MULTIPLIER)


# -- sorting ------------------------------------------------------------------


def _name_key(result: SearchResult) -> tuple[str, str]:
    # Casefolded first so "informe" and "Informe" sort together, then the raw
    # name so the order is total and never depends on the input.
    return (result.name.casefold(), result.name)


def _timestamp(value) -> float:
    """Sortable instant for a stored date, or ``-inf`` when there is none.

    ``-inf`` rather than a sentinel string: the order has to be numeric to be
    expressible descending, and an unparseable date must not be able to raise
    while the user is sorting a list.
    """
    if not value:
        return float("-inf")
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return float("-inf")


def sort_results(
    results: list[SearchResult], sort: str = SORT_RELEVANCE
) -> list[SearchResult]:
    """Order results by ``sort``, with a total order that is reproducible."""
    if sort == SORT_RELEVANCE:
        # Relevance keeps its own order untouched: the engine already decided
        # it, and re-deriving it here would be a second, divergent definition.
        return list(results)
    if sort == SORT_NAME:
        keyed = lambda item: _name_key(item) + (str(item.path),)  # noqa: E731
    elif sort == SORT_SIZE:
        keyed = lambda item: (-_size_of(item), _name_key(item))  # noqa: E731
    elif sort == SORT_MODIFIED:
        # Newest first, which is what "by date modified" means to a person
        # looking for a file they just touched. Undated documents land last.
        keyed = lambda item: (  # noqa: E731
            -_timestamp(item.modified_at), _name_key(item)
        )
    else:
        raise ValueError(f"unknown sort field {sort!r}")
    return sorted(results, key=keyed)


def _size_of(result: SearchResult) -> int:
    return int(getattr(result, "size", 0) or 0)


# -- grouping -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Group:
    """One bucket of results, with the label shown to the user."""

    key: str
    results: tuple[SearchResult, ...]

    @property
    def label(self) -> str:
        return self.key or "(sin grupo)"

    def __len__(self) -> int:
        return len(self.results)


def group_key(result: SearchResult, group: str) -> str:
    """The bucket a result belongs to, or ``""`` when not grouping."""
    if group == GROUP_FOLDER:
        parent = Path(result.path).parent
        return "" if str(parent) in (".", "") else str(parent)
    if group == GROUP_TYPE:
        suffix = Path(result.path).suffix.lower()
        return suffix.lstrip(".") or "(sin extension)"
    if group == GROUP_SOURCE:
        return result.source or "(sin fuente)"
    if group == GROUP_DATE:
        return _date_bucket(getattr(result, "modified_at", None))
    return ""


def _date_bucket(value) -> str:
    """Year-month for a dated document, or a stable marker for undated ones.

    Documents with no date are not filed under 9999 or dropped: they get their
    own named bucket so the count of results is conserved.
    """
    if not value:
        return "(sin fecha)"
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return "(sin fecha)"
    return f"{moment.year:04d}-{moment.month:02d}"


def group_results(
    results: list[SearchResult], group: str = GROUP_NONE
) -> tuple[Group, ...]:
    """Bucket results by ``group``, in a deterministic key order.

    Groups are ordered by key, not by size: a grouping that reorders itself
    when one document changes is a grouping the user cannot build a habit out
    of. Empty groups are never invented.
    """
    if group == GROUP_NONE:
        return (Group(key="", results=tuple(results)),) if results else ()
    buckets: dict[str, list[SearchResult]] = {}
    for result in results:
        buckets.setdefault(group_key(result, group), []).append(result)
    return tuple(
        Group(key=key, results=tuple(buckets[key])) for key in sorted(buckets)
    )


def count_invariants(
    results: list[SearchResult], groups: tuple[Group, ...]
) -> bool:
    """True when grouping neither lost nor invented a result."""
    grouped = [item for group in groups for item in group.results]
    return len(grouped) == len(results) and all(
        any(item is original for original in results) for item in grouped
    )


# -- saved searches -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SavedSearch:
    """A named query with the way it should be presented.

    Holds no results and no paths: only what the user typed and how they wanted
    it organized, which is what makes it safe to keep in a plain config file.
    """

    name: str
    query: str
    sort: str = SORT_RELEVANCE
    group: str = GROUP_NONE
    source: str = ""
    doc_type: str = ""

    def normalized(self) -> "SavedSearch":
        return replace(
            self,
            name=str(self.name).strip(),
            query=str(self.query).strip(),
            sort=self.sort if self.sort in SORT_FIELDS else SORT_RELEVANCE,
            group=self.group if self.group in GROUP_FIELDS else GROUP_NONE,
            source=str(self.source).strip().casefold(),
            doc_type=str(self.doc_type).strip().casefold().lstrip("."),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "query": self.query,
            "sort": self.sort,
            "group": self.group,
            "source": self.source,
            "doc_type": self.doc_type,
        }


def saved_from_dict(raw: object) -> SavedSearch | None:
    """Normalize a raw config entry; ``None`` when unusable (e.g. no name)."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    return SavedSearch(
        name=name,
        query=str(raw.get("query") or "").strip(),
        sort=str(raw.get("sort") or SORT_RELEVANCE),
        group=str(raw.get("group") or GROUP_NONE),
        source=str(raw.get("source") or ""),
        doc_type=str(raw.get("doc_type") or ""),
    ).normalized()


def load_saved(entries) -> tuple[SavedSearch, ...]:
    """Every usable saved search, later duplicates winning by name."""
    found: dict[str, SavedSearch] = {}
    for raw in entries or ():
        saved = saved_from_dict(raw)
        if saved is not None:
            found[saved.name.casefold()] = saved
    return tuple(found.values())


def save_search(entries, search: SavedSearch) -> tuple[dict, ...]:
    """Add or replace a saved search by name, keeping the existing order."""
    cleaned = search.normalized()
    if not cleaned.name:
        return tuple(dict(item) for item in entries or ())
    replaced = False
    updated: list[dict] = []
    for raw in entries or ():
        existing = saved_from_dict(raw)
        if existing is not None and existing.name.casefold() == cleaned.name.casefold():
            if replaced:
                continue  # drop a duplicate that was shadowed
            replaced = True
            updated.append(cleaned.as_dict())
            continue
        updated.append(dict(raw))
    if not replaced:
        updated.append(cleaned.as_dict())
    return tuple(updated)


def delete_search(entries, name: str) -> tuple[dict, ...]:
    """Remove every saved search with this name, however it was written."""
    wanted = str(name).strip().casefold()
    kept: list[dict] = []
    for raw in entries or ():
        existing = saved_from_dict(raw)
        if existing is not None and existing.name.casefold() == wanted:
            continue
        kept.append(dict(raw))
    return tuple(kept)


def find_saved(entries, name: str) -> SavedSearch | None:
    wanted = str(name).strip().casefold()
    for raw in entries or ():
        existing = saved_from_dict(raw)
        if existing is not None and existing.name.casefold() == wanted:
            return existing
    return None


def names(entries) -> tuple[str, ...]:
    return tuple(saved.name for saved in load_saved(entries))


__all__ = [
    "GROUP_DATE",
    "GROUP_FIELDS",
    "GROUP_FOLDER",
    "GROUP_NONE",
    "GROUP_SOURCE",
    "GROUP_TYPE",
    "MAX_SORT_POOL",
    "SORT_FIELDS",
    "SORT_MODIFIED",
    "SORT_NAME",
    "SORT_POOL_MULTIPLIER",
    "SORT_RELEVANCE",
    "SORT_SIZE",
    "Group",
    "SavedSearch",
    "count_invariants",
    "delete_search",
    "find_saved",
    "group_key",
    "group_results",
    "load_saved",
    "names",
    "pool_size",
    "save_search",
    "sort_results",
]
