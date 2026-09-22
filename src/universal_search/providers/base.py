from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from universal_search.domain.document import Document


@runtime_checkable
class DocumentProvider(Protocol):
    def discover(self, root: Path) -> Iterable[Document]:
        ...
