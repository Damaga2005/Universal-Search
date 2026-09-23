import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT,
    modified_at TEXT,
    content_hash TEXT,
    mtime_ns INTEGER,
    last_seen_run INTEGER NOT NULL DEFAULT 0,
    availability TEXT NOT NULL DEFAULT 'available',
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS usage_events_document
    ON usage_events(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    document_id UNINDEXED,
    name,
    path,
    content,
    tokenize = 'unicode61'
);
"""

# Columns added after the first release; applied to existing databases.
MIGRATIONS = (
    "ALTER TABLE documents ADD COLUMN mtime_ns INTEGER",
    "ALTER TABLE documents ADD COLUMN last_seen_run INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE documents ADD COLUMN availability TEXT NOT NULL DEFAULT 'available'",
)


class SearchDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(SCHEMA)
        self._migrate(connection)
        return connection

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(documents)")}
        for statement in MIGRATIONS:
            column = statement.split()[5]
            if column not in columns:
                connection.execute(statement)
                columns.add(column)
                connection.commit()
