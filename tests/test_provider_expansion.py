"""Phase 024: provider expansion contract tests.

Covers the formalized provider contract (capabilities, namespaced identity,
availability, cancellation, bounded errors), the registry negotiation rules,
the network/removable mounted-path providers, mixed-provider indexing with
per-provider failure isolation and the ``(source, path)`` uniqueness
migration. Written red first: every test fails against the phase-023 code.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from universal_search.domain.document import SourceKind, document_id_for
from universal_search.index.database import SCHEMA_VERSION, SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.providers.base import (
    AVAILABILITY_AVAILABLE,
    CAPABILITIES,
    CHANGE_DETECTION,
    CONTENT,
    ENUMERATE,
    ERRORS,
    IDENTITY,
    INTERFACE_VERSION,
    METADATA,
    MAX_PROVIDER_ERRORS,
    STREAMING,
    WATCH,
    CancelToken,
    DocumentProvider,
    Provider,
    ProviderCapabilities,
    ProviderError,
    ProviderFile,
    ProviderRegistry,
    ProviderResult,
    collect_provider_files,
)
from universal_search.providers.local import LocalProvider
from universal_search.providers.network import NetworkProvider
from universal_search.providers.onedrive import OneDriveProvider
from universal_search.providers.registry import register_builtins
from universal_search.providers.removable import RemovableProvider


# -- fakes ----------------------------------------------------------------------


class FakeProvider:
    """Minimal provider that exercises the contract without a filesystem."""

    key = "fake"
    version = "1.0"
    interface_version = INTERFACE_VERSION
    capabilities = frozenset({ENUMERATE, METADATA, IDENTITY, ERRORS, STREAMING})

    def __init__(
        self,
        items=(),
        *,
        key="fake",
        roots=(),
        fail_after=None,
        available=True,
    ) -> None:
        self.key = key
        self._items = list(items)
        self._roots = tuple(Path(root) for root in roots)
        self._fail_after = fail_after
        self._available = available

    def available(self) -> bool:
        return self._available

    def owns(self, root: Path | str) -> bool:
        if not self._roots:
            return True
        requested = Path(root)
        return any(
            requested == configured or configured in requested.parents
            for configured in self._roots
        )

    def iter_files(self, root: Path, cancel: CancelToken | None = None):
        if not self.owns(root):
            yield ProviderError(
                self.key, Path(root), "root is not a configured source"
            )
            return
        for index, item in enumerate(self._items):
            if cancel is not None and cancel.cancelled:
                return
            if self._fail_after is not None and index >= self._fail_after:
                raise OSError("transient provider failure")
            yield item


def make_file(path: Path, *, size: int = 10, mtime_ns: int = 1_700_000_000_000_000_000) -> ProviderFile:
    return ProviderFile(
        provider="fake",
        path=path,
        size=size,
        mtime_ns=mtime_ns,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        modified_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        availability=AVAILABILITY_AVAILABLE,
    )


def write_real(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# -- capability vocabulary ------------------------------------------------------


def test_capability_vocabulary_is_exhaustive() -> None:
    assert set(CAPABILITIES) == {
        ENUMERATE, METADATA, CONTENT, IDENTITY, CHANGE_DETECTION,
        "availability", ERRORS, WATCH, STREAMING,
    }


def test_provider_capabilities_declares_every_capability() -> None:
    capabilities = ProviderCapabilities(
        enumerate=True, metadata=True, content=True, identity=True,
        change_detection=True, availability=True, errors=True, watch=True,
        streaming=True,
    )
    assert capabilities.as_frozenset() == frozenset(CAPABILITIES)
    empty = ProviderCapabilities()
    assert empty.as_frozenset() == frozenset()


def test_provider_protocol_and_document_provider_are_distinct() -> None:
    provider = FakeProvider()
    assert isinstance(provider, Provider)
    # A legacy discover-only provider still satisfies DocumentProvider.
    class Legacy:
        key = "legacy"
        version = "1.0"
        capabilities = frozenset({ENUMERATE})

        def __init__(self) -> None:
            self.discovered = False

        def discover(self, root: Path):
            self.discovered = True
            return []

        def available(self) -> bool:
            return True

    legacy = Legacy()
    assert isinstance(legacy, DocumentProvider)


# -- registry negotiation ---------------------------------------------------------


def test_registry_rejects_duplicate_key() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider(key="fake"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeProvider(key="fake"))


def test_registry_rejects_unknown_capability() -> None:
    class Misdeclared(FakeProvider):
        capabilities = frozenset({ENUMERATE, "telepathy"})

    with pytest.raises(ValueError, match="unknown capabilities"):
        ProviderRegistry().register(Misdeclared())


def test_registry_rejects_incompatible_interface_version() -> None:
    class OldInterface(FakeProvider):
        interface_version = INTERFACE_VERSION + 1

    with pytest.raises(ValueError, match="interface version"):
        ProviderRegistry().register(OldInterface())


def test_registry_accepts_legacy_provider_without_interface_version() -> None:
    class Undeclared:
        key = "legacy-fake"
        version = "1.0"
        capabilities = frozenset({ENUMERATE})

        def available(self) -> bool:
            return True

        def iter_files(self, root: Path, cancel: CancelToken | None = None):
            return []

    registry = ProviderRegistry()
    registry.register(Undeclared())
    assert "legacy-fake" in registry.keys()


def test_registry_infos_report_interface_version_and_capabilities() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider(), kind="test", detail="a fake")
    (info,) = registry.infos()
    assert info.interface_version == INTERFACE_VERSION
    assert info.kind == "test"
    assert info.detail == "a fake"
    assert info.supports(ENUMERATE)
    assert not info.supports(WATCH)


def test_registry_for_capability_filters_by_availability() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider(available=True), kind="test")
    registry.register(FakeProvider(key="down", available=False), kind="test")
    assert registry.for_capability(ENUMERATE) == ("fake",)


def test_registry_reports_failing_availability_as_unavailable() -> None:
    class Exploding(FakeProvider):
        def available(self) -> bool:
            raise RuntimeError("boom")

    registry = ProviderRegistry()
    registry.register(Exploding())
    (info,) = registry.infos()
    assert info.available is False
    assert "availability check failed" in info.detail


def test_registry_reports_the_providers_actual_interface_version() -> None:
    """infos() reports the provider's declared version, not a constant."""

    class Older:
        key = "older"
        version = "1.0"
        # A provider built against an older contract declares it; infos()
        # must report the declaration, not the current build constant.
        interface_version = INTERFACE_VERSION - 1
        capabilities = frozenset({ENUMERATE})

        def available(self) -> bool:
            return True

        def iter_files(self, root: Path, cancel: CancelToken | None = None):
            return []

    class Legacy:
        key = "legacy"
        version = "1.0"
        capabilities = frozenset({ENUMERATE})

        def available(self) -> bool:
            return True

        def iter_files(self, root: Path, cancel: CancelToken | None = None):
            return []

    registry = ProviderRegistry()
    # register() refuses a mismatched contract, so seed the registry
    # directly to verify infos() reports the declaration, not the build.
    registry._providers["older"] = (Older(), "older", "")
    registry._providers["legacy"] = (Legacy(), "legacy", "")
    reported = {info.key: info.interface_version for info in registry.infos()}
    # The declared version is reported as-is...
    assert reported["older"] == INTERFACE_VERSION - 1
    # ...and a legacy provider with no declaration falls back to the build.
    assert reported["legacy"] == INTERFACE_VERSION


