# Phase 024 — Provider expansion

Status: implementation complete; full suite green (765 passed, 3 skipped),
pyflakes clean, one temporary task commit.

## Design decision (required before implementation)

**The provider key is the canonical source discriminator and the document
identity is provider-namespaced.** The canonical uniqueness contract is
`(source, path)`, enforced by a safe additive migration (schema v6 → v7).
Two providers may now own the same path (a NAS share and a local folder, a
removable drive and the drive letter it replaced) without changing search or
ranking semantics. Existing local/onedrive rows and existing tests remain
valid: the migration is a lossless table rebuild that preserves every row and
id, and the stable identity `document_id_for(source, path)` was already
namespaced by source — only the database constraint changed.

## Scope delivered

- **Formalized contract** (`providers/base.py`): the capability vocabulary
  grows to nine (`enumerate`, `metadata`, `content`, `identity`,
  `change_detection`, `availability`, `errors`, `watch`, `streaming`) with a
  structured `ProviderCapabilities` declaration; `ProviderFile` (metadata +
  availability + namespaced identity), `ProviderError`, `ProviderResult`,
  `collect_provider_files` (bounded), `CancelToken` (cooperative
  cancellation), shared availability states and
  `availability_from_attributes`; `INTERFACE_VERSION = 2`.
- **Registry negotiation**: `ProviderRegistry.register` rejects duplicate
  keys, unknown capabilities and incompatible interface versions, accepts
  legacy providers that predate `interface_version`, and reports a provider
  whose `available()` raises as unavailable instead of propagating.
- **Streaming enumeration**: `Provider.iter_files(root, cancel)` returns an
  iterator of `ProviderFile | ProviderError | IgnoredPath`. `LocalProvider`
  and `OneDriveProvider` implement it over the same scanner that has always
  been used (root resolved, symlinked directories never followed, unreadable
  entries reported, errors bounded by `MAX_PROVIDER_ERRORS = 100`).
- **Mounted-path providers** (`providers/network.py`, `providers/removable.py`):
  `NetworkProvider` (NAS/share as UNC or mapped drive) and
  `RemovableProvider` (USB/SD) share a `MountedPathProvider` base. Both are
  standard-library filesystem providers only — no network client, protocol or
  credential handling (the privacy rule is unchanged). Both validate root
  containment (a requested root must be a configured root or below it, after
  `resolve()`), report disconnected/unreachable roots as `ProviderError`, and
  refuse roots outside the configured set. `available()` differs: a NAS is
  available while any share answers; a removable volume requires every
  configured root to be attached.
- **Mixed-provider indexing** (`index/indexer.py`): `index_root(...,
  provider=...)` enumerates through the provider and stores the provider key
  as `documents.source`; `index_sources(...)` indexes several
  `(provider, root)` pairs with per-provider failure isolation. A provider
  that dies mid-enumeration costs only its own pass: the error is counted and
  the deletion pass is skipped, because files after the failure point were
  never seen and would otherwise be deleted while still existing on disk. The
  legacy classification path (no `provider=`) is unchanged and still
  propagates crashes exactly as before.
- **Source-scoped reconciliation**: `_delete_missing` is scoped to the
  provider's own rows when a provider is given, so re-indexing one provider
  never deletes another provider's rows for the same path.
- **Uniqueness migration** (`index/database.py`): schema v7 rebuilds
  `documents` without the `path`-only constraint, creates
  `documents_source_path` (UNIQUE on `(source, path)`) and `documents_path`
  (plain, keeps the per-root deletion range scan fast). The rebuild is
  column-explicit (legacy tables gained columns via `ALTER TABLE` and have a
  different order), idempotent (detected by the old DDL text) and lossless.
- **Source vocabulary**: `SourceKind` gains `NETWORK` and `REMOVABLE` (member
  value = provider key); the query language (`source:network`,
  `source:removable`), the CLI `--source` choices and the GUI source filter
  accept them. Ranking is unchanged (`SOURCE_SCORES.get(source, 1.0)`
  defaults new sources to a neutral 1.0).

## Files

