"""Phase 007: OneDrive source classification, availability and cloud safety."""

import time
from pathlib import Path

from universal_search.domain.document import SourceKind
from universal_search.domain.extraction import ExtractionResult
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.ranking import Candidate, Ranker
from universal_search.index.search import SearchEngine
from universal_search.providers import onedrive as od
from universal_search.providers.base import FileEntry
from universal_search.providers.onedrive import (
    AVAILABILITY_CLOUD_ONLY,
    OneDriveFile,
    OneDriveProvider,
)


# -- detection -------------------------------------------------------------

def test_detection_via_environment_variable(tmp_path, monkeypatch) -> None:
    root = tmp_path / "CloudSync"
    target = root / "apuntes" / "doc.md"
    monkeypatch.setenv("OneDrive", str(root))

    assert root in od.onedrive_roots()
    assert od.is_onedrive_path(target)
    assert not od.is_onedrive_path(tmp_path / "otras" / "doc.md")
    assert od.source_for_path(target) is SourceKind.ONEDRIVE


def test_detection_via_path_component_without_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OneDrive", raising=False)
    inside = tmp_path / "OneDrive - Empresa" / "trabajo.md"
    outside = tmp_path / "Documents" / "trabajo.md"
    inside.parent.mkdir(parents=True)

    assert od.is_onedrive_path(inside)
    assert not od.is_onedrive_path(outside)
    assert od.source_for_path(outside) is SourceKind.LOCAL


def test_detection_via_userprofile_glob(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OneDrive", raising=False)
    profile = tmp_path / "perfil"
    (profile / "OneDrive").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)

    roots = od.onedrive_roots()
    assert profile / "OneDrive" in roots


def test_roots_cache_follows_environment_changes(tmp_path, monkeypatch) -> None:
    first = tmp_path / "A"
    monkeypatch.setenv("OneDrive", str(first))
    assert first in od.onedrive_roots()

    second = tmp_path / "B"
    monkeypatch.setenv("OneDrive", str(second))
    assert second in od.onedrive_roots()
    assert first not in od.onedrive_roots()


# -- availability ----------------------------------------------------------