def test_builtins_include_network_and_removable() -> None:
    registry = register_builtins()
    assert {"local", "onedrive", "network", "removable"} <= set(registry.keys())
    # Default instances own no roots, so they report unavailable: registering
    # them must not make them look like indexable sources.
    assert "network" not in registry.for_capability(ENUMERATE)
    assert "removable" not in registry.for_capability(ENUMERATE)


# -- namespaced identity ----------------------------------------------------------


def test_identity_is_namespaced_by_provider_key() -> None:
    path = Path("C:/srv/share/nota.md")
    local_id = document_id_for(SourceKind.LOCAL, path)
    network_id = document_id_for(SourceKind.NETWORK, path)
    assert local_id != network_id
    assert network_id == document_id_for("network", path)


def test_provider_file_exposes_source_and_namespaced_identity() -> None:
    path = Path("C:/srv/share/nota.md")
    item = ProviderFile(
        provider="network", path=path, size=1, mtime_ns=2,
        created_at=None, modified_at=None,
    )
    assert item.source == "network"
    assert item.document_id == document_id_for("network", path)


# -- local provider iter_files -----------------------------------------------------


def test_local_iter_files_yields_namespaced_files(tmp_path: Path) -> None:
    write_real(tmp_path / "docs" / "nota.md", "contenido bjt")
    provider = LocalProvider()

    items = [
        item for item in provider.iter_files(tmp_path)
        if isinstance(item, ProviderFile)
    ]

    assert len(items) == 1
    (item,) = items
    assert item.provider == "local"
    assert item.path == (tmp_path / "docs" / "nota.md").resolve()
    assert item.size == (tmp_path / "docs" / "nota.md").stat().st_size
    assert item.availability == AVAILABILITY_AVAILABLE


