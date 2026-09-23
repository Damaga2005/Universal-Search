"""Phase 008: personal contexts, related terms, explainable boosts, usage privacy."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from universal_search.appconfig import AppConfig, AppPaths
from universal_search.context import (
    Context,
    configured_roots,
    context_boost_for,
    context_from_dict,
    context_to_dict,
    expansion_terms,
    get_context,
    load_contexts,
    usage_boost_from,
    with_active_context,
    with_context,
    with_context_removed,
)
from universal_search.domain.document import Document, SourceKind, document_id_for
from universal_search.gui.services import SearchService
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.ranking import (
    ACTIVATED_CONTEXT_WEIGHT,
    ACTIVATED_USAGE_WEIGHT,
    DEFAULT_WEIGHTS,
    Candidate,
    Ranker,
)
from universal_search.index.search import SearchEngine


# -- helpers -----------------------------------------------------------------

def make_context(**overrides) -> Context:
    raw = {"name": "Universidad", **overrides}
    context = context_from_dict(raw)
    assert context is not None
    return context


def add_document(indexer: Indexer, path: Path, content: str) -> Document:
    document = Document(
        id=document_id_for(SourceKind.LOCAL, path),
        source=SourceKind.LOCAL,
        path=path,
        name=path.name,
        extension=path.suffix.lower(),
        size=len(content.encode("utf-8")),
        created_at=None,
        modified_at=None,
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    indexer.upsert(document)
    return document


def build_engine(tmp_path: Path, docs: dict[str, str]) -> SearchEngine:
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    for relative, content in docs.items():
        add_document(indexer, tmp_path / relative, content)
    return SearchEngine(database)


# -- configuration model ------------------------------------------------------

def test_context_parsing_normalizes_and_rejects_garbage() -> None:
    assert context_from_dict({}) is None
    assert context_from_dict({"name": "   "}) is None
    assert context_from_dict("nope") is None

    context = context_from_dict(
        {
            "name": " Universidad ",
            "roots": ["C:/uni", ""],
            "subjects": ["Álgebra", "Física"],
            "doc_types": [".PDF", "Md"],
            "preferred_sources": ["OneDrive"],
            "related_terms": {"Fourier": ["Transformada", "  "], "": ["x"]},
            "recency_days": 30,
        }
    )
    assert context is not None
    assert context.name == "Universidad"
    assert context.roots == ("C:/uni",)
    assert context.subjects == ("álgebra", "física")
    assert context.doc_types == ("pdf", "md")
    assert context.preferred_sources == ("onedrive",)
    assert context.related_terms == {"fourier": ("transformada",)}
    assert context.recency_days == 30

    # invalid recency is dropped instead of crashing
    assert context_from_dict({"name": "x", "recency_days": -5}) is not None
    assert context_from_dict({"name": "x", "recency_days": -5}).recency_days is None


def test_context_roundtrip_via_config(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    config = with_context(AppConfig(), make_context(roots=[str(tmp_path / "uni")]))
    config = replace(config, active_context="Universidad", usage_tracking=True)
    config.save(paths)

    loaded = AppConfig.load(paths)
    assert load_contexts(loaded) == load_contexts(config)
    assert loaded.active_context == "Universidad"
    assert loaded.usage_tracking is True
    # a context root is always an indexed root too
    assert str(tmp_path / "uni") in loaded.roots


def test_invalid_context_entries_are_dropped_on_load(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text(
        '{"contexts": [{"name": "Ok"}, {"roots": ["x"]}, "junk"]}',
        encoding="utf-8",
    )
    config = AppConfig.load(paths)
    assert [context.name for context in load_contexts(config)] == ["Ok"]


def test_with_context_merges_and_deduplicates() -> None:
    config = with_context(AppConfig(), make_context(roots=["C:/a"], subjects=["álgebra"]))
    config = with_context(
        config, make_context(roots=["C:/b", "C:/a"], subjects=["física"])
    )
    context = get_context(config, "UNIVERSIDAD")  # case-insensitive lookup
    assert context is not None
    assert context.roots == ("C:/a", "C:/b")
    assert context.subjects == ("álgebra", "física")
    assert config.roots.count("C:/a") == 1


def test_with_context_removed_clears_active_context() -> None:
    config = with_context(AppConfig(), make_context())
    config = with_active_context(config, "Universidad")
    config = with_context_removed(config, "universidad")
    assert config.contexts == ()
    assert config.active_context == ""
    assert get_context(config, "Universidad") is None


def test_with_active_context_validates_unknown_names() -> None:
    config = with_context(AppConfig(), make_context())
    assert with_active_context(config, "Universidad").active_context == "Universidad"
    assert with_active_context(config, "").active_context == ""
    with pytest.raises(ValueError, match="desconocido"):
        with_active_context(config, "Inexistente")


def test_configured_roots_unions_context_roots() -> None:
    config = AppConfig(roots=("C:/base",))
    config = with_context(config, make_context(roots=["C:/uni", "C:/base"]))
    assert configured_roots(config) == ("C:/base", "C:/uni")


# -- query-time signals -------------------------------------------------------

def test_expansion_terms_only_add_related_terms() -> None:
    context = make_context(
        related_terms={"fourier": ["transformada", "serie"], "laplace": []}
    )
    assert expansion_terms(("fourier",), context) == ("transformada", "serie")
    # originals and duplicates never re-enter; unknown terms add nothing
    assert expansion_terms(("fourier", "transformada"), context) == ("serie",)
    assert expansion_terms(("integral",), context) == ()
    assert expansion_terms(("fourier",), None) == ()


def test_context_boost_reasons_and_cap() -> None:
    context = make_context(
        roots=["C:/uni"],
        subjects=["álgebra"],
        doc_types=["pdf"],
        preferred_sources=["onedrive"],
        recency_days=30,
    )
    from datetime import datetime, timedelta, timezone

    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    boost, reasons = context_boost_for(
        context,
        path="C:/uni/Álgebra lineal/apuntes.pdf",
        source="onedrive",
        doc_type="pdf",
        modified_at=recent,
    )
    # 0.5 root + 0.25 subject + 0.15 type + 0.1 source + 0.1 recency = 1.1
    # capped at 1.0 — personalization can never dominate the score.
    assert boost == 1.0
    joined = " ".join(reasons)
    assert "root:C:/uni" in joined
    assert "subject:álgebra" in joined
    assert "type:pdf" in joined
    assert "source:onedrive" in joined
    assert "recency:30d" in joined

    outside, no_reasons = context_boost_for(
        context, path="C:/otros/doc.txt", source="local", doc_type="txt"
    )
    assert outside == 0.0
    assert no_reasons == ()


def test_usage_boost_from_saturates() -> None:
    assert usage_boost_from(0) == 0.0
    assert usage_boost_from(-3) == 0.0
    assert 0.0 < usage_boost_from(1) < usage_boost_from(2) < usage_boost_from(4)
    assert usage_boost_from(4) == pytest.approx(1.0)
    assert usage_boost_from(1000) == 1.0


# -- ranking integrity --------------------------------------------------------

def test_default_weights_keep_the_exact_match_baseline() -> None:
    assert DEFAULT_WEIGHTS.usage == 0.0
    assert DEFAULT_WEIGHTS.context == 0.0
    assert DEFAULT_WEIGHTS.total == 14.0
    assert ACTIVATED_CONTEXT_WEIGHT <= 1.0
    assert ACTIVATED_USAGE_WEIGHT < ACTIVATED_CONTEXT_WEIGHT


def test_contributions_sum_matches_score() -> None:
    ranker = Ranker()
    candidate = Candidate(
        name="memoria final.md",
        path=r"C:\docs\memoria final.md",
        content="memoria final del proyecto",
        source="local",
        modified_at=None,
        bm25_rank=-3.0,
    )
    points, score = ranker.contributions(candidate, ("memoria", "final"))
    assert set(points) == set(DEFAULT_WEIGHTS.__dataclass_fields__)
    assert sum(points.values()) / DEFAULT_WEIGHTS.total == pytest.approx(score)
    assert points["filename_exact"] == pytest.approx(DEFAULT_WEIGHTS.filename_exact)
    assert ranker.score(candidate, ("memoria", "final")) == pytest.approx(score)


# -- recall and ranking behaviour (engine) ------------------------------------

def test_related_terms_improve_recall_without_hiding_exact(tmp_path: Path) -> None:
    engine = build_engine(
        tmp_path,
        {
            "uni/integrales.md": "series de fourier en dos dimensiones",
            "otras/notas.md": "la transformada de laplace convierte ecuaciones",
        },
    )
    context = make_context(related_terms={"fourier": ["transformada"]})

    baseline = [result.name for result in engine.search("fourier")]
    assert baseline == ["integrales.md"]  # synonym alone is not enough

    widened = engine.search("fourier", context=context)
    assert [result.name for result in widened] == ["integrales.md", "notas.md"]
    # the exact/filename match keeps the top spot and the strongest score
    assert widened[0].score > widened[1].score


def test_context_prefers_documents_under_its_roots(tmp_path: Path) -> None:
    engine = build_engine(
        tmp_path,
        {
            "uni/notas.md": "examen de termo aplicada",
            "otro/notas.md": "examen de termo aplicada",
        },
    )
    query = "termo"
    plain = {result.path.parent.name: result.score for result in engine.search(query)}
    assert plain["uni"] == pytest.approx(plain["otro"])  # identical documents

    context = make_context(roots=[str(tmp_path / "uni")])
    boosted = {
        result.path.parent.name: result.score
        for result in engine.search(query, context=context)
    }
    assert boosted["uni"] > boosted["otro"]
    # bounded: the boost moves the score by at most weight / total
    delta = boosted["uni"] - plain["uni"]
    assert 0 < delta <= ACTIVATED_CONTEXT_WEIGHT / (14.0 + ACTIVATED_CONTEXT_WEIGHT)


def test_exact_filename_match_beats_context_preference(tmp_path: Path) -> None:
    """AC: personalization must never overwhelm exact filename/content matches."""
    engine = build_engine(
        tmp_path,
        {
            "otro/memoria final.md": "memoria final del trabajo",
            "uni/temas.md": "memoria final de la asignatura",
        },
    )
    context = make_context(roots=[str(tmp_path / "uni")])

    results = engine.search("memoria final", context=context)
    assert results[0].name == "memoria final.md"  # exact name wins, context active

    in_context = next(r for r in results if r.name == "temas.md")
    plain = next(
        r
        for r in engine.search("memoria final")
        if r.name == "temas.md"
    )
    assert in_context.score > plain.score  # ...but the boost is still felt


def test_explain_breakdown_is_complete_and_consistent(tmp_path: Path) -> None:
    engine = build_engine(
        tmp_path,
        {"uni/termo/notas.md": "examen de termo aplicada"},
    )
    context = make_context(roots=[str(tmp_path / "uni")], subjects=["termo"])

    (result,) = engine.search("termo", context=context, explain=True)
    assert result.explain is not None
    assert set(result.explain) == set(DEFAULT_WEIGHTS.__dataclass_fields__)
    assert result.explain["context"] == pytest.approx(
        ACTIVATED_CONTEXT_WEIGHT * 0.75  # root 0.5 + subject 0.25
    )
    assert any(note.startswith("root:") for note in result.explain_notes)
    assert any(note.startswith("subject:") for note in result.explain_notes)
    # contributions / total == score (context raises the total to 15.0)
    assert sum(result.explain.values()) / (14.0 + ACTIVATED_CONTEXT_WEIGHT) == (
        pytest.approx(result.score)
    )

    plain = engine.search("termo")
    assert plain[0].explain is None  # off by default


# -- local usage learning (privacy) ------------------------------------------

def test_usage_is_disabled_by_default_and_never_records(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    config = AppConfig.load(paths)
    assert config.usage_tracking is False  # privacy: opt-in

    database = SearchDatabase(tmp_path / "index.db")
    add_document(Indexer(database), tmp_path / "doc.md", "contenido")
    service = SearchService(paths=paths)

    service.record_open("whatever", "consulta")
    assert service.engine.usage_rows() == []  # nothing recorded while off

    # data home is the per-user application directory (local-only storage)
    assert service.database.path.parent == paths.home


def test_usage_recording_is_inspectable_and_deletable(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    database = SearchDatabase(tmp_path / "index.db")
    document = add_document(Indexer(database), tmp_path / "doc.md", "contenido")
    service = SearchService(database_path=database.path, paths=paths)
    service.save_config(replace(AppConfig.load(paths), usage_tracking=True))

    service.record_open(document.id, "examen")
    service.record_open(document.id, "examen")

    rows = service.engine.usage_rows()
    assert len(rows) == 2
    assert rows[0]["query"] == "examen"
    assert rows[0]["name"] == "doc.md"

    assert service.engine.clear_usage() == 2
    assert service.engine.usage_rows() == []


def test_usage_signal_boosts_opened_result_boundedly(tmp_path: Path) -> None:
    engine = build_engine(
        tmp_path,
        {
            "aaa/notas.md": "practica de quimica",
            "bbb/notas.md": "practica de quimica",
        },
    )
    opened = next(
        result.document_id
        for result in engine.search("practica")
        if str(result.path.parent.name) == "bbb"
    )
    for _ in range(4):
        engine.record_open(opened, "practica")

    plain = {
        str(result.path.parent.name): result.score
        for result in engine.search("practica")
    }
    assert plain["aaa"] == pytest.approx(plain["bbb"])  # learning off: no influence

    learned = {
        str(result.path.parent.name): result.score
        for result in engine.search("practica", usage=True)
    }
    assert learned["bbb"] > learned["aaa"]
    delta = learned["bbb"] - plain["bbb"]
    assert 0 < delta <= ACTIVATED_USAGE_WEIGHT / (14.0 + ACTIVATED_USAGE_WEIGHT)


def test_service_resolves_named_context_override(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths(tmp_path / "home")
    service = SearchService(paths=paths)
    service.save_config(with_context(AppConfig.load(paths), make_context()))

    captured: dict = {}

    def spy(query, limit=50, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(service.engine, "search", spy)

    service.search("algo", context="Universidad")
    assert isinstance(captured["context"], Context)
    assert captured["context"].name == "Universidad"
    assert captured["usage"] is False  # usage stays opt-in

    service.search("algo")  # no active context -> core default
    assert captured["context"] is None
