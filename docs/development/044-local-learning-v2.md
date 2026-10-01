# 044 — Local Learning v2

## Objective
Improve local usage learning without turning Universal Search into an opaque recommendation engine.

## Signals
Evaluate:
- selected result;
- opened result;
- repeated query;
- source preference;
- document-type preference;
- recency.

All signals remain local and optional.

## Ranking
Learning is a secondary signal.

Rules:
- exact filename/phrase matches remain dominant;
- explicit filters override learned preference;
- new documents must not be permanently buried;
- deterministic baseline ranking remains available;
- learning can be disabled;
- learning data can be inspected and deleted.

## Cold start
Define behavior for users with no history.

## Explainability
When learning changes ranking materially, expose a concise reason where useful.

## Privacy
No telemetry.
No upload.
No hidden user profile.

## Tests
Use synthetic interaction histories to measure:
- ranking changes;
- stability;
- cold start;
- forgetting;
- disabled mode;
- exact-match protection.

## Acceptance
Learning improves repeated workflows while remaining bounded, inspectable, reversible and subordinate to explicit search intent.

## Ready-to-copy implementation prompt
Implement Phase 044 — Local Learning v2. Audit the current local usage signals and introduce a bounded, optional and explainable learning layer only where measurable benefit exists. Keep exact matches and explicit filters dominant, support disable/inspect/delete, define cold-start behavior and add synthetic ranking evaluation. No telemetry, cloud profile or opaque recommendation model. Do not push unless explicitly instructed.
