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

Phase 026 extends the corpus with the failure classes a semantic layer
would have to fix, so the lexical baseline can be measured against them:
a synonym document (``transistor-bipolar`` never says "BJT"), a
voltaje/tension synonym pair, an accented variant (``polarización`` vs the
unaccented query), a stopword-heavy paraphrase, a short query with a
single-character term, a malformed binary ``.md`` that is findable by name
only, and two more unrelated-domain distractors.
"""

import io
import os
import zipfile
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


# -- phase 045: binary formats, built deterministically ----------------------
# The corpus already had a `.pdf`, but it was a deliberately unreadable blob
# (`content is None`), so the one binary workload the phase lists was a
# metadata-only case that never exercised the extractor at all. Code, Office
# and *readable* PDF had no representation whatsoever, even though all three
# extractors ship. A workload that is not in the corpus cannot regress, and a
# workload that cannot regress is not a workload that is working.
#
# Three builders, because `CorpusDocument.raw` is the existing mechanism for
# "write these exact bytes". Each is deterministic down to the ZIP timestamps,
# which matters because `corpus_hash()` feeds the committed baseline: a corpus
# that hashes differently on every run makes every ranking measurement
# incomparable with the last one.
#
# Nothing here is a fixture blob. A PDF is a real, minimal, well-formed
# document; a DOCX/XLSX is a real OOXML package. If the extractor stops reading
# them, the corpus stops indexing them and the quality gate says so -- which is
# the whole point of putting them in.

_ZIP_TIMESTAMP = (2024, 1, 1, 0, 0, 0)


def pdf_bytes(lines: tuple[str, ...]) -> bytes:
    """A minimal but genuinely readable PDF: one page, Helvetica, Tj per line.

    A hand-built object table with a real cross-reference table, because
    `pypdf` is a real parser and this has to satisfy it rather than a
    convenient subset.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    body = ["BT", "/F1 12 Tf", "72 720 Td", "16 TL"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        body.append(f"({escaped}) Tj")
        body.append("T*")
    body.append("ET")
    stream = "\n".join(body).encode("latin-1", "replace")
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{number} 0 obj\n".encode("ascii") + payload + b"\nendobj\n")
    start_xref = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{start_xref}\n%%EOF\n".encode("ascii")
    )
    return buffer.getvalue()


_CT_DOCX = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_RELS_ROOT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{target}"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