Modified: `providers/base.py`, `providers/registry.py`, `providers/local.py`,
`providers/onedrive.py`, `index/indexer.py`, `index/database.py`,
`providers/__init__.py`, `domain/document.py` (two `SourceKind` members),
`query/nodes.py` (`SOURCE_KINDS`), `cli.py` (`--source` choices),
`gui/app.py` (source filter values), `tests/test_provider.py`,
`tests/test_indexer.py`, `tests/test_extensions.py` (two tests encoded the
old path-only uniqueness contract and were updated to the `(source, path)`
contract).

Created: `providers/network.py`, `providers/removable.py`,
`tests/test_provider_expansion.py`.

Note: the brief lists `tests/test_providers.py`; the repository's provider
test module is `tests/test_provider.py` (singular), which is what was
extended.

## Measured provider behavior

- `LocalProvider.iter_files` over a tree: same entries as `scan_local`, with
  `ScanError` converted to `ProviderError` and bounded at 100.
- `NetworkProvider`/`RemovableProvider` over a mounted tree: files enumerated
  with the provider key as source; an unconfigured root yields exactly one
  `ProviderError` and no scan; a removed root yields `ProviderError` from the
  scanner.
- Mixed indexing: a provider raising mid-enumeration after one file leaves
  that file indexed, counts one error, skips deletion, and the next provider
  in `index_sources` still indexes fully.
- Performance smoke: 500 small files through the provider path index
  correctly (created == 500, errors == 0) in ~3 s on an idle machine; the
  test bound is 60 s so only a real regression trips it.

## Tests

`tests/test_provider_expansion.py` (new, 48 tests) covers: the capability
vocabulary and `ProviderCapabilities`; registry negotiation (duplicate key,
unknown capability, incompatible interface version, legacy provider without
`interface_version`, failing `available()`); namespaced identity; local and
OneDrive `iter_files` (metadata, error conversion, cancellation, symlink
policy, ignore rules); cancellation and bounded error collection; network and
removable availability, root containment and disconnected states; indexing
through a provider (source written, unstable metadata re-indexed, disappeared
files deleted, permission errors counted, untrusted/hostile file names
indexed, ignored paths counted); two providers owning the same path;
`index_sources` isolation (failing provider, partial progress preserved,
cancelled pass never deletes); the v6 → v7 migration (rows preserved, old
constraint gone, new constraint enforced, idempotent); fresh-database
`(source, path)` uniqueness; the query language accepting the new sources; and
the performance smoke. `tests/test_provider.py` and `tests/test_indexer.py`
were extended with contract and source-scoped reconciliation tests.

## Verification

- `.venv\Scripts\python -m pytest tests\test_provider_expansion.py -q -o addopts=`
  → 47 passed, 1 skipped (symlink test: no privileges on this machine).
- `.venv\Scripts\python -m pytest -q -o addopts=` → **765 passed, 3 skipped**
  (baseline before the phase: 695 passed, 2 skipped).
- `.venv\Scripts\python -m pyflakes src tests` → clean.
- `universal-search extensions` lists all four providers with capabilities,
  interface version and availability.

## Limitations and concerns

- The background worker, CLI and control centre still call `index_root`
  without a provider, so they keep the legacy per-path classification. Wiring
  configured NAS/removable roots to the new providers (root → provider
  routing) is deliberately out of scope for this phase: the registry, the
  indexer seam and the providers are in place, and routing needs a
  configuration surface (which roots are NAS vs removable) that does not exist
  yet. This is the main follow-up.
- `watch` is in the capability vocabulary but no built-in provider claims it
  yet; change notifications remain a worker concern.
- The provider path isolates *all* mid-pass exceptions (not only provider
  ones); the legacy path preserves the old propagate-on-crash behavior. A
  database-level failure inside a provider pass is therefore counted and the
  pass continues rather than aborting — the safe outcome for a mixed-provider
  run, but worth knowing.
- The v7 migration rebuilds the `documents` table; it is lossless and
  idempotent, but it is a full table copy, so a very large index pays a
  one-time rebuild cost on first open (proportional to the index size).
