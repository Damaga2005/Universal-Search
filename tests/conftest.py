import sqlite3
from pathlib import Path

import pytest


def count_rows(db_path: Path, table: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


@pytest.fixture
def row_count():
    return count_rows
