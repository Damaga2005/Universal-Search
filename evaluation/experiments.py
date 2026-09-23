"""How much headroom does each weight have? (spec 013)

Precision@K, Recall@K and MRR saturate on a corpus this small: the
default weighting already places a relevant document first for every
labelled query, so the metrics alone cannot tell a safe change from a
harmful one. The useful question is not "is the score good?" but "**how
far can this weight move before the measured ranking changes?**".

For a given weight and query this module bisects over the weight value
and reports the first value at which the top of the ranking differs from
the default. A boundary far from the current setting is evidence that the
setting is not a house of cards; a boundary next to it means the number
is balanced on a knife edge and must not be touched casually.

    python -m evaluation --flip path_match notas
    python -m evaluation --flip recency diagrama
"""

import dataclasses
import shutil
import tempfile
from pathlib import Path

from evaluation import corpus as corpus_module
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.ranking import DEFAULT_WEIGHTS, Ranker, RankingWeights
from universal_search.index.search import SearchEngine

DEFAULT_TOP = 3
MAX_STEPS = 30
DEFAULT_HIGH = 8.0


def _top_ids(
    database: SearchDatabase,
    ids: dict[Path, str],
    query: str,
    weights: RankingWeights,
    top: int,
) -> tuple[str, ...]:
    engine = SearchEngine(database, ranker=Ranker(weights))
    return tuple(
        ids.get(Path(result.path).resolve(), str(result.path))
        for result in engine.search(query, limit=top)
    )


def flip_point(
    tree: Path,
    database: SearchDatabase,
    weight: str,
    query: str,
    *,
    top: int = DEFAULT_TOP,
    high: float = DEFAULT_HIGH,
) -> dict[str, object]:
    """How far can ``weight`` move before ``query``'s ranking changes?

    Two questions, two answers:

    * **is the signal load-bearing?** — does setting it to zero change the
      ranking? A signal that changes nothing at 0 and nothing at 8 is a
      decoration, not a signal.
    * **how much headroom is there?** — the smallest value above the
      current one at which the ranking changes, and how many times bigger
      that is than the current value.

    ``flip_up_at`` of ``None`` means no value in ``[current, high]`` moves
    the ranking.
    """
    ids = corpus_module.ids_by_path(tree)
    default_value = getattr(DEFAULT_WEIGHTS, weight)
    default_top = _top_ids(database, ids, query, DEFAULT_WEIGHTS, top)

    zero_top = _top_ids(
        database, ids, query,
        dataclasses.replace(DEFAULT_WEIGHTS, **{weight: 0.0}), top,
    )

    def top_at(value: float) -> tuple[str, ...]:
        return _top_ids(
            database, ids, query,
            dataclasses.replace(DEFAULT_WEIGHTS, **{weight: value}), top,
        )

    low, boundary, boundary_top = default_value, None, None
    for _ in range(MAX_STEPS):
        middle = (low + high) / 2.0
        if top_at(middle) == default_top:
            low = middle
        else:
            boundary = middle
            boundary_top = top_at(middle)
            high = middle
    return {
        "weight": weight,
        "query": query,
        "default_value": default_value,
        "default_top": list(default_top),
        "load_bearing": list(zero_top) != list(default_top),
        "top_at_zero": list(zero_top),
        "flip_up_at": boundary,
        "top_at_flip": list(boundary_top) if boundary_top else None,
        "headroom_ratio": (
            boundary / default_value
            if boundary is not None and default_value
            else None
        ),
    }


def run(weight: str, query: str, *, top: int = DEFAULT_TOP) -> dict[str, object]:
    """Index the corpus once, then bisect the requested weight."""
    temp = Path(tempfile.mkdtemp(prefix="flip013-"))
    tree = temp / "tree"
    try:
        corpus_module.build(tree)
        database = SearchDatabase(temp / "index.db")
        Indexer(database).index_root(tree)
        return flip_point(tree, database, weight, query, top=top)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
