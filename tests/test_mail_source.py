"""Phase 033: mail as a searchable source.

The properties under test are the ones that decide whether a mail index is
honest: that what the user searches for is actually indexed, that attachments
are never read, and that one broken part does not lose the message.
"""

from __future__ import annotations

import base64
from pathlib import Path

from universal_search.domain.extraction import (
    ExtractionLimits,
    ExtractionStatus,
)
from universal_search.extractors import extract, infos, supports
from universal_search.extractors.mail import (
    html_to_text,
    read_mail,
    split_mbox,
)

SIMPLE = (
    b"From: Ana Ruiz <ana@example.com>\r\n"
    b"To: Bolt Labs <equipo@example.com>\r\n"
    b"Cc: Luis <luis@example.com>\r\n"
    b"Subject: presupuesto Q3 y revision de proveedores\r\n"
    b"Date: Mon, 14 Sep 2026 10:22:31 +0200\r\n"
    b"Message-ID: <abc123@example.com>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Hola equipo,\r\n\r\nAdjunto el presupuesto del Q3.\r\n"
)

ALTERNATIVE = (
    b"From: Ana Ruiz <ana@example.com>\r\n"
    b"Subject: informe con HTML\r\n"
    b'Content-Type: multipart/alternative; boundary="bnd"\r\n'
    b"\r\n"
    b"--bnd\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"version en texto plano\r\n"
    b"--bnd\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"\r\n"
    b"<html><body><p>version en <b>HTML</b></p>"
    b"<script>alert('no indexar')</script></body></html>\r\n"
    b"--bnd--\r\n"
)


def write(tmp_path: Path, name: str, raw: bytes) -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return target


# -- registration ------------------------------------------------------------

def test_mail_extensions_are_registered():
    for extension in (".eml", ".mbox", ".mbx", ".email"):
        assert supports(extension), extension


def test_msg_is_not_registered():
    """`.msg` is an OLE compound file, not RFC 5322. It is never opened."""
    assert not supports(".msg")


def test_mail_is_inspectable_without_reading_code():
    info = next(item for item in infos() if item.key == "mail")
    assert ".eml" in info.extensions
    assert "attachments never read" in info.note
    assert info.binary_safe is True


# -- what gets indexed --------------------------------------------------------

def test_subject_sender_and_body_are_searchable(tmp_path: Path):
    result = read_mail(write(tmp_path, "correo.eml", SIMPLE))
    assert result.text
    assert "Subject: presupuesto Q3 y revision de proveedores" in result.text
    assert "From: Ana Ruiz <ana@example.com>" in result.text
    assert "Cc: Luis <luis@example.com>" in result.text
    assert "Adjunto el presupuesto del Q3." in result.text
    assert result.status == ExtractionStatus.OK


def test_the_subject_becomes_the_document_title(tmp_path: Path):
    result = read_mail(write(tmp_path, "correo.eml", SIMPLE))
    assert result.structure is not None
    assert result.structure.title == "presupuesto Q3 y revision de proveedores"


def test_html_bodies_are_flattened_to_text(tmp_path: Path):
    result = read_mail(write(tmp_path, "alt.eml", ALTERNATIVE))
    assert "version en texto plano" in result.text
    assert "version en" in result.text and "HTML" in result.text
    assert "<p>" not in result.text
    # Script content is not document text.
    assert "no indexar" not in result.text


def test_html_block_tags_do_not_glue_words_together():
    text = html_to_text("<td>presupuesto</td><td>proveedores</td>")
    assert "presupuesto" in text and "proveedores" in text
    assert "presupuestoproveedores" not in text


def test_base64_transfer_encoding_is_decoded(tmp_path: Path):
    body = base64.b64encode("cifras con acento: Bishops".encode())
    raw = (
        b"From: ana@example.com\r\nSubject: cifras\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\n" + body
    )
    result = read_mail(write(tmp_path, "b64.eml", raw))
    assert "cifras con acento: Bishops" in result.text


# -- the boundary that matters: attachments -----------------------------------

def test_attachments_are_never_read(tmp_path: Path):
    secret = base64.b64encode(b"salario de Ana: 5000 euros")
    raw = (
        b"From: ana@example.com\r\nSubject: nomina\r\n"
        b'Content-Type: multipart/mixed; boundary="bnd"\r\n'
        b"\r\n--bnd\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Vea el adjunto.\r\n"
        b"--bnd\r\n"
        b'Content-Type: application/pdf\r\n'
        b'Content-Disposition: attachment; filename="nomina.pdf"\r\n'
        b"Content-Transfer-Encoding: base64\r\n\r\n" + secret
        + b"\r\n--bnd--\r\n"
    )
    result = read_mail(write(tmp_path, "adj.eml", raw))
    assert "5000" not in result.text
    assert "salario" not in result.text
    assert "nomina.pdf" not in result.text
    # ...and the fact that something was skipped is visible, not implied.
    assert any("attachment" in warning for warning in result.warnings)


def test_an_inline_image_is_not_mistaken_for_body_text(tmp_path: Path):
    raw = (
        b"From: ana@example.com\r\nSubject: con imagen\r\n"
        b'Content-Type: multipart/related; boundary="bnd"\r\n'
        b"\r\n--bnd\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<p>grafico adjunto</p>\r\n"
        b"--bnd\r\n"
        b"Content-Type: image/png\r\n\r\n\x89PNG binary\r\n--bnd--\r\n"
    )
    result = read_mail(write(tmp_path, "img.eml", raw))
    assert "grafico adjunto" in result.text
    assert "PNG binary" not in result.text


# -- damaged input costs a part, not the message ------------------------------

