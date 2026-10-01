"""Phase 036: grouping, sorting and saved searches.

The property that matters is that organizing never changes *which* documents
you get. Everything here is about keeping presentation separate from retrieval,
and about saved searches that can be removed as well as added.
"""

from __future__ import annotations

import pathlib

import pytest

from universal_search.index.search import SearchResult
from universal_search.organize import (
    GROUP_DATE,
    GROUP_FOLDER,
    GROUP_NONE,
    GROUP_SOURCE,
    GROUP_TYPE,
    MAX_SORT_POOL,
    SORT_MODIFIED,
    SORT_NAME,
    SORT_POOL_MULTIPLIER,
    SORT_RELEVANCE,
    SORT_SIZE,
    Group,
    SavedSearch,
    count_invariants,
    delete_search,
    find_saved,
    group_key,
    group_results,
    load_saved,
    names,
    pool_size,
    save_search,
    saved_from_dict,
    sort_results,
)


def result(
    name: str,
    *,
    path: str | None = None,
    source: str = "local",
    modified_at: str | None = "2026-09-14 10:00:00",
    score: float = 1.0,
    size: int = 0,
) -> SearchResult:
    return SearchResult(
        path=__import__("pathlib").Path(path or f"docs/{name}"),
        name=name,
        source=source,
        snippet=None,
        rank=1,
        score=score,
        modified_at=modified_at,
        size=size,
    )


# -- sorting: the same results, in a declared order ---------------------------

def test_relevance_sort_does_not_touch_the_engine_order():
    ordered = [result("c", score=0.1), result("a", score=0.9)]
    assert sort_results(ordered, SORT_RELEVANCE) == ordered


def test_name_sort_is_case_insensitive():
    ordered = [result("Zeta.md"), result("alfa.md"), result("Media.md")]
    assert [item.name for item in sort_results(ordered, SORT_NAME)] == [
        "alfa.md", "Media.md", "Zeta.md",
    ]


def test_ties_never_depend_on_input_order():
    # Same score, same casefolded name, different paths: the order must come
    # from the path, or two identical queries would list results differently.
    first = [result("nota.md", path="b/nota.md"), result("nota.md", path="a/nota.md")]
    second = list(reversed(first))
    names_out = [str(item.path) for item in sort_results(first, SORT_NAME)]
    assert names_out == [str(item.path) for item in sort_results(second, SORT_NAME)]


def test_size_sort_is_descending():
    ordered = [
        result("a.md", size=10),
        result("b.md", size=300),
        result("c.md", size=100),
    ]
    assert [item.name for item in sort_results(ordered, SORT_SIZE)] == [
        "b.md", "c.md", "a.md",
    ]


def test_modified_sort_puts_undated_documents_last():
    ordered = [
        result("nuevo.md", modified_at="2026-09-20 08:00:00"),
        result("sin-fecha.md", modified_at=None),
        result("viejo.md", modified_at="2020-01-01 00:00:00"),
    ]
    assert [item.name for item in sort_results(ordered, SORT_MODIFIED)] == [
        "nuevo.md", "viejo.md", "sin-fecha.md",
    ]


def test_an_unknown_sort_is_rejected_rather_than_ignored():
    with pytest.raises(ValueError):
        sort_results([result("a.md")], "por-magia")


# -- sorting must not change which documents you get -------------------------

def test_relevance_uses_exactly_the_requested_limit():
    assert pool_size(20, SORT_RELEVANCE) == 20


def test_another_sort_widens_the_pool():
    # Otherwise "--sort name --limit 20" would mean "the 20 most relevant
    # documents, alphabetically", which is not sorting by name.
    assert pool_size(20, SORT_NAME) == 20 * SORT_POOL_MULTIPLIER


def test_the_widened_pool_is_capped():
    assert pool_size(10_000, SORT_NAME) == MAX_SORT_POOL


def test_the_pool_is_never_smaller_than_the_page():
    for limit in (0, 1, 5, 50):
        for sort in (SORT_RELEVANCE, SORT_NAME, SORT_SIZE):
            assert pool_size(limit, sort) >= max(1, limit)


# -- grouping: nothing lost, nothing invented ---------------------------------

def test_grouping_by_folder_uses_the_parent_directory():
    ordered = [
        result("a.md", path="docs/2026/a.md"),
        result("b.md", path="docs/2025/b.md"),
        result("c.md", path="docs/2026/c.md"),
    ]
    groups = group_results(ordered, GROUP_FOLDER)
    # The bucket shows the folder the way the user sees it, which on Windows
    # means the platform separator rather than a POSIX one.
    assert [group.key for group in groups] == [
        str(pathlib.Path("docs/2025")), str(pathlib.Path("docs/2026")),
    ]
    assert len(groups[1]) == 2
    assert count_invariants(ordered, groups)


def test_group_order_does_not_depend_on_size():
    # A grouping that reorders itself when one document changes is a grouping
    # nobody can build a habit out of.
    small = [result("a.md", path="x/a.md")]
    big = small + [result(f"f{index}.md", path="x/f{index}.md") for index in range(9)]
    assert [group.key for group in group_results(small, GROUP_FOLDER)] == [
        group.key for group in group_results(big, GROUP_FOLDER)
    ]


def test_grouping_by_type_uses_the_extension():
    ordered = [
        result("a.md"),
        result("b.md"),
        result("c.pdf"),
        result("sin"),
    ]
    groups = {group.key: len(group) for group in group_results(ordered, GROUP_TYPE)}
    assert groups == {"md": 2, "pdf": 1, "(sin extension)": 1}


