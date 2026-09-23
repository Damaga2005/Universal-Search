# 019 — Provider & Extension Architecture

## Objective

Make Universal Search extensible enough to add storage providers and extractors without repeatedly changing the core search engine.

## Provider architecture

Formalise provider capabilities for:

- local filesystem
- OneDrive/local cloud representation if available
- future network/NAS
- removable media

Capabilities may include:

- enumeration
- metadata
- content reading
- change detection
- availability
- stable identity

## Extractors

Formalise:

- supported types
- detection
- extraction
- limits
- errors
- metadata

Extractors remain independent from providers.

## Plugin boundary

Determine whether runtime third-party plugins are genuinely necessary. Prefer internal extension points first.

If dynamic plugins are justified:

- stable interface
- interface version
- plugin metadata validation
- failure isolation
- trust/security documentation
- no marketplace

## Configuration

Provider/extractor registration must be inspectable and versioned where necessary.

## Compatibility

Adding a provider must not require rewriting SearchEngine, ranking or GUI presentation unless a genuinely new capability requires it.

## Tests

Add fake provider/extractor tests for registration, capability negotiation, failure isolation, unsupported formats, duplicate identities across sources, metadata and compatibility.

## Acceptance

- interfaces are explicit
- future providers can be added without core rewrites
- failures are isolated
- identities remain stable
- extension documentation exists
- complexity is justified

## Ready-to-copy implementation prompt

Implement Phase 019 — Provider & Extension Architecture. Audit actual provider/extractor boundaries, formalise capability interfaces, introduce registration/versioning only where justified, add fake providers/extractors and failure-isolation tests, and document future NAS/removable/cloud integration. Do not build a marketplace or unnecessary dynamic plugin system. Do not push.