_CORE_PROPERTIES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>{title}</dc:title>
</cp:coreProperties>"""

_CT_XLSX = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _zip(members: tuple[tuple[str, str], ...]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            # Fixed permission bits: the default carries the creating process's
            # umask into the archive, which would make two machines disagree.
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return buffer.getvalue()


def docx_bytes(paragraphs: tuple[tuple[str, str], ...], title: str = "") -> bytes:
    """A minimal WordprocessingML package carrying real paragraphs."""
    body = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<w:document xmlns:w="{_W_NS}"><w:body>',
    ]
    for style, text in paragraphs:
        body.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
        )
    body.append("</w:body></w:document>")
    return _zip((
        ("[Content_Types].xml", _CT_DOCX),
        ("_rels/.rels", _RELS_ROOT.format(target="word/document.xml")),
        ("docProps/core.xml", _CORE_PROPERTIES.format(title=title)),
        ("word/document.xml", "".join(body)),
    ))


def xlsx_bytes(sheet_name: str, rows: tuple[tuple[str, ...], ...]) -> bytes:
    """A minimal SpreadsheetML package: one sheet, shared strings, inline rows."""
    strings: list[str] = []
    index: dict[str, int] = {}
    for row in rows:
        for cell in row:
            if cell not in index:
                index[cell] = len(strings)
                strings.append(cell)
    shared = (
        f'<sst xmlns="{_S_NS}" count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{cell}</t></si>" for cell in strings)
        + "</sst>"
    )
    body = []
    for row_number, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{chr(65 + column)}{row_number}" t="s">'
            f"<v>{index[cell]}</v></c>"
            for column, cell in enumerate(row)
        )
        body.append(f'<row r="{row_number}">{cells}</row>')
    sheet = f'<worksheet xmlns="{_S_NS}"><sheetData>{"".join(body)}</sheetData></worksheet>'
    workbook = (
        f'<workbook xmlns="{_S_NS}" xmlns:r="{_R_NS}"><sheets>'
        f'<sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    return _zip((
        ("[Content_Types].xml", _CT_XLSX),
        ("_rels/.rels", _RELS_ROOT.format(target="xl/workbook.xml")),
        ("xl/workbook.xml", workbook),
        ("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS),
        ("xl/sharedStrings.xml", shared),
        ("xl/worksheets/sheet1.xml", sheet),
    ))


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    """One synthetic file: what it is, and how relevant it may look.

    ``content`` is the file text, or ``None`` for a binary the extractors
    cannot read. ``raw`` (phase 026) writes exact bytes instead — used for
    the malformed document, whose body is binary garbage the text
    extractor reads as replacement characters.
    """

    id: str
    path: str
    content: str | None
    age_days: int
    raw: bytes | None = None


@dataclass(frozen=True, slots=True)
class LabelledQuery:
    """A query plus the document ids a user would call relevant.

    An empty ``relevant`` set marks the "must retrieve nothing" case.
    ``failure_class`` (phase 026) tags the failure class a query is meant
    to measure (``synonym``, ``paraphrase``, ``morphological``), so the
    baseline report can group the failures it records.
    """

    query: str
    relevant: frozenset[str]
    note: str
    failure_class: str = ""
    known_limitation: str = ""
    """``known_limitation`` (phase 045) marks a query this build is *documented*
    not to answer, and carries the reason.

    It exists because the two alternatives are both worse: a red gate nobody
    can close, or a corpus case quietly deleted. A declared limitation is
    still measured, still diagnosed, still named in every report and still
    challenged by the quality gate -- what it changes is that the exact-match
    threshold is computed over what the product claims to do rather than over
    everything anybody thought to try.
    """


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
    # -- phase 026: the failure classes a semantic layer must fix ----------
    # A document about BJT that never uses the acronym: the synonym case.
    # Query "BJT" cannot reach it through any lexical signal.
    CorpusDocument(
        id="transistor-bipolar",
        path="electronica/transistores/transistor_bipolar.md",
        age_days=150,
        content=(
            "El transistor de union bipolar es un dispositivo de tres "
            "terminales llamados base, emisor y colector. La ganancia de "
            "corriente Beta relaciona la corriente de colector con la "
            "corriente de base. La union base-emisor se polariza en directa "
            "mientras la union base-colector se polariza en inversa."
        ),
    ),
    # voltaje/tension synonym pair: this document says "tension", the query
    # says "voltaje" — no shared token, so the AND of the query fails.
    CorpusDocument(
        id="tension-base-emisor",
        path="electronica/notas/tension_base_emisor.md",
        age_days=70,
        content=(
            "La tension entre base y emisor de un transistor determina la "
            "corriente de colector. Un valor tipico de 0.7 V indica silicio "
            "y unos 0.2 V indica germanio."
        ),
    ),
    # Accented variant: the query "polarizacion" (unaccented, as most users
    # type) must reach a document that only exists in accented form. FTS5's
    # unicode61 tokenizer case-folds but does not strip diacritics.
    CorpusDocument(
        id="polarizacion-acentuada",
        path="electronica/apuntes/polarizacion_acentuada.md",
        age_days=200,
        content=(
            "La polarización del punto de operación se analiza con la recta "
            "de carga. El análisis de pequeña señal requiere conocer el "
            "punto Q y la tensión térmica del semiconductor."
        ),
    ),
    # Paraphrase target: the query "como se determina el punto de trabajo"
    # shares only function words with this document, so the lexical AND of
    # every query term fails even though it is exactly about that.
    CorpusDocument(
        id="calculo-punto-operacion",
        path="cursos/practicas/calculo_punto_operacion.md",
        age_days=300,
        content=(
            "Como calcular el punto de operacion de un transistor bipolar: "
            "se iguala la recta de carga a la curva caracteristica y se "
            "resuelve el punto Q resultante."
        ),
    ),
    # Malformed content: a .md whose body is binary garbage. The indexer
    # must survive it (extraction errors are data) and the document must
    # stay findable by its name alone.
    CorpusDocument(
        id="malformed",
        path="datos/malformed/archivo_roto.md",
        age_days=10,
        content=None,
        raw=b"\x00\x01\x02\x03\x04\xff\xfe\xfd\xfc\xfb\xfa"
            b"\x00\x00\x00\x80\x81\x82\x83\x84\x85",
    ),
    # Unrelated-domain distractors: must never surface for technical queries.
    CorpusDocument(
        id="futbol",
        path="personal/deportes/futbol.md",
        age_days=5,
        content=(
            "Partido de futbol entre los equipos del barrio. El delantero "
            "marco dos goles en la segunda parte."
        ),
    ),
    CorpusDocument(
        id="viajes",
        path="personal/viajes/bruselas.md",
        age_days=20,
        content=(
            "Viaje a Bruselas: visita al Atomium, al Grand Place y al museo "
            "del Cromatico. El vuelo sale el lunes por la manana."
        ),
    ),

    # -- phase 045: the workloads the corpus did not represent ---------------
    # Code. Nothing in the corpus was source code, so `read_text`'s code
    # extensions were never exercised by a measurement.
    CorpusDocument(
        id="codigo-ebers",
        path="codigo/electronica/ebers_moll.py",
        age_days=18,
        content=(
            '"""Ebers-Moll helpers."""\n'
            "\n"
            "\n"
            "def polarizar_transistor(vcc, is_mA, beta, vt=0.02585):\n"
            '    """Return (ib_mA, ic_mA, vbe_mV) for a desired collector current."""\n'
            "    ic = is_mA\n"
            "    ib = ic / beta\n"
            "    vbe = vt * 1e3 * __import__('math').log(1 + ic / ib)\n"
            "    return ib, ic, vbe\n"
            "\n"
            "\n"
            "def vbe_de_saturacion(vcc, ic_mA, rce=0.2):\n"
            "    vbe = vcc - ic_mA * rce\n"
            "    return vbe\n"
        ),
    ),
    CorpusDocument(
        id="codigo-adc",
        path="codigo/lab3/instrumento.c",
        age_days=7,
        content=(
            "/*lectura del convertidor analogico digital del espectrometro*/\n"
            "#include <stdint.h>\n"
            "\n"
            "uint16_t lectura_adc(void) {\n"
            "    return (uint16_t)(ADC >> 4);\n"
            "}\n"
        ),
    ),

    # Office. The extractor has shipped since phase 025 and the corpus never
    # once asked it to read anything.
    CorpusDocument(
        id="entrega-docx",
        path="cursos/entregas/entrega_final.docx",
        age_days=30,
        content=None,
        raw=docx_bytes((
            ("Heading1", "Entrega final: analisis del amplificador de emisor comun"),
            ("Normal", "Se analiza la ganancia de tension del amplificador de "
                       "emisor comun y su punto de reposo."),
            ("Normal", "Se concludes que el bias punto de trabajo debe fijarse "
                       "lejos de la saturacion."),
        ), title="Entrega final amplificador"),
    ),
    CorpusDocument(
        id="inventario-xlsx",
        path="finanzas/inventario.xlsx",
        age_days=12,
        content=None,
        raw=xlsx_bytes("Almacen", (
            ("componente", "referencia", "stock"),
            ("transistor bipolar", "2N2222", "40"),
            ("resistencia de base", "10k", "120"),
            ("condensador de polarizacion", "100u", "75"),
        )),
    ),

    # A PDF whose text is actually readable. The corpus's only `.pdf` before
    # this was `content is None`: a metadata-only case that proved the *name*
    # was indexed and never that the extractor worked.
    CorpusDocument(
        id="manual-pdf",
        path="electronica/manuales/practica3_polarizacion.pdf",
        age_days=55,
        content=None,
        raw=pdf_bytes((
            "Practica 3: polarizacion del transistor bipolar",
            "El punto de trabajo se ajusta con el modelo Ebers-Moll.",
            "Medir VBE en el osciloscopio y comparar con el valor teorico.",
            "Entrega: informe de la practica antes del proximo lunes.",
        )),
    ),

    # Multilingual. Everything above is Spanish. `unicode61` folds diacritics,
    # so accented Spanish is not really a multilingual test; these are.
    CorpusDocument(
        id="biasing-en",
        path="lectures/transistor_biasing.md",
        age_days=95,
        content=(
            "# Transistor biasing\n\n"
            "Fixed bias sets the quiescent operating point with two resistors. "
            "The collector current depends on the supply voltage and the base "
            "resistor, and the emitter resistor provides thermal stability."
        ),
    ),
    CorpusDocument(
        id="aop-fr",
        path="cours/amplificateur_operationnel.md",
        age_days=140,
        content=(
            "# Amplificateur operationnel\n\n"
            "Le gain de tension en montage non inverseur vaut un plus le "
            "rapport des resistances. La saturation depend de la tension "
            "d'alimentation et du gain demande."
        ),
    ),
    # A script the Unicode tokenizer has no word boundaries for. Kept on
    # purpose: if it cannot be found, that is a measured fact about the
    # corpus and the diagnosis has to be able to say *why*.
    CorpusDocument(
        id="notas-ja",
        path="lecturas/katakana_notes.md",
        age_days=60,
        content=(
            "# \u30c6\u30b9\u30c8\u306b\u3064\u3044\u3066\n\n"
            "\u96fb\u6d41\u306e\u5834\u5408\u306b\u306f\u30c6\u30b9\u30c8\u3092\u66f8\u304d\u8fbc\u3093\u3067\u3002"
            "\n"
        ),
    ),

    # A second duplicated pair. One pair could be a fluke; the question the
    # phase asks about duplicated material is whether a tie is broken the same
    # way every time, which needs more than one instance to look at.
    CorpusDocument(
        id="algebra-original",
        path="notas/algebra/resumen_algebra.md",
        age_days=800,
        content="resumen de algebra lineal: espacios vectoriales y matrices",
    ),
    CorpusDocument(
        id="algebra-copia",
        path="zzz-almacen/copia/resumen_algebra.md",
        age_days=800,
        content="resumen de algebra lineal: espacios vectoriales y matrices",
    ),

    # Filenames that are nothing but abbreviations, which is how real course
    # material is actually named and the one thing a name-based signal has to
    # survive.
    CorpusDocument(
        id="t6-abrev",
        path="electronica/notas/T6_BJT_Apuntes.md",
        age_days=220,
        content="apuntes del tema 6: el transistor bipolar como amplificador",
    ),
    CorpusDocument(
        id="lab3-abrev",
        path="cursos/lab_3_nodos_activos.md",
        age_days=210,
        content=(
            "practica 3: nodos activos en el amplificador diferencial, "
            "ganancia y margen de fase"
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
            "transistor-bipolar",
            # Phase 045. The name literally contains the acronym and the body
            # says "transistor bipolar". Its absence from this set was a
            # labelling gap, and it only became visible because the corpus
            # gained a document that could be judged against it.
            "t6-abrev",
        }),
        note=(
            "single term: filename, content, binary, path-only and "
            "synonym matches (transistor-bipolar never says 'BJT')"
        ),
        failure_class="synonym",
    ),
    LabelledQuery(
        query='"ebers moll"',
        relevant=frozenset({
            "bjt-modelo", "bjt-amplificador", "ebers-exacto", "ebers-lejos",
            # Phase 045: the implementation file is named after the model and
            # the lab manual cites it by name. Both are answers to this query.
            "codigo-ebers", "manual-pdf",
        }),
        note="exact phrase must win over the same words far apart",
    ),
    LabelledQuery(
        query="ebers moll",
        relevant=frozenset({
            "bjt-modelo", "bjt-amplificador", "ebers-exacto", "ebers-lejos",
            "codigo-ebers", "manual-pdf",
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
        relevant=frozenset({
            "informe", "bitacora", "informe-etiqueta",
            # Phase 045: the PDF's last line is "informe de la practica".
            "manual-pdf",
        }),
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
        relevant=frozenset({
            "bjt-modelo", "bjt-amplificador", "bjt-notas",
            "polarizacion-acentuada",
            # Phase 045: a manual titled "polarizacion del transistor" and a
            # spreadsheet whose row reads "condensador de polarizacion". Both
            # outranked the original set and both are genuine answers.
            "manual-pdf", "inventario-xlsx",
        }),
        note=(
            "content-heavy match with no filename help, plus an accented "
            "variant: unicode61 folds diacritics so it IS retrieved, but "
            "the Python ranker scores it lower (mild ranking effect)"
        ),
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
        relevant=frozenset({
            "bjt-datasheet",
            # Phase 045: a second, readable PDF. The filter-only query was
            # written when the corpus had exactly one PDF, so "type:pdf"
            # silently meant "that one PDF" instead of "any PDF".
            "manual-pdf",
        }),
        note=(
            "filter-only query: no text, recency order. Its relevance set is "
            "every PDF in the corpus, which only became true once there was "
            "more than one"
        ),
    ),
    LabelledQuery(
        query="zzz no existe",
        relevant=frozenset(),
        note="must retrieve nothing (empty relevance set)",
    ),
    # -- phase 026: the measured failure classes --------------------------
    LabelledQuery(
        query="voltaje base emisor",
        relevant=frozenset({"bjt-notas", "tension-base-emisor"}),
        note="synonym voltaje/tension: the lexical AND fails on 'voltaje'",
        failure_class="synonym",
    ),
    LabelledQuery(
        query="como se determina el punto de trabajo",
        relevant=frozenset({
            "bjt-modelo", "bjt-notas", "calculo-punto-operacion",
        }),
        note=(
            "paraphrase: stopword-heavy, the lexical AND of every term "
            "fails even though the documents are about exactly this"
        ),
        failure_class="paraphrase",
    ),
    LabelledQuery(
        query="punto Q",
        relevant=frozenset({
            "bjt-modelo", "calculo-punto-operacion", "polarizacion-acentuada",
        }),
        note=(
            "short query with a single-character term: every document that "
            "names 'punto Q' explicitly"
        ),
    ),
    LabelledQuery(
        query="archivo_roto",
        relevant=frozenset({"malformed"}),
        note="malformed content: name-only match over a binary body",
    ),
    LabelledQuery(
        query="receta paella",
        relevant=frozenset({"paella"}),
        note=(
            "morphological: 'receta' (singular) vs 'recetas' (plural path) "
            "— the AND fails even though the recipe is exactly this"
        ),
        failure_class="morphological",
    ),

    # -- phase 045: one query per workload the corpus did not represent ------
    LabelledQuery(
        query="polarizar_transistor",
        relevant=frozenset({"codigo-ebers"}),
        note=(
            "code: an identifier lives in source, not in prose. The name is "
            "camel/snake mixed and only the exact spelling retrieves it"
        ),
        failure_class="filename/path",
    ),
    LabelledQuery(
        query="lectura_adc",
        relevant=frozenset({"codigo-adc"}),
        note="code: a C function, retrieved by its name from a comment and a definition",
    ),
    LabelledQuery(
        query="entrega final",
        relevant=frozenset({"entrega-docx"}),
        note="office: DOCX body text, plus the core title as a second chance",
    ),
    LabelledQuery(
        query="condensador de polarizacion",
        relevant=frozenset({"inventario-xlsx"}),
        note="office: XLSX shared strings, nothing in the file name to help",
    ),
    LabelledQuery(
        query="punto de reposo",
        relevant=frozenset({"entrega-docx"}),
        note=(
            "extraction: a phrase that exists ONLY inside the DOCX. If the "
            "office extractor regressed, this is the query that notices"
        ),
    ),
    LabelledQuery(
        query="osciloscopio",
        relevant=frozenset({"manual-pdf"}),
        note=(
            "extraction: a word that exists ONLY inside a real PDF's text "
            "layer. The corpus's previous PDF was unreadable by design"
        ),
    ),
    LabelledQuery(
        query="transistor biasing",
        relevant=frozenset({"biasing-en"}),
        note="multilingual: an English document reached with an English query",
    ),
    LabelledQuery(
        query="gain de tension",
        relevant=frozenset({"aop-fr"}),
        note=(
            "multilingual: a French document. The query is French too, so "
            "this measures tokenisation, not translation"
        ),
    ),
    LabelledQuery(
        query="resumen algebra",
        relevant=frozenset({"algebra-original", "algebra-copia"}),
        note=(
            "duplicated material: byte-identical pair with different ages. "
            "Both must come back; the tie-break is the ranking's business"
        ),
    ),
    LabelledQuery(
        query="T6 BJT Apuntes",
        relevant=frozenset({"t6-abrev"}),
        note=(
            "filename/path: a name that is nothing but abbreviations, matched "
            "term by term because no single term is the file's identity"
        ),
    ),
    LabelledQuery(
        query="nodos activos",
        relevant=frozenset({"lab3-abrev"}),
        note=(
            "filename/path: 'lab_3' in the name is an abbreviation the query "
            "cannot use, so only the descriptive half of the name can help"
        ),
    ),
    LabelledQuery(
        query="\u30c6\u30b9\u30c8",
        relevant=frozenset({"notas-ja"}),
        note=(
            "extraction: a script with no whitespace word boundaries. Kept on "
            "purpose -- whether it is found is a measured fact, and the "
            "diagnosis has to be able to say why either way"
        ),
        failure_class="extraction",
        known_limitation=(
            "CJK is not segmented by the unicode61 tokenizer this index uses: "
            "\u30c6\u30b9\u30c8\u306b\u3064\u3044\u3066 is indexed as ONE token, so a "
            "substring query can never reach it. Measured, not assumed: the "
            "trigram tokenizer answers this query and then FAILS a two-"
            "character query in the same document, and it would change the "
            "tokenisation of all 30 corpus queries. Quality gate Q13 measures "
            "that trade on every run."
        ),
    ),
)


def build(root: Path) -> list[Path]:
    """Materialise :data:`DOCUMENTS` under ``root``; return their paths."""
    written: list[Path] = []
    for document in DOCUMENTS:
        path = root / document.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if document.raw is not None:
            # Exact bytes (phase 026): a malformed body the text extractor
            # reads as replacement characters, never as a crash.
            path.write_bytes(document.raw)
        elif document.content is None:
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
