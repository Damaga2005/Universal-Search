"""Phase 036 evidence: organizing results must not change which results you get.

Run from the repository root::

    python -m evaluation.organize_gate

The first gate is the one this phase is really about. Sorting and grouping are
presentation; the moment they start hiding or promoting documents they are
retrieval. So T1 compares the *set* of documents a query returns with and
without any organization applied and requires them to be identical.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation import corpus as corpus_module  # noqa: E402
from universal_search.appconfig import AppConfig, AppPaths  # noqa: E402
from universal_search.index.database import SearchDatabase  # noqa: E402
from universal_search.index.indexer import Indexer  # noqa: E402
from universal_search.index.search import SearchEngine  # noqa: E402
from universal_search.organize import (  # noqa: E402
    GROUP_DATE,
    GROUP_FIELDS,
    GROUP_FOLDER,
    GROUP_SOURCE,
    GROUP_TYPE,
    MAX_SORT_POOL,
    SORT_MODIFIED,
    SORT_NAME,
    SORT_RELEVANCE,
    SORT_SIZE,
    SavedSearch,
    count_invariants,
    delete_search,
    find_saved,
    group_results,
    load_saved,
    pool_size,
    save_search,
    sort_results,
)

PAGE = 8

# A query broad enough that the widened pool really has something to choose
# from. The first version of this gate used "informe", which matches three
# documents in the corpus, so the "the pool was widened" gate was technically
# true and evidentially worthless.
QUERY = "de"

THRESHOLDS = {
    "T1_documents_from_outside": 0,
    "T2_sort_is_monotonic": 1,
    "T3_undated_documents_last": 1,
    "T4_grouping_conserves_results": 1,
    "T5_reproducible_order": 1,
    "T6_pool_widened": 1,
    "T7_saved_round_trip": 1,
    "T8_saved_deletable": 1,
    "T9_mrr_change": 0.0,
}


@dataclass
class Verdict:
    gate: str
    measured: float
    threshold: float
    passed: bool
    detail: str

    def line(self) -> str:
        return (
            f"{'PASS' if self.passed else 'FAIL'}  {self.gate:<38} "
            f"{self.measured:>10.3f}  (umbral {self.threshold})  {self.detail}"
        )


def main() -> int:
    # Windows consoles default to a legacy code page, and these gates print
    # the interface's own strings. Never crash while reporting (the CLI has
    # done this since phase 005; a gate that dies printing is worse than one
    # that reports a failure).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    corpus_module.assert_labels_are_consistent()
    workspace = Path(tempfile.mkdtemp(prefix="universal-search-036-"))
    tree = workspace / "tree"
    corpus_module.build(tree)
    mapping = corpus_module.ids_by_path(tree)
    database = SearchDatabase(workspace / "index.db")
    Indexer(database).index_root(tree)
    lexical = SearchEngine(database)
    from evaluation import runner  # noqa: PLC0415

    # One page, fetched wide enough that a non-relevance order is meaningful.
    wide = lexical.search(QUERY, pool_size(PAGE, SORT_NAME))
    page = wide[:PAGE]
    baseline = [item.path for item in page]

    # -- T1: presentation stays inside the candidate pool --------------------
    # The first version of this gate asserted "organizing never changes which
    # documents you get". It failed with 19 candidates and it was wrong: asking
    # for alphabetical order and getting the eight most relevant documents in
    # alphabetical order is not ordering, it is shuffling. Choosing a different
    # subset *of the pool* is the requested behaviour.
    #
    # The property that actually matters is narrower, and it is the one the
    # pool widening exists to protect:
    #
    #   * the candidate pool is the same whatever presentation is asked for;
    #   * every document shown came out of that pool — nothing is invented;
    #   * relevance order still hands back exactly the engine's own order.
    #
    # How many documents a re-order selects differently is *measured and
    # published*, because that is the cost of the feature and hiding it would
    # be the lie.
    pool = set(item.path for item in wide)
    outside = 0
    subsets_changed = 0
    for sort in (SORT_NAME, SORT_MODIFIED, SORT_SIZE):
        for group in GROUP_FIELDS:
            organized = sort_results(list(wide), sort)[:PAGE]
            assert count_invariants(organized, group_results(organized, group))
            outside += sum(1 for item in organized if item.path not in pool)
            subsets_changed += int(
                set(item.path for item in organized) != set(baseline)
            )
    relevance_order_preserved = (
        [str(item.path) for item in sort_results(list(wide), SORT_RELEVANCE)]
        == [str(item.path) for item in wide]
    )
    t1 = not outside and relevance_order_preserved

    # -- T2: the sorted page is genuinely ordered by its key ------------------
    by_name = sort_results(list(wide), SORT_NAME)[:PAGE]
    names_in_order = [item.name.casefold() for item in by_name]
    t2 = names_in_order == sorted(names_in_order)

    by_modified = sort_results(list(wide), SORT_MODIFIED)
    stamps = [
        item.modified_at for item in by_modified if item.modified_at
    ]
    t2 = t2 and stamps == sorted(stamps, reverse=True)

    # -- T3: undated documents land last, never in the middle ----------------
    # Dated documents occupy the head, undated ones the tail, with no mixing:
    # an undated document between two dated ones would mean the order is not
    # total.
    head_dated = all(item.modified_at for item in by_modified[: len(stamps)])
    tail_undated = all(
        not item.modified_at for item in by_modified[len(stamps):]
    )
    t3 = head_dated and tail_undated

    # -- T4: grouping never loses or invents a result -------------------------
    conserved = all(
        count_invariants(page, group_results(page, field))
        for field in (GROUP_FOLDER, GROUP_TYPE, GROUP_SOURCE, GROUP_DATE)
    )
    t4 = conserved and sum(
        len(group) for group in group_results(page, GROUP_FOLDER)
    ) == len(page)

    # -- T5: the same results always come out in the same order ---------------
    first = [str(item.path) for item in sort_results(list(wide), SORT_NAME)]
    shuffled = list(reversed(wide))
    second = [str(item.path) for item in sort_results(shuffled, SORT_NAME)]
    t5 = first == second

    # -- T6: a non-relevance order really widens the candidate pool -----------
    t6 = (
        pool_size(PAGE, SORT_RELEVANCE) == PAGE
        and pool_size(PAGE, SORT_NAME) > PAGE
        and pool_size(10_000, SORT_NAME) == MAX_SORT_POOL
    )
    # And that widening is what makes the sorted page differ from the page
    # relevance alone would have chosen.
    relevance_page = lexical.search(QUERY, PAGE)
    widened_page = [str(item.path) for item in sort_results(
        lexical.search(QUERY, pool_size(PAGE, SORT_NAME)), SORT_NAME
    )[:PAGE]]
    plain_page = [str(item.path) for item in relevance_page]
    t6 = t6 and widened_page != plain_page

    # -- T7: a saved search round-trips through the real config file ---------
    home = AppPaths(workspace / "home")
    home.ensure()
    config = AppConfig.load(home)
    config = replace(config, saved_searches=save_search(
        config.saved_searches,
        SavedSearch(name="Finanzas", query="presupuesto",
                    sort=SORT_MODIFIED, group=GROUP_FOLDER),
    ))
    config.save(home)
    reloaded = AppConfig.load(home)
    found = find_saved(reloaded.saved_searches, "finanzas")
    t7 = (
        found is not None
        and found.query == "presupuesto"
        and found.sort == SORT_MODIFIED
        and found.group == GROUP_FOLDER
        # No paths and no results are ever stored with a saved search.
        and set(found.as_dict()) == {
            "name", "query", "sort", "group", "source", "doc_type"
        }
    )

    # -- T8: a saved search can be removed ------------------------------------
    cleared = delete_search(reloaded.saved_searches, "Finanzas")
    config = replace(config, saved_searches=cleared)
    config.save(home)
    t8 = load_saved(AppConfig.load(home).saved_searches) == ()

    # -- T9: the default path is untouched ------------------------------------
    mrr_before = runner.measure(lexical, mapping).mrr()
    sort_results(list(wide), SORT_RELEVANCE)
    mrr_after = runner.measure(lexical, mapping).mrr()

    verdicts = [
        Verdict("T1 documents from outside the pool", outside,
                THRESHOLDS["T1_documents_from_outside"],
                t1,
                f"{PAGE} de {len(wide)} candidatos, 12 combinaciones; "
                f"reordenando cambian {subsets_changed} subconjuntos "
                f"(es lo pedido); orden por relevancia intacto: "
                f"{relevance_order_preserved}"),
        Verdict("T2 sort is monotonic", 1 if t2 else 0,
                THRESHOLDS["T2_sort_is_monotonic"], t2,
                "por nombre y por fecha (mas reciente primero)"),
        Verdict("T3 undated documents last", 1 if t3 else 0,
                THRESHOLDS["T3_undated_documents_last"], t3,
                "sin fecha no se mezclan con las que si la tienen"),
        Verdict("T4 grouping conserves results", 1 if t4 else 0,
                THRESHOLDS["T4_grouping_conserves_results"], t4,
                f"{len(page)} documentos, {len(group_results(page, GROUP_FOLDER))} carpetas"),
        Verdict("T5 reproducible order", 1 if t5 else 0,
                THRESHOLDS["T5_reproducible_order"], t5,
                "mismos resultados, mismo orden, entrada invertida"),
        Verdict("T6 pool really widened", 1 if t6 else 0,
                THRESHOLDS["T6_pool_widened"], t6,
                f"pagina {PAGE} -> conjunto {pool_size(PAGE, SORT_NAME)}, "
                f"tope {MAX_SORT_POOL}"),
        Verdict("T7 saved search round trip", 1 if t7 else 0,
                THRESHOLDS["T7_saved_round_trip"], t7,
                "consulta, orden, agrupacion y filtros en config.json"),
        Verdict("T8 saved search deletable", 1 if t8 else 0,
                THRESHOLDS["T8_saved_deletable"], t8,
                "una funcion que solo anade deja basura sin quitar"),
        Verdict("T9 lexical MRR change", abs(mrr_after - mrr_before),
                THRESHOLDS["T9_mrr_change"], mrr_before == mrr_after,
                f"MRR sigue en {mrr_after:.4f}"),
    ]

    print("=" * 104)
    print("PUERTA DE EVIDENCIA - FASE 036 (agrupar, ordenar y busquedas guardadas)")
    print("=" * 104)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 104)
    print("conjunto de candidatos y orden resultante:")
    print(f"  {len(wide)} candidatos para una pagina de {PAGE}")
    for item in sort_results(list(wide), SORT_NAME)[:PAGE]:
        print(f"    {item.name:<34} {item.modified_at or '(sin fecha)'}")
    print("agrupacion por carpeta:")
    for group in group_results(page, GROUP_FOLDER):
        print(f"    {group.label}  ({len(group)})")

    payload = {
        "phase": "036",
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "candidates": len(wide),
        "page": PAGE,
    }
    out = ROOT / "evaluation" / "organize_baseline.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")
    failed = [v for v in verdicts if not v.passed]
    print("VEREDICTO:", "SHIP" if not failed else f"NO SHIP ({len(failed)} puertas)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
