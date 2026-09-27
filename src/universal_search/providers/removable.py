"""Removable media provider: a USB flash drive or SD card, mounted (024).

A removable drive is indexed exactly like the NAS share it physically is
— a mounted filesystem — through :class:`~universal_search.providers.network.MountedPathProvider`.
The difference is the availability semantics: a removable volume is either
attached or gone, so ``available()`` requires every configured root to
answer, and a drive that disappears between the check and the enumeration
surfaces as :class:`ProviderError` entries instead of an empty success
that would let the indexer delete everything under the root.

No removable-specific OS API is used: the provider works on any platform
through the same standard-library filesystem access as every other
mounted-path provider, and no network module is imported.
"""

from collections.abc import Iterable
from pathlib import Path

from universal_search.providers.network import MountedPathProvider, _is_reachable
from universal_search.providers.ignore import IgnoreRules


class RemovableProvider(MountedPathProvider):
    """Removable media (USB flash, SD card) mounted at a drive/mount point."""

    key = "removable"
    version = "1.0"

    def __init__(
        self, roots: Iterable[Path | str] = (), rules: IgnoreRules | None = None
    ) -> None:
        super().__init__(roots, rules)

    def available(self) -> bool:
        """True only while every configured root is still attached.

        A stale drive letter (the card was pulled) must read as
        unavailable, not as an empty source.
        """
        return bool(self._roots) and all(
            _is_reachable(root) for root in self._roots
        )