def test_local_iter_files_converts_scan_errors(tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory.md"
    target.write_text("soy un fichero", encoding="utf-8")
    provider = LocalProvider()

    items = list(provider.iter_files(target))

    assert len(items) == 1
    (error,) = items
    assert isinstance(error, ProviderError)
    assert error.provider == "local"
    assert error.path == target.resolve()


def test_local_iter_files_honours_cancel_token(tmp_path: Path) -> None:
    for number in range(50):
        write_real(tmp_path / f"f{number:03d}.md", f"contenido {number}")
    provider = LocalProvider()
    token = CancelToken()

    iterator = provider.iter_files(tmp_path, token)
    assert iter(iterator) is iterator  # lazy: an iterator, not a materialized list
    next(iterator)
    token.cancel()
    with pytest.raises(StopIteration):
        next(iterator)


@pytest.mark.skipif(
    not hasattr(Path, "symlink_to"), reason="symlinks unavailable on this platform"
)
def test_local_iter_files_does_not_follow_symlinked_directories(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    write_real(real / "secreto.md", "contenido secreto")
    tree = tmp_path / "tree"
    tree.mkdir()
    write_real(tree / "normal.md", "contenido normal")
    try:
        (tree / "enlazado").symlink_to(real, target_is_directory=True)
    except OSError:  # pragma: no cover - no privileges on this machine
        pytest.skip("symlink creation not permitted")

    items = [
        item for item in LocalProvider().iter_files(tree)
        if isinstance(item, ProviderFile)
    ]

    assert [item.path.name for item in items] == ["normal.md"]


def test_local_provider_declares_streaming_and_errors() -> None:
    provider = LocalProvider()
    assert {STREAMING, ERRORS, ENUMERATE, METADATA, CONTENT, IDENTITY} <= set(
        provider.capabilities
    )


def test_onedrive_iter_files_yields_availability(tmp_path: Path) -> None:
    write_real(tmp_path / "nube.md", "contenido en la nube")
    provider = OneDriveProvider()

    items = [
        item for item in provider.iter_files(tmp_path)
        if isinstance(item, ProviderFile)
    ]

    assert len(items) == 1
    (item,) = items
    assert item.provider == "onedrive"
    # Non-Windows attributes are None, which means locally available.
    assert item.availability == AVAILABILITY_AVAILABLE


# -- cancellation and bounded errors -------------------------------------------------


def test_cancel_token_flags_cancellation() -> None:
    token = CancelToken()
    assert token.cancelled is False
    token.cancel()
    assert token.cancelled is True


def test_collect_provider_files_materializes_bounded_result(tmp_path: Path) -> None:
    items = [make_file(tmp_path / f"f{number}.md") for number in range(5)]
    provider = FakeProvider(items)

    result = collect_provider_files(provider, tmp_path)

    assert isinstance(result, ProviderResult)
    assert result.provider == "fake"
    assert result.files == tuple(items)
    assert result.errors == ()


def test_collect_provider_files_bounds_error_reporting() -> None:
    errors = [
        ProviderError("fake", Path(f"/x{number}.md"), "boom")
        for number in range(500)
    ]
    provider = FakeProvider(errors)

    result = collect_provider_files(provider, Path("/"))

    assert len(result.errors) == MAX_PROVIDER_ERRORS


class BoundingProvider(FakeProvider):
    """A provider that bounds its errors and reports the true total.

    Mimics the real providers: it yields at most MAX_PROVIDER_ERRORS errors
    but records the full count so the indexer can surface the dropped ones.
    """

    def __init__(self, errors, **kwargs) -> None:
        super().__init__(errors, **kwargs)
        self._enumeration_errors = 0

    def iter_files(self, root: Path, cancel: CancelToken | None = None):
        for item in super().iter_files(root, cancel):
            if isinstance(item, ProviderError):
                self._enumeration_errors += 1
                if self._enumeration_errors > MAX_PROVIDER_ERRORS:
                    continue  # bounded: drop the rest, but keep counting
            yield item


def test_indexer_surfaces_dropped_provider_errors(tmp_path: Path) -> None:
    """Errors a provider bounds away are surfaced, not silently dropped."""
    errors = [
        ProviderError("fake", Path(f"/x{number}.md"), "boom")
        for number in range(MAX_PROVIDER_ERRORS + 40)
    ]
    database = SearchDatabase(tmp_path / "index.db")

    stats = Indexer(database).index_root(
        tmp_path, provider=BoundingProvider(errors)
    )

    assert stats.errors == MAX_PROVIDER_ERRORS
    assert stats.dropped_errors == 40


def test_fake_provider_enumeration_is_lazy(tmp_path: Path) -> None:
    provider = FakeProvider([make_file(tmp_path / "a.md")])
    iterator = provider.iter_files(tmp_path)
    assert iter(iterator) is iterator
    assert isinstance(next(iterator), ProviderFile)
    with pytest.raises(StopIteration):
        next(iterator)


# -- network / removable providers -----------------------------------------------------


def test_network_provider_without_roots_is_unavailable_and_refuses(tmp_path: Path) -> None:
    provider = NetworkProvider()
    assert provider.available() is False
    assert provider.owns(tmp_path) is False
    assert list(provider.iter_files(tmp_path)) == [
        ProviderError("network", tmp_path, "root is not a configured network source")
    ]


def test_network_provider_available_when_root_reachable(tmp_path: Path) -> None:
    provider = NetworkProvider(roots=[tmp_path])
    assert provider.available() is True


def test_network_provider_unavailable_when_disconnected(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    provider = NetworkProvider(roots=[share])
    assert provider.available() is True
    share.rmdir()  # the share vanished
    assert provider.available() is False


def test_network_provider_validates_root_containment(tmp_path: Path) -> None:
    share = tmp_path / "share"
    nested = share / "docs"
    nested.mkdir(parents=True)
    provider = NetworkProvider(roots=[share])

    assert provider.owns(share) is True
    assert provider.owns(nested) is True
    assert provider.owns(tmp_path) is False  # parent of the configured root
    assert provider.owns(tmp_path / "other") is False  # unrelated sibling


def test_network_provider_iter_files_scans_mounted_tree(tmp_path: Path) -> None:
    share = tmp_path / "share"
    write_real(share / "a.md", "contenido alpha")
    write_real(share / "sub" / "b.md", "contenido beta")
    provider = NetworkProvider(roots=[share])

    items = [
        item for item in provider.iter_files(share)
        if isinstance(item, ProviderFile)
    ]

    assert {item.path.name for item in items} == {"a.md", "b.md"}
    assert all(item.provider == "network" for item in items)


def test_network_provider_iter_files_refuses_unconfigured_root(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    write_real(share / "a.md", "contenido alpha")
    provider = NetworkProvider(roots=[share])

    items = list(provider.iter_files(tmp_path))

    assert items == [
        ProviderError("network", tmp_path, "root is not a configured network source")
    ]


def test_network_provider_reports_disconnected_root_as_error(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    provider = NetworkProvider(roots=[share])
    share.rmdir()

    items = list(provider.iter_files(share))

    assert len(items) == 1
    assert isinstance(items[0], ProviderError)
    assert items[0].provider == "network"


def test_removable_provider_requires_attachment(tmp_path: Path) -> None:
    drive = tmp_path / "usb"
    document = write_real(drive / "doc.md", "contenido extraible")
    provider = RemovableProvider(roots=[drive])
    assert provider.available() is True
    document.unlink()
    drive.rmdir()  # the card was pulled
    assert provider.available() is False


def test_removable_provider_iter_files_scans_mounted_drive(tmp_path: Path) -> None:
    drive = tmp_path / "usb"
    write_real(drive / "doc.md", "contenido extraible")
    provider = RemovableProvider(roots=[drive])

    items = [
        item for item in provider.iter_files(drive)
        if isinstance(item, ProviderFile)
    ]

    assert len(items) == 1
    assert items[0].provider == "removable"


def test_removable_provider_refuses_unconfigured_root(tmp_path: Path) -> None:
    drive = tmp_path / "usb"
    drive.mkdir()
    provider = RemovableProvider(roots=[drive])

    items = list(provider.iter_files(tmp_path))

    assert items == [
        ProviderError("removable", tmp_path, "root is not a configured removable source")
    ]


# -- indexing through providers --------------------------------------------------------


def test_index_root_through_provider_writes_provider_source(tmp_path: Path) -> None:
    real = write_real(tmp_path / "real" / "nota.md", "agenda secreta")
    provider = FakeProvider([make_file(real)])
    database = SearchDatabase(tmp_path / "index.db")

    stats = Indexer(database).index_root(tmp_path / "real", provider=provider)

    assert stats.created == 1
    with database.connect() as connection:
        row = connection.execute(
            "SELECT source, path FROM documents"
        ).fetchone()
    assert row["source"] == "fake"
    assert row["path"] == str(real.resolve())
    results = SearchEngine(database).search("agenda")
    assert [r.name for r in results] == ["nota.md"]


def test_index_root_through_provider_reindexes_unstable_metadata(tmp_path: Path) -> None:
    real = write_real(tmp_path / "real" / "nota.md", "version uno alpha")
    provider = FakeProvider([make_file(real)])
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(tmp_path / "real", provider=provider)

    real.write_text("version dos beta", encoding="utf-8")
    changed = make_file(real, size=real.stat().st_size, mtime_ns=real.stat().st_mtime_ns)
    stats = indexer.index_root(tmp_path / "real", provider=FakeProvider([changed]))

    assert stats.updated == 1
    assert [r.name for r in SearchEngine(database).search("beta")] == ["nota.md"]
    assert SearchEngine(database).search("alpha") == []


def test_index_root_through_provider_deletes_disappeared_files(tmp_path: Path) -> None:
    real = write_real(tmp_path / "real" / "nota.md", "contenido bjt")
    provider = FakeProvider([make_file(real)])
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(tmp_path / "real", provider=provider)

    real.unlink()
    stats = indexer.index_root(tmp_path / "real", provider=FakeProvider([]))

    assert stats.deleted == 1
    assert SearchEngine(database).search("bjt") == []


def test_index_root_through_provider_counts_permission_errors(tmp_path: Path) -> None:
    provider = FakeProvider(
        [ProviderError("fake", tmp_path / "bloqueado.md", "PermissionError: acceso denegado")]
    )
    database = SearchDatabase(tmp_path / "index.db")

    stats = Indexer(database).index_root(tmp_path, provider=provider)

    assert stats.errors == 1


def test_index_root_through_provider_indexes_untrusted_metadata(tmp_path: Path) -> None:
    hostile = write_real(tmp_path / "real" / "a'b; --.md", "contenido bjt")
    provider = FakeProvider([make_file(hostile)])
    database = SearchDatabase(tmp_path / "index.db")

    stats = Indexer(database).index_root(tmp_path / "real", provider=provider)

    assert stats.created == 1
    assert stats.extraction_errors == 0
    results = SearchEngine(database).search("bjt")
    assert [r.name for r in results] == ["a'b; --.md"]


def test_index_root_through_provider_counts_ignored_paths(tmp_path: Path) -> None:
    from universal_search.providers.base import IgnoredPath

    provider = FakeProvider([IgnoredPath(tmp_path / "skip.md", False)])
    database = SearchDatabase(tmp_path / "index.db")

    stats = Indexer(database).index_root(tmp_path, provider=provider)

    assert stats.ignored == 1


def test_two_providers_can_own_the_same_path(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    real = write_real(shared / "nota.md", "contenido compartido")
    first = FakeProvider([make_file(real)], key="fake-a")
    second = FakeProvider([make_file(real)], key="fake-b")
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(shared, provider=first)
    indexer.index_root(shared, provider=second)

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT source, path FROM documents ORDER BY source"
        ).fetchall()
        fts_rows = connection.execute(
            "SELECT COUNT(*) FROM documents_fts"
        ).fetchone()[0]
    assert [(row["source"], row["path"]) for row in rows] == [
        ("fake-a", str(real)),
        ("fake-b", str(real)),
    ]
    assert fts_rows == 2
    assert len(SearchEngine(database).search("compartido")) == 2


def test_index_sources_isolates_failing_provider(tmp_path: Path) -> None:
    working_root = tmp_path / "working"
    write_real(working_root / "ok.md", "contenido sano")
    broken_root = tmp_path / "broken"
    write_real(broken_root / "x.md", "contenido roto")
    write_real(broken_root / "y.md", "contenido roto dos")
    failing = FakeProvider(
        [make_file(broken_root / "x.md"), make_file(broken_root / "y.md")],
        fail_after=1,
    )
    working = FakeProvider([make_file(working_root / "ok.md")])
    database = SearchDatabase(tmp_path / "index.db")

    stats = Indexer(database).index_sources(
        [(failing, broken_root), (working, working_root)]
    )

    assert stats.errors >= 1  # the failing provider cost only its own pass
    assert stats.created == 2  # partial progress kept + healthy provider indexed
    assert [r.name for r in SearchEngine(database).search("sano")] == ["ok.md"]


def test_index_sources_enumeration_failure_preserves_existing_rows(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_real(root / "nota.md", "contenido previo")
    indexer = Indexer(SearchDatabase(tmp_path / "index.db"))
    indexer.index_root(root, provider=FakeProvider([make_file(root / "nota.md")]))

    indexer.index_root(root, provider=FakeProvider([make_file(root / "nota.md")], fail_after=1))

    # The half-finished pass must not delete what it failed to see.
    assert [r.name for r in SearchEngine(indexer.database).search("previo")] == ["nota.md"]


def test_index_root_through_provider_honours_cancel_token(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    token = CancelToken()
    token.cancel()  # already cancelled before the pass starts
    provider = FakeProvider([make_file(root / "a.md")])
    database = SearchDatabase(tmp_path / "index.db")

    stats = Indexer(database).index_root(root, provider=provider, cancel=token)

    assert stats.created == 0
    assert stats.deleted == 0  # a cancelled pass never deletes


# -- (source, path) uniqueness migration -------------------------------------------------


_V6_DOCUMENTS_DDL = """
CREATE TABLE documents (
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
)
"""


def make_legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(_V6_DOCUMENTS_DDL)
        connection.execute(
            "INSERT INTO documents (id, source, path, name, extension, size)"
            " VALUES ('d1', 'local', 'C:/uni/a.md', 'a.md', '.md', 10)"
        )
        connection.execute(
            "INSERT INTO documents (id, source, path, name, extension, size)"
            " VALUES ('d2', 'onedrive', 'C:/uni/b.md', 'b.md', '.md', 20)"
        )
        connection.commit()
    finally:
        connection.close()


def test_migration_v6_to_v7_moves_uniqueness_to_source_path(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    make_legacy_database(path)

    database = SearchDatabase(path)
    with database.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        rows = connection.execute(
            "SELECT id, source, path FROM documents ORDER BY id"
        ).fetchall()
        assert [(row["id"], row["source"], row["path"]) for row in rows] == [
            ("d1", "local", "C:/uni/a.md"),
            ("d2", "onedrive", "C:/uni/b.md"),
        ]
        index = connection.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'index' AND name = 'documents_source_path'"
        ).fetchone()
        assert index is not None
        # Same path, new provider: allowed now.
        connection.execute(
            "INSERT INTO documents (id, source, path, name, extension, size)"
            " VALUES ('d3', 'network', 'C:/uni/a.md', 'a.md', '.md', 10)"
        )
        connection.commit()
        # The canonical (source, path) pair is still unique.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO documents (id, source, path, name, extension, size)"
                " VALUES ('d4', 'network', 'C:/uni/a.md', 'a.md', '.md', 10)"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 3


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    make_legacy_database(path)
    first = SearchDatabase(path).connect()
    first.close()

    with SearchDatabase(path).connect() as connection:
        rows = connection.execute(
            "SELECT id FROM documents ORDER BY id"
        ).fetchall()
        assert [row["id"] for row in rows] == ["d1", "d2"]


def test_fresh_database_enforces_source_path_uniqueness(tmp_path: Path) -> None:
    from dataclasses import replace

    from universal_search.domain.document import Document

    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    local = Document(
        id="local-id", source=SourceKind.LOCAL, path=Path("C:/docs/report.md"),
        name="report.md", extension=".md", size=9,
        created_at=None, modified_at=None, content="needle local",
        content_hash=None,
    )
    indexer.upsert(local)
    indexer.upsert(
        replace(local, id="network-id", source=SourceKind.NETWORK,
                content="needle network")
    )

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 2
        stored_path = str(Path("C:/docs/report.md"))  # same form str(Path) stores
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO documents (id, source, path, name, extension, size)"
                " VALUES ('dup', 'network', ?, 'report.md', '.md', 9)",
                (stored_path,),
            )


# -- query language accepts the new source kinds -----------------------------------------


def test_query_language_accepts_new_source_kinds() -> None:
    from universal_search.query import parse_query
    from universal_search.query.nodes import Filter

    for kind in ("network", "removable"):
        filters: list[Filter] = []

        def walk(node) -> None:
            if isinstance(node, Filter):
                filters.append(node)
            for attr in ("items", "operand"):
                child = getattr(node, attr, None)
                if isinstance(child, tuple):
                    for item in child:
                        walk(item)
                elif child is not None and hasattr(child, "op"):
                    walk(child)

        walk(parse_query(f"source:{kind}"))
        assert [(f.field, f.op, f.value) for f in filters] == [("source", "=", kind)]

    with pytest.raises(Exception, match="source:"):
        parse_query("source:twitter")


# -- performance smoke ---------------------------------------------------------------------


def test_performance_smoke_provider_path(tmp_path: Path) -> None:
    """500 files through the provider path: correctness plus a time bound.

    The bound is a smoke tripwire, not a benchmark: it is generous versus
    the measured provider-path cost on an idle machine (~3 s for 500 small
    files) so only a real regression trips it, even when the CI machine is
    loaded.
    """
    import time

    root = tmp_path / "tree"
    total = 500
    for number in range(total):
        write_real(
            root / f"doc-{number:04d}.md",
            f"topico {number % 6} contenido de prueba numero {number}",
        )
    on_disk = len(list(root.iterdir()))
    database = SearchDatabase(tmp_path / "index.db")
    provider = LocalProvider()

    started = time.perf_counter()
    stats = Indexer(database).index_root(root, provider=provider)
    elapsed = time.perf_counter() - started

    assert stats.created == on_disk == total
    assert stats.errors == 0
    assert elapsed < 60.0, f"provider-path indexing too slow: {elapsed:.1f}s"
    assert len(SearchEngine(database).search("topico", limit=total)) == total
