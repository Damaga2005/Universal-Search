# 042 — Search Experience v2

## Objective
Make search the central product experience rather than merely a database query surface.

## Scope
Improve:
- incremental result rendering where safe;
- query suggestions from indexed local data;
- recent searches stored locally only;
- search history controls;
- keyboard shortcuts;
- filters;
- source/type chips;
- result grouping;
- snippets;
- related documents;
- open/reveal/copy actions;
- result explanation when useful.

Never allow suggestions or history to override exact search semantics.

## Query behavior
Preserve the Phase 012 language contract.

Malformed queries must remain non-destructive.

Explicit filters always win over personalization or semantic signals.

## Privacy
Search history is optional and local.

Provide:
- disable history;
- clear history;
- inspect stored history;
- retention policy.

Do not transmit queries.

## Performance
Measure:
- keystroke-to-first-result;
- keystroke-to-stable-results;
- cold/warm behavior;
- large result sets.

Avoid expensive ranking on every keystroke unless debounced and bounded.

## Tests
Cover query semantics, filters, history privacy, keyboard behavior, incremental updates and ranking stability.

## Acceptance
A user can search repeatedly without friction and understand why each result is useful.

## Ready-to-copy implementation prompt
Implement Phase 042 — Search Experience v2. Improve the primary search workflow with responsive results, local suggestions/history, filters, result actions, related documents and useful explanations while preserving the existing query-language contract and privacy guarantees. Measure interactive latency, keep the UI non-blocking, add regression tests and real Windows smoke coverage. Do not introduce cloud search or remote telemetry. Do not push unless explicitly instructed.
