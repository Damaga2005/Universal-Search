# Development Prompt 004 — Search Ranking

## Objective

Turn raw full-text matches into useful ranked results.

## Ranking signals

The initial ranking model should consider:

1. exact filename match;
2. filename token match;
3. path match;
4. exact phrase match;
5. term frequency;
6. proximity of terms;
7. content relevance;
8. document type;
9. source/context;
10. recency only where it improves usefulness.

The ranking system must be deterministic.

## University context

The system should be capable of identifying a user-defined workspace such as:

    OneDrive/Universidad/

and giving it contextual weight when the user searches from that workspace/profile.

This is not yet an AI model.

## Future personal signals

Reserve an extension point for local usage signals such as:

- frequently opened result;
- frequently selected result;
- successful searches.

Do not collect these signals by default unless the product setting explicitly enables them.

## Acceptance criteria

- Search results are deterministic.
- A file named exactly like the query can outrank a weak content-only match.
- A document with strong contextual matches can outrank a document containing one isolated occurrence.
- Tests verify ranking behavior.
- Ranking is isolated from storage and presentation.

## Prompt

Implement a deterministic composite ranking engine on top of the existing FTS search. Keep ranking provider-independent and test every scoring signal. Do not use AI, embeddings or network services. Preserve an extension point for optional future local personalization.
