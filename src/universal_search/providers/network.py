"""Network/NAS provider: a mounted filesystem, nothing more (phase 024).

A NAS share is indexed through the OS mount: a UNC path (``\\\\server\\share``)
or a drive letter the user mapped. There is deliberately no network client,
protocol or credential handling here — phase 024 defines network/NAS support
as *mounted filesystem* support, so the provider only ever touches paths
the operating system already exposes as local directories. The privacy rule
is unchanged: no network module is imported anywhere in the application
(``tests/test_privacy.py`` enforces this).

Safety properties:

* **Configured roots only** — ``iter_files`` refuses any root outside the
  configured set; there is no silent fallback to an unsafe path.
* **Root containment** — a requested root must be a configured root or
  below it, compared after ``resolve()`` so a symlink cannot smuggle a
  scan out of the configured tree.
* **Disconnected states** — a share that vanished surfaces as
  :class:`ProviderError` entries (permissions included), never as a crash
  and never as an empty success that would let the indexer delete
  everything under the root.
* **No content policy of its own** — to the OS these are ordinary local
  files, so content flows through the normal extractor pipeline and
  availability comes from the file attributes like everywhere else.
"""

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from universal_search.providers.base import (
    AVAILABILITY,
    CHANGE_DETECTION,
    CONTENT,
    ENUMERATE,
    ERRORS,
    IDENTITY,
    INTERFACE_VERSION,
    MAX_PROVIDER_ERRORS,
    METADATA,
    STREAMING,
    CancelToken,
    IgnoredPath,
    ProviderError,
    ProviderFile,
    ScanError,
    availability_from_attributes,
)
from universal_search.providers.ignore import IgnoreRules
from universal_search.providers.local import scan_local


def _is_reachable(root: Path) -> bool:
    """Whether the OS still answers for ``root`` (a mount that is there)."""
    try:
        os.stat(root)
    except OSError:
        return False
    return True


class MountedPathProvider:
    """Shared base for providers that index a mounted filesystem.

    Subclasses set ``key``/``version``; everything else — containment,
    availability, bounded streaming enumeration over the same scanner the
    local provider uses — is inherited. The reparse/symlink policy is the
    scanner's: the root is resolved and symlinked directories are never
    descended into, so a share cannot point the scan outside its tree.
    """

    key = "mounted"
    version = "1.0"
    interface_version = INTERFACE_VERSION
    capabilities = frozenset(
        {
            ENUMERATE, METADATA, CONTENT, IDENTITY, CHANGE_DETECTION,
            AVAILABILITY, ERRORS, STREAMING,
        }
    )

    def __init__(
        self, roots: Iterable[Path | str] = (), rules: IgnoreRules | None = None
    ) -> None:
        self._roots = tuple(Path(root) for root in roots)
        self.rules = rules
        # Total enumeration errors seen in the last pass (including the ones
        # bounded away); read by the indexer to surface dropped errors.
        self._enumeration_errors = 0

    # -- configuration -----------------------------------------------------

    def _resolved_roots(self) -> tuple[Path, ...]:
        resolved = []
        for root in self._roots:
            try:
                resolved.append(root.resolve())
            except OSError:
                resolved.append(root)
        return tuple(resolved)

    def owns(self, root: Path | str) -> bool:
        """Whether ``root`` is a configured root or lives below one."""
        if not self._roots:
            return False
        try:
            requested = Path(root).resolve()
        except OSError:
            requested = Path(root)
        return any(
            requested == base or base in requested.parents
            for base in self._resolved_roots()
        )

    def available(self) -> bool:
        """True while at least one configured root answers on this machine."""
        return any(_is_reachable(root) for root in self._roots)

    # -- enumeration ---------------------------------------------------------

    def iter_files(
        self, root: Path, cancel: CancelToken | None = None
    ) -> Iterator[ProviderFile | ProviderError | IgnoredPath]:
        """Stream the mounted tree, refusing anything outside the roots."""
        root_path = Path(root)
        if not self.owns(root_path):
            yield ProviderError(
                self.key, root_path,
                f"root is not a configured {self.key} source",
            )
            return
        errors = 0
        for item in scan_local(root_path, self.rules):
            if cancel is not None and cancel.cancelled:
                return
            if isinstance(item, IgnoredPath):
                yield item
                continue
            if isinstance(item, ScanError):
                errors += 1
                self._enumeration_errors = errors
                if errors <= MAX_PROVIDER_ERRORS:
                    yield ProviderError(self.key, item.path, item.message)
                continue
            yield ProviderFile(
                provider=self.key,
                path=item.path,
                size=item.size,
                mtime_ns=item.mtime_ns,
                created_at=item.created_at,
                modified_at=item.modified_at,
                availability=availability_from_attributes(item.attributes),
                attributes=item.attributes,
            )


class NetworkProvider(MountedPathProvider):
    """NAS / network share exposed by the OS as a mounted filesystem.

    Configure it with the UNC paths or mapped drive letters the user
    actually mounts; a NAS with several shares is available while any of
    them answers.
    """

    key = "network"
    version = "1.0"
