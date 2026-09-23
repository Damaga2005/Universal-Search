"""Labelled evaluation corpus for ranking quality (spec 013).

Deterministic by construction: every file's content is fixed text and
every timestamp is a fixed epoch minus an age in days, so two runs index
byte-identical files and produce comparable orderings. No private
document is involved — the material below is synthetic and was written
for this repository.

Each document declares a stable ``id``, a path relative to the corpus
root, its content (``None`` for a binary the extractors cannot read) and
an age. Each labelled query declares the ids a user would consider
relevant; everything else in the tree is a distractor on purpose, which
is what makes Precision@K mean anything here.

Covered cases: single terms (``BJT``, ``CMOS``, ``MUX``), an exact phrase
(``"ebers moll"``) and the same words without quotes, a filename-exact
match (``informe``), a content-heavy match (``polarizacion``), a path-only
match (``cursos/BJT/``), an accidental generic path match
(``descargas/notas/`` holding an unrelated CV), a long document that must
not win by bulk, a metadata-only binary, a filter-only query, and
unrelated documents that must never surface.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Fixed reference point for every timestamp: 2024-01-01T00:00:00Z. Ages are
# relative to it, so the *difference* between two documents never drifts
# with the wall clock and the ranking stays reproducible.
BASE_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()

# Vocabulary for the long sensor log. It deliberately contains none of the
# query terms, so that document can only match through its two explicit
# mentions of "bjt" (plus its name) — which is exactly what the
# length-normalisation case needs.
LOG_WORDS = (
    "sensor lectura muestra canal umbral periodo estado mascara registro "
    "cola evento marca tiempo real adc gpio pin nivel histeresis"
).split()


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    """One synthetic file: what it is, and how relevant it may look."""

    id: str
    path: str
    content: str | None
    age_days: int


@dataclass(frozen=True, slots=True)
class LabelledQuery:
    """A query plus the document ids a user would call relevant.

    An empty ``relevant`` set marks the "must retrieve nothing" case.
    """

    query: str
    relevant: frozenset[str]
    note: str


def _long_log_mentions(count: int = 4000) -> str:
    """A long log with ``bjt`` mentioned twice, far apart."""
    words = [LOG_WORDS[index % len(LOG_WORDS)] for index in range(count)]
    words[17] = "bjt"
    words[count - 23] = "bjt"
    return " ".join(words)


DOCUMENTS: tuple[CorpusDocument, ...] = (
    CorpusDocument(
        id="bjt-modelo",
        path="electronica/BJT_Ebers_Moll.md",
        age_days=400,
        content=(
            "Modelo Ebers-Moll del transistor BJT. La polarizacion del punto "
            "Q se calcula con la recta de carga. El modelo ebers moll sigue "
            "siendo la referencia para el analisis de pequena senal."
        ),
    ),
    CorpusDocument(
        id="bjt-amplificador",
        path="electronica/amplificadores/Tema_6_BJT.md",
        age_days=30,
        content=(
            "Amplificadores BJT de punto fijo. BJT BJT BJT BJT BJT BJT BJT "
            "BJT BJT BJT BJT BJT BJT. El modelo ebers moll explica la "
            "ganancia de tension del BJT en el punto de polarizacion."
        ),
    ),
    CorpusDocument(
        id="bjt-notas",
        path="personal/notas/bjt_punto_operacion.md",
        age_days=90,
        content=(
            "Notas de clase sobre BJT y polarizacion del punto de operacion. "
            "BJT BJT. La corriente de colector depende de la tension entre "
            "base y emisor."
        ),
    ),
    CorpusDocument(
        id="bjt-log",
        path="datos/sensores/sensor_bjt.log",
        age_days=5,
        content=_long_log_mentions(),
    ),
    CorpusDocument(
        id="bjt-datasheet",
        path="electronica/datasheets/BJT_2N2222.pdf",
        age_days=200,
        content=None,  # binary the PDF extractor cannot read: name-only
    ),
    CorpusDocument(
        id="bjt-carpeta",
        path="cursos/BJT/calculo_matrices.txt",
        age_days=120,
        content=(
            "Calculo de matrices de transferencia para analisis de pequena "
            "senal. Determinacion de Ic, Vbe y Beta en el regimen activo."
        ),
    ),
    CorpusDocument(
        id="cmos-logica",
        path="electronica/cmos/logica_cmos.md",
        age_days=60,
        content=(
            "Logica CMOS con transistores NMOS y PMOS. CMOS consume muy "
            "poca potencia estatica comparada con la familia TTL y es "
            "sensible a la descarga estatica."
        ),
    ),
    CorpusDocument(
        id="cmos-mux",
        path="electronica/cmos/mux_cmos.md",
        age_days=45,
        content=(
            "Multiplexor MUX de cuatro entradas implementado con celdas "
            "CMOS. El selector CMOS controla que entrada llega a la salida."
        ),
    ),
    CorpusDocument(
        id="mux-generico",
        path="electronica/digital/mux_basico.md",
        age_days=80,
        content=(
            "Multiplexor analogico MUX de ocho entradas con transicion suave "
            "entre canales. MUX pasivo con motivo de conduccion definido."
        ),
    ),
    CorpusDocument(
        id="ebers-exacto",
        path="cursos/modelos/ebers_moll.md",
        age_days=500,
        content=(
            "El modelo ebers moll aproxima el transistor bipolar mediante dos "
            "diodos y una fuente de corriente. ebers moll es un modelo de "
            "primer orden muy usado en spice."
        ),
    ),
    CorpusDocument(
        id="ebers-lejos",
        path="cursos/modelos/ebers_moll_apuntes.md",
        age_days=500,
        content=(
            "El modelo del transistor bipolar se explica con detalle en "
            "estas apuntes. ebers aparece en el primer tema junto a la "
            "teoria del punto de operacion; moll se estudia mas adelante, al "
            "analizar los regimenes de trabajo."
        ),
    ),
    CorpusDocument(
        id="notas-generico",
        path="descargas/notas/curriculum_vitae.txt",
        age_days=15,
        content=(
            "Experiencia laboral en mantenimiento industrial y coordinacion "
            "de equipos. Formacion tecnica en electromecanica."
        ),
    ),
    CorpusDocument(
        id="informe",
        path="trabajo/informes/informe_final.md",
        age_days=25,
        content=(
            "Informe final del proyecto de instrumentacion. Resumen, "
            "metodologia, resultados y conclusiones del montaje."
        ),
    ),
    # Evidence profiles that compete: this document *is* about the query
    # term in its content only, while `informe-etiqueta` claims it with its
    # name alone. Which one ranks first is a weighting decision, so the
    # evaluation can measure the boundary instead of assuming it.
    CorpusDocument(
        id="bitacora",
        path="trabajo/bitacora.md",
        age_days=45,
        content=(
            "informe informe informe informe informe informe informe "
            "informe del estado de las mediciones"
        ),
    ),
    CorpusDocument(
        id="informe-etiqueta",
        path="personal/etiquetas/informe.txt",
        age_days=8,
        content="sin detalles adicionales",
    ),
    # A recency tie-break pair: identical content and names, opposite
    # alphabetical and chronological order, so the winner is decided
    # purely by the recency signal (and by the path when it is disabled).
    CorpusDocument(
        id="diagrama-lecturas",
        path="lecturas/diagrama.md",
        age_days=3650,
        content="diagrama de bloques del sistema digital",
    ),
    CorpusDocument(
        id="diagrama-almacen",
        path="zzz-almacen/diagrama.md",
        age_days=10,
        content="diagrama de bloques del sistema digital",
    ),
    CorpusDocument(
        id="presupuesto",
        path="finanzas/presupuesto.csv",
        age_days=70,
        content="concepto,importe\nlicencia,1200\nhardware,3400\nformacion,800",
    ),
    CorpusDocument(
        id="paella",
        path="personal/recetas/paella.md",
        age_days=100,
        content=(
            "Paella valenciana con arroz bomba. Sofreir el sofrito con "
            "azafran antes de incorporar el arroz."
        ),
    ),
    CorpusDocument(
        id="meteorologia",
        path="personal/meteorologia/pronostico.md",
        age_days=2,
        content=(
            "Pronostico para la semana: nubosidad variable, temperaturas "
            "suaves y viento del norte."
        ),
    ),
)

DOCUMENT_IDS: frozenset[str] = frozenset(doc.id for doc in DOCUMENTS)

LABELLED_QUERIES: tuple[LabelledQuery, ...] = (
    LabelledQuery(
        query="BJT",
        relevant=frozenset({
            "bjt-modelo", "bjt-amplificador", "bjt-notas",
            "bjt-log", "bjt-datasheet", "bjt-carpeta",
        }),
        note="single term: filename, content, binary and path-only matches",
    ),
    LabelledQuery(
        query='"ebers moll"',
        relevant=frozenset({
            "bjt-modelo", "bjt-amplificador", "ebers-exacto", "ebers-lejos",
        }),
        note="exact phrase must win over the same words far apart",
    ),
    LabelledQuery(
        query="ebers moll",
        relevant=frozenset({
            "bjt-modelo", "bjt-amplificador", "ebers-exacto", "ebers-lejos",
        }),
        note="same words without quotes: conjunction, order-independent",
    ),
    LabelledQuery(
        query="CMOS",
        relevant=frozenset({"cmos-logica", "cmos-mux"}),
        note="single term over a different technical domain",
    ),
    LabelledQuery(
        query="MUX",
        relevant=frozenset({"cmos-mux", "mux-generico"}),
        note="term shared by two documents of equal relevance",
    ),
    LabelledQuery(
        query="informe",
        relevant=frozenset({"informe", "bitacora", "informe-etiqueta"}),
        note=(
            "filename-exact match competing with a content-only match: "
            "the weighting decides the order"
        ),
    ),
    LabelledQuery(
        query="diagrama",
        relevant=frozenset({"diagrama-lecturas", "diagrama-almacen"}),
        note=(
            "recency tie-break: identical content, opposite alphabetical "
            "and chronological order"
        ),
    ),
    LabelledQuery(
        query="polarizacion",
        relevant=frozenset({"bjt-modelo", "bjt-amplificador", "bjt-notas"}),
        note="content-heavy match with no filename help",
    ),
    LabelledQuery(
        query="notas",
        relevant=frozenset({"bjt-notas"}),
        note="accidental generic path match must not win (descargas/notas/)",
    ),
    LabelledQuery(
        query="presupuesto",
        relevant=frozenset({"presupuesto"}),
        note="unrelated document: control that noise never surfaces",
    ),
    LabelledQuery(
        query="bjt type:txt",
        relevant=frozenset({"bjt-carpeta"}),
        note="text term plus a filter: only the .txt document survives",
    ),
    LabelledQuery(
        query="type:pdf",
        relevant=frozenset({"bjt-datasheet"}),
        note="filter-only query: no text, recency order, score 0.0",
    ),
    LabelledQuery(
        query="zzz no existe",
        relevant=frozenset(),
        note="must retrieve nothing (empty relevance set)",
    ),
)


def build(root: Path) -> list[Path]:
    """Materialise :data:`DOCUMENTS` under ``root``; return their paths."""
    written: list[Path] = []
    for document in DOCUMENTS:
        path = root / document.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if document.content is None:
            # Deterministic bytes an extractor cannot read: the document
            # stays in the index as metadata-only (name and path searchable).
            path.write_bytes(
                b"%PDF-1.7 synthetic " + document.id.encode("ascii") + b" \x00\x01"
            )
        else:
            path.write_text(document.content, encoding="utf-8")
        stamp = BASE_EPOCH - document.age_days * 86400
        os.utime(path, (stamp, stamp))
        written.append(path)
    return written


def ids_by_path(root: Path) -> dict[Path, str]:
    """Map every corpus path (absolute) to its document id."""
    return {(root / document.path).resolve(): document.id
            for document in DOCUMENTS}


def assert_labels_are_consistent() -> None:
    """Every labelled id must exist in the corpus (cheap self-check)."""
    unknown = sorted(
        {
            document_id
            for labelled in LABELLED_QUERIES
            for document_id in labelled.relevant
            if document_id not in DOCUMENT_IDS
        }
    )
    if unknown:
        raise AssertionError(f"labels reference unknown documents: {unknown}")
