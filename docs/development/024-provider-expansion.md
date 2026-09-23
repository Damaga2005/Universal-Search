# 024 — Provider Expansion

## Objective
Evolve the provider architecture so new storage backends can be added without changing search, ranking or database internals throughout the codebase.

## Target providers
Evaluate and, when justified:
- local filesystem
- OneDrive synced/local content
- network/NAS paths
- removable drives
- a clean boundary for future cloud-only providers

Do not add providers merely for feature count.

## Provider contract
Formalize capabilities for enumeration, metadata, content access, stable identity, change detection, availability, errors, optional watch support and optional streaming reads.

## Identity
Provider identity must survive ordinary metadata changes and be namespaced by provider. Path alone cannot be the universal identity.

## Availability and performance
Represent online, unavailable, partially available, permission denied, disconnected and unsupported states. Enumeration must support batching, cancellation and bounded memory. A slow provider must not block unrelated providers.

## Security
Treat provider data as untrusted. Validate paths, metadata, sizes, timestamps and identities. Respect Windows reparse/symlink boundaries and configured source roots.

## Tests
Create fake providers for normal operation, duplicates, unstable metadata, disappearing files, permission failures, transient failures, large enumerations and cancellation. Test mixed-provider indexing.

## Acceptance
Adding a provider requires implementing a documented interface rather than modifying unrelated search/ranking/database code.

## Ready-to-copy implementation prompt
Implement Phase 024 — Provider Expansion. Audit local and OneDrive providers, formalize capabilities and stable identity, then add justified network/NAS and removable-source support while preserving a clean boundary for future cloud-only providers. Implement batching, cancellation, availability states, failure isolation and security validation. Add fake-provider and mixed-source integration tests, run the full suite and performance checks, document the provider contract, and do not push unless explicitly instructed.
