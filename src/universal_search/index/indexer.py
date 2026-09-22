from universal_search.domain.document import Document
from universal_search.index.database import SearchDatabase


class Indexer:
    def __init__(self, database: SearchDatabase) -> None:
        self.database = database

    def upsert(self, document: Document) -> None:
        with self.database.connect() as connection:
            previous = connection.execute(
                "SELECT id FROM documents WHERE path = ?", (str(document.path),)
            ).fetchone()
            stale_ids = {document.id}
            if previous is not None:
                stale_ids.add(previous["id"])
            for stale_id in stale_ids:
                connection.execute("DELETE FROM documents_fts WHERE document_id = ?", (stale_id,))
            connection.execute("""
                INSERT INTO documents (id, source, path, name, extension, size, created_at, modified_at, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    id=excluded.id, source=excluded.source, name=excluded.name,
                    extension=excluded.extension, size=excluded.size,
                    created_at=excluded.created_at, modified_at=excluded.modified_at,
                    content_hash=excluded.content_hash, indexed_at=CURRENT_TIMESTAMP
            """, (document.id, document.source.value, str(document.path), document.name, document.extension, document.size,
                  document.created_at.isoformat() if document.created_at else None,
                  document.modified_at.isoformat() if document.modified_at else None, document.content_hash))
            connection.execute("INSERT INTO documents_fts(document_id,name,path,content) VALUES (?,?,?,?)",
                               (document.id, document.name, str(document.path), document.content or ""))
