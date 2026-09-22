from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class SourceKind(StrEnum):
    LOCAL = "local"
    ONEDRIVE = "onedrive"
    OTHER = "other"


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