def test_availability_from_windows_attributes() -> None:
    assert od.availability_from_attributes(None) == "available"
    assert od.availability_from_attributes(0) == "available"
    assert od.availability_from_attributes(0x20) == "available"
    assert (
        od.availability_from_attributes(od.FILE_ATTRIBUTE_OFFLINE)
        == AVAILABILITY_CLOUD_ONLY
    )
    assert (
        od.availability_from_attributes(od.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
        == AVAILABILITY_CLOUD_ONLY
    )
    assert (
        od.availability_from_attributes(od.FILE_ATTRIBUTE_RECALL_ON_OPEN)
        == AVAILABILITY_CLOUD_ONLY
    )
    assert od.availability_of(Path(__file__)) == "available"
    assert od.availability_of(Path("/no/such/file-nowhere")) in (
        "unavailable",
        "available",  # platforms without placeholder support
    )


def test_allow_content_read_rules() -> None:
    # locally available: always readable
    assert od.allow_content_read("available", 10**12, 0.0) is True
    # cloud-only, no explicit opt-in: never readable (no silent download)
    assert od.allow_content_read(AVAILABILITY_CLOUD_ONLY, 10, 0.0) is False
    # explicit limit covering the file
    assert od.allow_content_read(AVAILABILITY_CLOUD_ONLY, 900_000, 1.0) is True
    # huge file beyond the limit: still not downloaded
    assert od.allow_content_read(AVAILABILITY_CLOUD_ONLY, 2_000_000, 1.0) is False
    # unavailable/offline: never readable
    assert od.allow_content_read("unavailable", 10, 100.0) is False


# -- indexing: phase A (synced files) --------------------------------------

def test_synced_onedrive_files_are_indexed_with_source_label(tmp_path) -> None:
    root = tmp_path / "OneDrive - Uso"
    root.mkdir()
    (root / "informe.md").write_text("resumen del practico", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")

    stats = Indexer(database).index_root(root)

    assert stats.created == 1
    results = SearchEngine(database).search("practico")
    assert [r.name for r in results] == ["informe.md"]
    assert results[0].source == "onedrive"          # clear source label
    assert results[0].availability == "available"


def test_local_file_is_reclassified_when_root_becomes_onedrive(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    (root / "nota.md").write_text("contenido importante", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")

    Indexer(database).index_root(root)
    row = database.connect().execute(
        "SELECT source, id FROM documents"
    ).fetchone()
    assert row["source"] == "local"

    monkeypatch.setenv("OneDrive", str(tmp_path))  # folder is now synced
    stats = Indexer(database).index_root(root)

    assert stats.unchanged == 0  # source changed -> row rebuilt, not skipped
    row = database.connect().execute(
        "SELECT source, id FROM documents"
    ).fetchone()
    assert row["source"] == "onedrive"
    results = SearchEngine(database).search("importante")
    assert [r.source for r in results] == ["onedrive"]


def test_onedrive_rows_are_deleted_on_reconcile(tmp_path) -> None:
    root = tmp_path / "OneDrive - Demo"
    root.mkdir()
    target = root / "borrar.md"
    target.write_text("temporal", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")

    Indexer(database).index_root(root)
    assert SearchEngine(database).search("temporal")

    target.unlink()
    stats = Indexer(database).index_root(root)

    assert stats.deleted == 1
    assert SearchEngine(database).search("temporal") == []
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0] == 0


# -- indexing: phase B (cloud-only files) ----------------------------------

def synthetic_cloud_scan(path: Path):
    """A scan that yields one cloud-only placeholder and nothing else."""

    mtime_ns = time.time_ns()  # stable across passes, like a real file

    def scan(_root, _rules=None):
        yield FileEntry(
            path=path,
            size=500,
            mtime_ns=mtime_ns,
            created_at=None,
            modified_at=None,
            attributes=od.FILE_ATTRIBUTE_OFFLINE
            | od.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
        )

    return scan


def test_cloud_only_file_is_indexed_as_metadata_without_downloading(
    tmp_path, monkeypatch
) -> None:
    from universal_search.index import indexer as indexer_module

    target = tmp_path / "OneDrive - Nube" / "nube.md"
    database = SearchDatabase(tmp_path / "index.db")
    calls: list[Path] = []

    def spy(path: Path) -> ExtractionResult:
        calls.append(path)
        return ExtractionResult(text="no debería leerse")

    monkeypatch.setattr(
        indexer_module, "scan_local", synthetic_cloud_scan(target)
    )
    stats = Indexer(database).index_root(
        tmp_path, read_content=spy, onedrive_download_mb=0.0
    )

    assert stats.cloud_only == 1
    assert stats.extraction_errors == 0
    assert calls == []                       # content was never read
    row = database.connect().execute(
        "SELECT content_hash, availability FROM documents"
    ).fetchone()
    assert row["content_hash"] is None       # no content, no hash
    assert row["availability"] == "cloud_only"

    # metadata (name/path) remains searchable, flagged as cloud-only
    results = SearchEngine(database).search("nube")
    assert [r.name for r in results] == ["nube.md"]
    assert results[0].availability == "cloud_only"

    # second pass: still unchanged, no read, no churn
    again = Indexer(database).index_root(
        tmp_path, read_content=spy, onedrive_download_mb=0.0
    )
    assert again.unchanged == 1
    assert calls == []


def test_cloud_only_download_requires_explicit_limit(
    tmp_path, monkeypatch
) -> None:
    from universal_search.index import indexer as indexer_module

    target = tmp_path / "OneDrive - Nube" / "descargable.md"
    database = SearchDatabase(tmp_path / "index.db")
    calls: list[Path] = []

    def spy(path: Path) -> ExtractionResult:
        calls.append(path)
        return ExtractionResult(text="contenido de la nube")

    monkeypatch.setattr(
        indexer_module, "scan_local", synthetic_cloud_scan(target)
    )
    # file is 500 B; limit of 1 MB covers it -> explicit read is allowed
    stats = Indexer(database).index_root(
        tmp_path, read_content=spy, onedrive_download_mb=1.0
    )

    assert calls == [target]                # one explicit, bounded read
    assert stats.cloud_only == 0
    row = database.connect().execute(
        "SELECT availability FROM documents"
    ).fetchone()
    assert row["availability"] == "available"  # download materialized it
    assert [r.name for r in SearchEngine(database).search("nube")] == [
        "descargable.md"
    ]


def test_huge_cloud_file_is_never_downloaded(tmp_path, monkeypatch) -> None:
    from universal_search.index import indexer as indexer_module

    target = tmp_path / "OneDrive - Nube" / "video.mp4"
    database = SearchDatabase(tmp_path / "index.db")
    calls: list[Path] = []

    def scan(_root, _rules=None):
        yield FileEntry(
            path=target,
            size=od.MB * 50,               # 50 MB
            mtime_ns=time.time_ns(),
            created_at=None,
            modified_at=None,
            attributes=od.FILE_ATTRIBUTE_OFFLINE,
        )

    monkeypatch.setattr(indexer_module, "scan_local", scan)
    stats = Indexer(database).index_root(
        tmp_path,
        read_content=lambda path: calls.append(path)
        or ExtractionResult(text="x"),
        onedrive_download_mb=10.0,          # limit (10 MB) < file (50 MB)
    )

    assert calls == []
    assert stats.cloud_only == 1


# -- provider (phase B abstraction) ----------------------------------------

def test_provider_discovers_metadata_with_availability(tmp_path) -> None:
    root = tmp_path / "OneDrive - Descubrir"
    root.mkdir()
    (root / "local.md").write_text("físicamente aquí", encoding="utf-8")

    entries = [item for item in OneDriveProvider().discover(root)]

    assert len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, OneDriveFile)
    assert entry.availability == "available"
    assert entry.size > 0
    # DocumentProvider protocol compatibility
    from universal_search.providers.base import DocumentProvider

    assert isinstance(OneDriveProvider(), DocumentProvider)


def test_provider_read_content_is_explicit(tmp_path) -> None:
    root = tmp_path / "OneDrive - Lectura"
    root.mkdir()
    real = root / "texto.txt"
    real.write_text("contenido legible", encoding="utf-8")

    cloud_entry = OneDriveFile(
        path=real, size=500, availability=AVAILABILITY_CLOUD_ONLY,
        modified_at=None,
    )

    # no limit configured -> explicit refusal, nothing read
    denied = OneDriveProvider(download_max_mb=0.0).read_content(cloud_entry)
    assert denied.text is None
    assert "cloud-only" in denied.error

    # explicit limit covering the file -> content read (hydration allowed)
    allowed = OneDriveProvider(download_max_mb=1.0).read_content(cloud_entry)
    assert allowed.error is None
    assert allowed.text == "contenido legible"

    # file beyond the limit -> refused even with a limit configured
    big = OneDriveFile(
        path=real, size=od.MB * 5, availability=AVAILABILITY_CLOUD_ONLY,
        modified_at=None,
    )
    too_big = OneDriveProvider(download_max_mb=1.0).read_content(big)
    assert "exceeds" in too_big.error

    # unavailable (offline/broken) -> refused
    broken = OneDriveFile(
        path=real, size=10, availability="unavailable", modified_at=None
    )
    assert "unavailable" in OneDriveProvider().read_content(broken).error


# -- ranking must stay provider-agnostic ------------------------------------

def test_onedrive_source_does_not_leak_into_ranking() -> None:
    base = dict(
        name="informe practico.md",
        path=r"C:\docs\informe practico.md",
        content="resumen del practico",
        modified_at=None,
        bm25_rank=-2.5,
    )
    ranker = Ranker()
    local = ranker.signals(
        Candidate(**base, source="local"), ("practico",)
    )
    cloud = ranker.signals(
        Candidate(**base, source="onedrive"), ("practico",)
    )
    assert local == cloud  # identical weights: no OneDrive-specific scoring
