# 022 — Related Document Graph

## Objective
Implement the roadmap item for a local, deterministic graph of related documents. It must explain why documents are related and remain separate from query relevance.

## Relationship signals
Evaluate:
- shared normalized terms
- weighted keyword overlap
- title/headings overlap
- phrase overlap
- directory/path relationship
- explicit references when safely detectable
- lexical similarity
- provider/context relationship

Do not use sensitive personal attributes or cloud AI.

## Data model
Define versioned derived entities for document nodes, relationship edges, edge type, weight, evidence, generation version and timestamp. Graph data must be rebuildable from canonical indexed documents.

## Candidate generation
Do not perform naive all-pairs comparison for large corpora. Use inverted-term candidates and explicit limits for terms, candidates, minimum similarity and stored edges.

## Incremental maintenance
On document update, recompute only affected relationships where possible. On deletion, remove its node and obsolete edges. Provide a deterministic full rebuild.

## Ranking and UX
Related-document ranking must not leak into normal query ranking. Present a ranked list first, with evidence such as shared terms or lexical similarity. Avoid a large graph visualization unless usability evidence justifies it.

## Tests
Cover deterministic edges, identical/unrelated documents, deletion, incremental updates, rebuild, bounds, special characters, ranking stability and derived-data version changes.

## Acceptance
A corpus containing BJT, Ebers-Moll and unrelated documents produces explainable related-document results locally and updates them incrementally.

## Ready-to-copy implementation prompt
Implement Phase 022 — Related Document Graph. Audit the current document-intelligence and ranking layers first. Add a bounded, deterministic local relationship graph with versioned derived data, explainable evidence, incremental maintenance and a simple related-documents UX. Keep graph relevance separate from search relevance, prevent O(N²) behavior, add synthetic evaluation data and performance tests, and run the complete regression suite. Do not introduce cloud AI or external services. Do not push unless explicitly instructed.
