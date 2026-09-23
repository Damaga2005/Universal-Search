"""Local document intelligence (spec 014).

Deterministic, local, rebuildable analysis of indexed documents: language,
title and headings, section count, bounded keyword/term vectors and
lightweight co-occurrence. No model, no network, no telemetry.

    from universal_search.intelligence import analyze, rebuild, related

    analysis = analyze(text, name="informe.md")
    stats = rebuild(database)             # derived data, versioned
    neighbours = related(database, "informe.md", limit=5)

The derived data is disposable: search works without it, and deleting it
never touches the index.
"""

from universal_search.intelligence.analysis import (
    INTELLIGENCE_VERSION,
    DocumentAnalysis,
    analyze,
    summarize,
)
from universal_search.intelligence.store import (
    RebuildStats,
    RelatedDocument,
    analysis_for,
    clear,
    rebuild,
    related,
    row_to_analysis,
    term_vocabulary,
)

__all__ = [
    "INTELLIGENCE_VERSION",
    "DocumentAnalysis",
    "RebuildStats",
    "RelatedDocument",
    "analysis_for",
    "analyze",
    "clear",
    "rebuild",
    "related",
    "row_to_analysis",
    "summarize",
    "term_vocabulary",
]
