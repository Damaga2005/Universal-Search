from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path


class SourceKind(StrEnum):
    LOCAL = "local"
    ONEDRIVE = "onedrive"
    OTHER = "other"


def document_id_for(source: SourceKind | str, path: Path | str) -> str:
    """Stable document identity.

    A document keeps the same id for as long as its source and canonical
    path do not change; content or timestamp changes never alter identity.
    """
    source_value = source.value if isinstance(source, SourceKind) else str(source)
    return sha256(f"{source_value}\x00{path}".encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    source: SourceKind
    path: Path
    name: str
    extension: str
    size: int
    created_at: datetime | None
    modified_at: datetime | None
    content: str | None
    content_hash: str | None
