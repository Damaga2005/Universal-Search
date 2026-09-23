"""Provider and extractor extension contracts (spec 019).

Fake providers and extractors exercise registration, capability
negotiation, failure isolation and identity stability without touching
the filesystem or the real index: the point of the phase is that adding a
source is a *registration*, not a rewrite of the core.
"""

import sqlite3
from contextlib import closing as contextlib_closing
from pathlib import Path

import pytest

from universal_search.domain.document import Document, SourceKind, document_id_for
from universal_search.domain.extraction import ExtractionResult
from universal_search import extractors
from universal_search.providers import registry as provider_registry
from universal_search.providers.base import (
    CAPABILITIES,
    CONTENT,
    ENUMERATE,
    IDENTITY,
    METADATA,
    ProviderRegistry,
)


class FakeNasProvider:
    """A provider for a share it may or may not be able to reach."""

    version = "0.3"
    capabilities = frozenset({ENUMERATE, METADATA, IDENTITY})

    def __init__(self, reachable: bool = True, key: str = "nas") -> None:
        self.reachable = reachable
        self.key = key

    def available(self) -> bool:
        return self.reachable

    def discover(self, root: Path):
        yield Document(
            id=document_id_for(SourceKind.LOCAL, root / "informe.md"),
            source=SourceKind.LOCAL,
            path=root / "informe.md",
            name="informe.md",
            extension=".md",
            size=10,
            content="contenido",
        )


class BrokenProvider:
    """Fails on every call: the registry must report, not propagate."""

    key = "roto"
    version = "1.0"
    capabilities = frozenset({ENUMERATE})

    def available(self) -> bool:
        raise OSError("the share is unreachable")

    def discover(self, root: Path):  # pragma: no cover - never reached
        raise AssertionError("availability already failed")


class NotAProvider:
    key = "invalido"
    version = "1.0"
    capabilities = frozenset({ENUMERATE})


# -- built-in providers --------------------------------------------------------

def test_builtin_providers_are_registered_and_inspectable():
    infos = provider_registry.infos()
    keys = {info.key for info in infos}
    assert {"local", "onedrive"} <= keys
    local = next(info for info in infos if info.key == "local")
    assert local.available is True
    assert set(local.capabilities) == set(CAPABILITIES)
    onedrive = next(info for info in infos if info.key == "onedrive")
    # OneDrive deliberately does not claim content: reading a placeholder
    # would download it.
    assert CONTENT not in onedrive.capabilities
    assert "onedrive" in onedrive.detail or onedrive.detail


def test_registration_is_idempotent():
    first = provider_registry.register_builtins().keys()
    second = provider_registry.register_builtins().keys()
    assert first == second


def test_registration_refuses_duplicates_and_invalid_providers():
    registry = ProviderRegistry()
    registry.register(FakeNasProvider())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeNasProvider())
    with pytest.raises(TypeError):
        registry.register(NotAProvider())

    class Nameless:
        version = "1.0"
        capabilities = frozenset()

        def available(self) -> bool:
            return True

        def discover(self, root: Path):
            return []

    with pytest.raises(ValueError, match="key"):
        registry.register(Nameless())


def test_unknown_capabilities_are_refused():
    class Invented:
        key = "inventado"
        version = "1.0"
        capabilities = frozenset({"leer_el_alma"})

        def available(self) -> bool:
            return True

        def discover(self, root: Path):
            return []

    with pytest.raises(ValueError, match="unknown capabilities"):
        ProviderRegistry().register(Invented())


# -- capability negotiation and failure isolation ------------------------------

def test_capability_negotiation_lists_only_available_providers():
    registry = ProviderRegistry()
    registry.register(FakeNasProvider(reachable=True), kind="network")
    registry.register(
        FakeNasProvider(reachable=False, key="usb"), kind="removable"
    )
    assert registry.for_capability(ENUMERATE) == ("nas",)
    # A provider without the capability is never offered for it.
    assert registry.for_capability(CONTENT) == ()


def test_a_broken_provider_is_reported_unavailable_not_raised():
    registry = ProviderRegistry()
    registry.register(BrokenProvider(), kind="network", detail="share roto")
    info = registry.infos()[0]
    assert info.available is False
    assert "availability check failed" in info.detail
    # And it simply does not appear as usable for any capability.
    assert registry.for_capability(ENUMERATE) == ()


