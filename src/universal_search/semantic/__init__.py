"""Optional local semantic layer (phase 026).

A dependency-free, local-only approximation of semantic retrieval: a
character n-gram TF-IDF cosine ranker. It is **fallback-only** — it is
consulted only when the lexical engine returns nothing, so exact
filenames, exact phrases, filters and query operators stay authoritative
and a semantic candidate can never reorder a non-empty lexical result.

The layer is optional and removable: with no provider configured, or with
the derived tables deleted, search is exactly the lexical engine it was
before. No runtime dependency, no network, no model download, no upload.

Measured decision (phase 026, see docs/development/026-...-report.md):
on the fixed corpus the lexical engine retrieves nothing for the synonym,
paraphrase and morphological queries. A bounded semantic *boost* on a
non-empty pool was evaluated and rejected (it flipped the exact-token
query "CMOS" and gained nothing on the failure subset). The fallback-only
design recovered the failure queries (mean failure R@5 0.179 -> 0.762)
with exact-match correctness 1.0 and no top-1 regression.
"""

from universal_search.semantic.engine import HybridSearchEngine
from universal_search.semantic.index import SemanticIndex
from universal_search.semantic.ngram import (
    NGRAM_PREPROCESSING_VERSION,
    NGRAM_SIZE,
    NGRAM_VERSION,
    SemanticProvider,
)

__all__ = [
    "HybridSearchEngine",
    "SemanticIndex",
    "SemanticProvider",
    "NGRAM_SIZE",
    "NGRAM_VERSION",
    "NGRAM_PREPROCESSING_VERSION",
]
