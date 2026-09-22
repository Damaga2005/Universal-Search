from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from universal_search.domain.document import Document


class DocumentProvider(Protocol):
    def discover(self, root: Path) -> Iterable[Document]:
        ...
