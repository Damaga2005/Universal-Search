"""Local document intelligence (spec 014).

Deterministic, local, rebuildable analysis of indexed documents: language,
title and headings, section count, bounded keyword/term vectors and
lightweight co-occurrence. No model, no network, no telemetry.

    from universal_search.intelligence import GraphStore, analyze, rebuild, related

    analysis = analyze(text, name="informe.md")
    stats = rebuild(database)             # derived data, versioned
    neighbours = related(database, "informe.md", limit=5)
    graph = GraphStore(database)          # optional, also rebuildable

The derived data is disposable: search works without it, and deleting it
never touches the index.
"""

from universal_search.intelligence.analysis import (
    INTELLIGENCE_VERSION,
    DocumentAnalysis,
    DocumentRecord,
    analyze,
    summarize,
)
from universal_search.intelligence.graph import (
    GRAPH_GENERATION_VERSION,
    GRAPH_PREPROCESSING_VERSION,
    GRAPH_SCHEMA_VERSION,
    GRAPH_VERSION,
    MAX_AGGREGATE_CANDIDATE_IDS,
    MAX_ALIAS_LOOKUPS_PER_DOCUMENT,
    MAX_CANDIDATE_COUNTER_VALUES,
    MAX_POSTINGS_PER_TERM,
    MAX_REFERENCES_PER_DOCUMENT,
    GraphNode,
    GraphStats,
    GraphStore,
    RelationshipEdge,
    RelationshipEvidence,
    document_records,
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
    "GRAPH_GENERATION_VERSION",
    "GRAPH_PREPROCESSING_VERSION",
    "GRAPH_SCHEMA_VERSION",
    "GRAPH_VERSION",
    "MAX_AGGREGATE_CANDIDATE_IDS",
    "MAX_ALIAS_LOOKUPS_PER_DOCUMENT",
    "MAX_CANDIDATE_COUNTER_VALUES",
    "MAX_POSTINGS_PER_TERM",
    "MAX_REFERENCES_PER_DOCUMENT",
    "DocumentAnalysis",
    "DocumentRecord",
    "GraphNode",
    "GraphStats",
    "GraphStore",
    "RebuildStats",
    "RelatedDocument",
    "RelationshipEdge",
    "RelationshipEvidence",
    "document_records",
    "analysis_for",
    "analyze",
    "clear",
    "rebuild",
    "related",
    "row_to_analysis",
    "summarize",
    "term_vocabulary",
]
