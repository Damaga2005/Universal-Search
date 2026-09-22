import hashlib
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from universal_search.domain.document import Document, SourceKind


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".sql", ".yaml", ".yml", ".toml", ".ini", ".log"}


def _read_text(path: Path) -> str | None:
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return None
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def discover_local(root: Path) -> Iterable[Document]:
    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        content = _read_text(path)
        identity = f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode()
        yield Document(
            id=hashlib.sha256(identity).hexdigest(),
            source=SourceKind.LOCAL,
            path=path,
            name=path.name,
            extension=path.suffix.lower(),
            size=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest() if content is not None else None,
        )