def test_an_unknown_charset_loses_the_body_but_keeps_the_headers(tmp_path: Path):
    raw = (
        b"From: ana@example.com\r\nSubject: raro\r\n"
        b"Content-Type: text/plain; charset=inventado-9000\r\n\r\n"
        b"el cuerpo se pierde"
    )
    result = read_mail(write(tmp_path, "raro.eml", raw))
    assert result.text is not None
    assert "Subject: raro" in result.text
    assert "el cuerpo se pierde" not in result.text
    assert result.status == ExtractionStatus.PARTIAL
    assert any("unreadable" in warning for warning in result.warnings)


def test_missing_headers_are_not_an_error(tmp_path: Path):
    raw = b"Subject: solo esto\r\n\r\ncuerpo sin remitente\r\n"
    result = read_mail(write(tmp_path, "mín.eml", raw))
    assert result.status == ExtractionStatus.OK
    assert "solo esto" in result.text
    assert "From:" not in result.text


def test_a_message_with_no_body_still_indexes_its_headers(tmp_path: Path):
    raw = b"From: ana@example.com\r\nSubject: vacio\r\n\r\n"
    result = read_mail(write(tmp_path, "vacio.eml", raw))
    # Not NO_CONTENT: a subject line with no body is exactly the kind of thing
    # a user searches for, and reporting "no content" would drop it.
    assert result.status == ExtractionStatus.OK
    assert "Subject: vacio" in result.text


def test_control_characters_in_headers_are_stripped(tmp_path: Path):
    raw = (
        b"From: ana@example.com\r\n"
        b"Subject: hola\x00\x07\x1bmundo\x00\r\n\r\ncuerpo\r\n"
    )
    result = read_mail(write(tmp_path, "ctrl.eml", raw))
    assert "\x00" not in result.text
    assert "\x07" not in result.text
    assert "\x1b" not in result.text


def test_binary_noise_is_not_indexed_as_text(tmp_path: Path):
    path = write(tmp_path, "ruido.eml", bytes(range(256)) * 40)
    result = read_mail(path)
    # Whatever it finds, it must be a clean string and must not raise.
    assert result.text is None or result.text.strip()
    assert "\x00" not in (result.text or "")


# -- mbox containers ----------------------------------------------------------

def test_mbox_messages_are_split_and_indexed_together(tmp_path: Path):
    first = (
        b"From ana@example.com Mon Sep 14 10:22:31 2026\r\n"
        b"From: ana@example.com\r\nSubject: primero\r\n\r\ncuerpo primero\r\n"
    )
    second = (
        b"From luis@example.com Mon Sep 14 11:00:00 2026\r\n"
        b"From: luis@example.com\r\nSubject: segundo\r\n\r\ncuerpo segundo\r\n"
    )
    result = read_mail(write(tmp_path, "buzon.mbox", first + second))
    assert "Subject: primero" in result.text
    assert "Subject: segundo" in result.text
    assert "cuerpo primero" in result.text
    assert "cuerpo segundo" in result.text


def test_mbox_quoted_from_lines_do_not_become_messages(tmp_path: Path):
    body = b"\r\n>From: alguien@example.com\r\n> From: otro@example.com\r\n"
    raw = (
        b"From ana@example.com Mon Sep 14 10:22:31 2026\r\n"
        b"From: ana@example.com\r\nSubject: reenviado\r\n\r\n" + body
    )
    assert len(split_mbox(raw)) == 1


def test_mbox_sender_comes_from_the_envelope_when_the_header_is_absent(tmp_path):
    raw = (
        b"From ana@example.com Mon Sep 14 10:22:31 2026\r\n"
        b"Subject: sin remitente\r\n\r\ncuerpo\r\n"
    )
    result = read_mail(write(tmp_path, "sin.mbox", raw))
    assert "From: ana@example.com" in result.text


def test_mbox_message_count_is_bounded(tmp_path: Path):
    blocks = [
        b"From a@b.com Mon Sep 14 10:00:00 2026\r\n"
        b"Subject: mensaje %d\r\n\r\ncuerpo %d\r\n" % (index, index)
        for index in range(20)
    ]
    result = read_mail(
        write(tmp_path, "muchos.mbox", b"".join(blocks)),
        limits=ExtractionLimits(max_pages=5),
    )
    assert "Subject: mensaje 0" in result.text
    assert "Subject: mensaje 19" not in result.text
    assert result.status == ExtractionStatus.TRUNCATED
    assert any("stopped at 5" in warning for warning in result.warnings)


# -- the resource contract of phase 025 still holds ---------------------------

def test_an_oversized_input_is_refused_before_being_opened(tmp_path: Path):
    path = write(tmp_path, "enorme.eml", SIMPLE)
    result = read_mail(path, limits=ExtractionLimits(max_input_bytes=10))
    assert result.status == ExtractionStatus.ERROR
    assert "exceeds" in (result.error or "")


def test_a_cancellation_stops_the_pass(tmp_path: Path):
    blocks = [
        b"From a@b.com Mon Sep 14 10:00:00 2026\r\n"
        b"Subject: mensaje %d\r\n\r\ncuerpo %d\r\n" % (index, index)
        for index in range(50)
    ]
    result = read_mail(
        write(tmp_path, "corta.mbox", b"".join(blocks)),
        cancel=lambda: True,
    )
    assert result.status == ExtractionStatus.CANCELLED
    assert result.text is None


def test_extraction_goes_through_the_registry(tmp_path: Path):
    result = extract(write(tmp_path, "correo.eml", SIMPLE))
    assert result.text
    assert "Ana Ruiz" in result.text


def test_a_broken_mail_file_never_raises(tmp_path: Path):
    path = write(tmp_path, "raro.eml", SIMPLE)
    path.unlink()
    result = extract(path)
    assert result.text is None
    assert result.error