def test_a_new_provider_needs_no_core_rewrite():
    """The contract: registration + protocol, nothing else."""
    registry = ProviderRegistry()
    registry.register(FakeNasProvider(), kind="network", detail="NAS")
    info = registry.infos()[0]
    assert info.key == "nas"
    assert info.version == "0.3"
    assert info.kind == "network"
    # The engine and the ranking do not import providers at all.
    engine_source = (
        Path(__file__).resolve().parents[1]
        / "src" / "universal_search" / "index" / "search.py"
    ).read_text(encoding="utf-8")
    ranking_source = (
        Path(__file__).resolve().parents[1]
        / "src" / "universal_search" / "index" / "ranking.py"
    ).read_text(encoding="utf-8")
    assert "providers" not in engine_source
    assert "providers" not in ranking_source


# -- identities ----------------------------------------------------------------

def test_identity_is_stable_and_paths_are_unique(tmp_path: Path):
    """Identity rules the index really has (spec 019).

    ``documents.path`` is UNIQUE: the index is keyed by absolute path, so
    a file that a cloud provider syncs into a local folder is **one**
    document, not two. Two different sources for the same path would be
    rejected by the schema, and the source is derived from the path
    itself (``source_for_path``) rather than stored twice.
    """
    from universal_search.index.database import SearchDatabase
    from universal_search.providers.onedrive import source_for_path

    path = tmp_path / "notas.md"
    local = document_id_for(SourceKind.LOCAL, path)
    assert local == document_id_for(SourceKind.LOCAL, path)
    # A cloud source would produce a different id, but it can never be
    # stored for the same path — and the index derives the source itself.
    assert document_id_for(SourceKind.ONEDRIVE, path) != local
    assert source_for_path(path) == SourceKind.LOCAL

    with contextlib_closing(SearchDatabase(tmp_path / "ids.db").connect()) as connection:
        connection.execute(
            "INSERT INTO documents(id,source,path,name,extension,size)"
            " VALUES (?,?,?,?,?,?)",
            (local, "local", str(path), "notas.md", ".md", 10),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO documents(id,source,path,name,extension,size)"
                " VALUES (?,?,?,?,?,?)",
                (
                    document_id_for(SourceKind.ONEDRIVE, path), "onedrive",
                    str(path), "notas.md", ".md", 10,
                ),
            )


# -- extractors ----------------------------------------------------------------

def test_extractor_registry_is_inspectable():
    described = {info.key: info for info in extractors.infos()}
    assert set(described) == {"text", "pdf", "office"}
    assert ".pdf" in described["pdf"].extensions
    assert ".docx" in described["office"].extensions
    assert ".md" in described["text"].extensions
    for info in described.values():
        assert info.max_chars > 0
        assert info.note


def test_unsupported_formats_are_never_opened(tmp_path: Path):
    binary = tmp_path / "imagen.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01")
    result = extractors.extract(binary)
    assert result.text is None
    assert result.error is None  # unsupported is not an error
    assert extractors.supports(".png") is False


def test_a_broken_extractor_is_isolated(tmp_path: Path):
    def explode(path: Path) -> ExtractionResult:
        raise RuntimeError("extractor roto")

    original = extractors.EXTRACTORS.get(".xyz")
    extractors.EXTRACTORS[".xyz"] = explode
    try:
        result = extractors.extract(tmp_path / "archivo.xyz")
    finally:
        if original is None:
            extractors.EXTRACTORS.pop(".xyz", None)
        else:  # pragma: no cover - defensive
            extractors.EXTRACTORS[".xyz"] = original
    assert result.text is None
    assert "extractor roto" in (result.error or "")


def test_extractors_never_raise_for_any_file(tmp_path: Path):
    hostile = {
        "roto.pdf": b"%PDF-1.7 roto",
        "vacio.txt": b"",
        "nulos.txt": b"\x00\x00\x00",
        "binario.docx": b"\x50\x4b\x03\x04 rubbish",
    }
    for name, payload in hostile.items():
        path = tmp_path / name
        path.write_bytes(payload)
        result = extractors.extract(path)  # must not raise
        assert result.text is None or isinstance(result.text, str)


# -- CLI -----------------------------------------------------------------------

def test_cli_lists_providers_and_extractors(capsys):
    import unittest.mock as mock

    from universal_search.cli import main

    with mock.patch.object(
        __import__("sys"), "argv", ["universal-search", "extensions"]
    ):
        main()
    out = capsys.readouterr().out
    assert "local" in out and "onedrive" in out
    assert "capabilities:" in out
    assert "extractors:" in out
    assert "runtime plugins are deliberately not supported" in out