def test_grouping_by_source_keeps_every_source_visible():
    ordered = [
        result("a.md", source="local"),
        result("b.md", source="onedrive"),
        result("c.md", source="network"),
    ]
    groups = group_results(ordered, GROUP_SOURCE)
    assert [group.key for group in groups] == ["local", "network", "onedrive"]
    assert all(len(group) == 1 for group in groups)


def test_grouping_by_date_buckets_by_month():
    ordered = [
        result("a.md", modified_at="2026-09-14 10:00:00"),
        result("b.md", modified_at="2026-09-30 10:00:00"),
        result("c.md", modified_at="2025-12-31 10:00:00"),
    ]
    groups = group_results(ordered, GROUP_DATE)
    assert [group.key for group in groups] == ["2025-12", "2026-09"]
    assert len(groups[1]) == 2


def test_undated_documents_get_their_own_bucket_not_a_wrong_date():
    ordered = [result("a.md", modified_at=None), result("b.md")]
    groups = group_results(ordered, GROUP_DATE)
    keys = {group.key: len(group) for group in groups}
    assert keys["(sin fecha)"] == 1
    assert count_invariants(ordered, groups)


def test_an_unparseable_date_is_not_filed_under_nonsense():
    ordered = [result("a.md", modified_at="no-es-una-fecha")]
    groups = group_results(ordered, GROUP_DATE)
    assert groups[0].key == "(sin fecha)"


def test_no_grouping_returns_one_unnamed_group():
    ordered = [result("a.md"), result("b.md")]
    groups = group_results(ordered, GROUP_NONE)
    assert len(groups) == 1
    assert groups[0].key == ""
    assert groups[0].label == "(sin grupo)"


def test_no_results_means_no_groups():
    assert group_results([], GROUP_FOLDER) == ()


def test_grouping_never_loses_or_invents_a_result():
    for field in (GROUP_FOLDER, GROUP_TYPE, GROUP_SOURCE, GROUP_DATE):
        ordered = [
            result("a.md", path="p/a.md", source="local"),
            result("b.pdf", path="q/b.pdf", source="onedrive",
                   modified_at=None),
            result("c", path="c", modified_at="2020-05-05 00:00:00"),
        ]
        assert count_invariants(ordered, group_results(ordered, field)), field


def test_a_group_label_is_readable():
    assert Group(key="", results=()).label == "(sin grupo)"
    assert Group(key="docs/2026", results=()).label == "docs/2026"


def test_group_key_is_empty_when_not_grouping():
    assert group_key(result("a.md"), GROUP_NONE) == ""


# -- saved searches: they must be removable -----------------------------------

def test_a_saved_search_keeps_the_query_and_the_presentation():
    saved = SavedSearch(
        name="Electronica", query="  transistor  ",
        sort=GROUP_FOLDER, group=GROUP_FOLDER, doc_type=".PDF",
    ).normalized()
    assert saved.query == "transistor"
    assert saved.doc_type == "pdf"
    assert saved.name == "Electronica"


def test_an_unknown_sort_or_group_falls_back_instead_of_being_stored():
    saved = SavedSearch(name="x", query="q", sort="magia", group="magia")
    assert saved.normalized().sort == SORT_RELEVANCE
    assert saved.normalized().group == GROUP_NONE


def test_saving_and_reading_a_search_round_trips():
    entries = save_search((), SavedSearch(name="notas", query="presupuesto"))
    assert names(entries) == ("notas",)
    assert find_saved(entries, "notas").query == "presupuesto"


def test_saving_the_same_name_replaces_instead_of_duplicating():
    entries = save_search((), SavedSearch(name="notas", query="uno"))
    entries = save_search(entries, SavedSearch(name="notas", query="dos"))
    assert names(entries) == ("notas",)
    assert find_saved(entries, "notas").query == "dos"


def test_replacing_keeps_the_original_position():
    entries = save_search((), SavedSearch(name="a", query="1"))
    entries = save_search(entries, SavedSearch(name="b", query="2"))
    entries = save_search(entries, SavedSearch(name="a", query="3"))
    assert names(entries) == ("a", "b")


def test_a_search_can_be_deleted():
    entries = save_search((), SavedSearch(name="notas", query="q"))
    entries = delete_search(entries, "notas")
    assert names(entries) == ()
    # Deleting something that is not there is not an error.
    assert delete_search(entries, "notas") == ()


def test_deleting_is_case_insensitive():
    entries = save_search((), SavedSearch(name="Notas", query="q"))
    assert names(delete_search(entries, "notas")) == ()


def test_a_search_without_a_name_is_never_stored():
    assert save_search((), SavedSearch(name="   ", query="q")) == ()


def test_unusable_entries_are_ignored_not_fatal():
    entries = save_search((), SavedSearch(name="buena", query="q"))
    broken = entries + ({"query": "sin nombre"}, "no-es-un-diccionario", 42)
    assert names(broken) == ("buena",)
    assert saved_from_dict(None) is None
    assert saved_from_dict({"name": "  "}) is None


def test_a_duplicate_name_in_the_file_keeps_the_last_one():
    entries = save_search((), SavedSearch(name="a", query="primera"))
    entries = entries + ({"name": "a", "query": "segunda"},)
    assert [saved.query for saved in load_saved(entries)] == ["segunda"]
    assert len(load_saved(entries)) == 1


def test_a_saved_search_holds_no_paths():
    payload = SavedSearch(name="n", query="q").as_dict()
    assert set(payload) == {
        "name", "query", "sort", "group", "source", "doc_type"
    }
    assert not any("path" in key for key in payload)


def test_a_saved_search_is_immutable():
    saved = SavedSearch(name="n", query="q")
    with pytest.raises(Exception):
        saved.query = "otra"  # type: ignore[misc]
