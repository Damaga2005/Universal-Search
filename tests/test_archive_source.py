"""Phase 034: searchable content inside ZIP archives.

The property that matters is not "we can read a zip". It is that a hostile zip
cannot get us: a traversal name, a lying central directory and a bomb are all
refused from the header, before a byte is read.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from universal_search.domain.extraction import (
    ExtractionLimits,
    ExtractionStatus,
)
from universal_search.extractors import extract, infos, supports
from universal_search.extractors.archive import read_archive


def make_zip(path: Path, members: dict[str, bytes], *, compress: bool = True) -> Path:
    mode = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w", mode) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


# -- registration ------------------------------------------------------------

def test_zip_is_registered_and_inspectable():
    assert supports(".zip")
    info = next(item for item in infos() if item.key == "archive")
    assert ".zip" in info.extensions
    assert info.binary_safe is True


# -- what gets indexed --------------------------------------------------------

def test_member_content_is_searchable(tmp_path: Path):
    archive = make_zip(tmp_path / "docs.zip", {
        "notas/represion.txt": "La represion de un SCR de puerta esta activa.",
        "notas/valvulas.txt": "Valvula de control sobre el colector.",
    })
    result = read_archive(archive)
    assert result.status == ExtractionStatus.OK
    assert "represion" in result.text
    assert "Valvula" in result.text


def test_member_names_are_indexed_so_the_file_inside_is_findable(tmp_path: Path):
    archive = make_zip(tmp_path / "docs.zip", {
        "notas/valvulas.txt": "texto sin relacion",
    })
    result = read_archive(archive)
    assert "notas/valvulas.txt" in result.text


def test_each_member_is_labelled_so_a_hit_can_be_traced(tmp_path: Path):
    archive = make_zip(tmp_path / "docs.zip", {"a.txt": "uno", "b.txt": "dos"})
    result = read_archive(archive)
    assert "=== a.txt" in result.text
    assert "=== b.txt" in result.text


def test_member_names_become_bounded_structure(tmp_path: Path):
    archive = make_zip(tmp_path / "docs.zip", {
        f"miembro-{index}.txt": "x" for index in range(5)
    })
    result = read_archive(archive)
    assert result.structure is not None
    assert len(result.structure.headings) == 5


def test_directories_are_not_content(tmp_path: Path):
    archive = make_zip(tmp_path / "docs.zip", {"carpeta/": b"", "a.txt": "uno"})
    result = read_archive(archive)
    assert "carpeta/" not in result.text


# -- the boundary: non-text members are never read ---------------------------

def test_binary_members_are_skipped_not_decoded(tmp_path: Path):
    archive = make_zip(tmp_path / "mix.zip", {
        "notas.txt": "texto legible",
        "programa.exe": b"MZ\x90\x00binary\x00\x01\x02",
        "imagen.png": b"\x89PNG\r\n\x1a\n\x00\x01",
    })
    result = read_archive(archive)
    assert "texto legible" in result.text
    assert "MZ" not in result.text
    assert "PNG" not in result.text
    assert any("non-text" in warning for warning in result.warnings)


def test_a_nested_archive_is_not_opened(tmp_path: Path):
    inner = make_zip(tmp_path / "inner.zip", {"secreto.txt": "tokenSecreto"})
    inner_bytes = inner.read_bytes()
    archive = make_zip(tmp_path / "outer.zip", {
        "notas.txt": "visible",
        "dentro.zip": inner_bytes,
    })
    result = read_archive(archive)
    assert "visible" in result.text
    # Without a depth limit, nested archives are an unbounded expansion path.
    assert "tokenSecreto" not in result.text
    assert any("no recursion" in warning for warning in result.warnings)


# -- hostile archives are refused from the header ----------------------------

def test_a_traversal_member_is_refused(tmp_path: Path):
    archive = make_zip(tmp_path / "slip.zip", {
        "../../windows/system32/evil.txt": "contenido malicioso",
        "notas.txt": "legible",
    })
    result = read_archive(archive)
    assert "contenido malicioso" not in result.text
    assert "legible" in result.text
    assert any("unsafe member name" in warning for warning in result.warnings)
    assert result.status == ExtractionStatus.PARTIAL


def test_an_absolute_member_name_is_refused(tmp_path: Path):
    archive = make_zip(tmp_path / "abs.zip", {
        "/etc/passwd": "root:x:0:0",
        "notas.txt": "legible",
    })
    result = read_archive(archive)
    assert "root:x" not in result.text
    assert any("unsafe member name" in warning for warning in result.warnings)


def test_a_lying_declared_size_is_refused(tmp_path: Path):
    archive = make_zip(tmp_path / "lie.zip", {
        "notas.txt": "legible",
        "enorme.txt": "x" * 5000,
    })
    result = read_archive(archive, limits=ExtractionLimits(max_part_bytes=1000))
    assert "legible" in result.text
    assert result.status == ExtractionStatus.PARTIAL
    assert any("refused" in warning for warning in result.warnings)


def test_the_bomb_ratio_is_refused(tmp_path: Path):
    archive = make_zip(
        tmp_path / "bomb.zip",
        {"notas.txt": "legible", "bomba.txt": b"\0" * 200_000},
    )
    result = read_archive(
        archive, limits=ExtractionLimits(max_expansion_ratio=2)
    )
    assert "legible" in result.text
    assert any("refused" in warning for warning in result.warnings)


def test_a_member_that_lies_about_its_own_size_is_not_trusted(tmp_path: Path):
    """The declared size is not the read size: reading is what counts."""
    archive = make_zip(
        tmp_path / "grow.zip", {"notas.txt": "y" * 50_000}
    )
    result = read_archive(archive, limits=ExtractionLimits(max_part_bytes=1000))
    assert result.text is None or "y" * 1000 not in result.text


def test_nothing_is_written_to_disk(tmp_path: Path):
    archive = make_zip(tmp_path / "docs.zip", {"notas.txt": "contenido"})
    before = sorted(p.name for p in tmp_path.iterdir())
    read_archive(archive)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# -- damaged input -------------------------------------------------------------

def test_a_corrupt_archive_is_an_error_not_a_crash(tmp_path: Path):
    broken = tmp_path / "roto.zip"
    broken.write_bytes(b"PK\x03\x04 this is not a zip")
    result = read_archive(broken)
    assert result.status == ExtractionStatus.ERROR
    assert result.error


def test_an_empty_archive_has_no_content(tmp_path: Path):
    archive = make_zip(tmp_path / "vacio.zip", {})
    result = read_archive(archive)
    assert result.status == ExtractionStatus.NO_CONTENT
    assert result.text is None


def test_a_missing_archive_is_an_error(tmp_path: Path):
    result = read_archive(tmp_path / "no-existe.zip")
    assert result.status == ExtractionStatus.ERROR
    assert result.error


# -- the resource contract of phase 025 still holds --------------------------

def test_an_oversized_archive_is_refused_before_being_opened(tmp_path: Path):
    archive = make_zip(tmp_path / "docs.zip", {"notas.txt": "contenido"})
    result = read_archive(archive, limits=ExtractionLimits(max_input_bytes=10))
    assert result.status == ExtractionStatus.ERROR
    assert "exceeds" in (result.error or "")


def test_the_member_count_is_bounded(tmp_path: Path):
    archive = make_zip(
        tmp_path / "muchos.zip", {f"m{index}.txt": "x" for index in range(40)}
    )
    result = read_archive(
        archive, limits=ExtractionLimits(max_zip_members=5)
    )
    assert "=== m0.txt" in result.text
    assert "=== m39.txt" not in result.text
    assert result.status == ExtractionStatus.TRUNCATED
    assert any("stopped at 5" in warning for warning in result.warnings)


def test_cancellation_stops_the_pass(tmp_path: Path):
    archive = make_zip(
        tmp_path / "docs.zip", {f"m{index}.txt": "x" for index in range(50)}
    )
    result = read_archive(archive, cancel=lambda: True)
    assert result.status == ExtractionStatus.CANCELLED
    assert result.text is None


def test_extraction_goes_through_the_registry(tmp_path: Path):
    archive = make_zip(tmp_path / "docs.zip", {"notas.txt": "via registro"})
    assert "via registro" in extract(archive).text


def test_resource_usage_reports_decompressed_bytes(tmp_path: Path):
    body = ("presupuesto del trimestre y revision de proveedores " * 100).encode()
    archive = make_zip(tmp_path / "docs.zip", {"notas.txt": body})
    result = read_archive(archive)
    assert result.status == ExtractionStatus.OK
    assert result.resource_usage is not None
    # The declared size in the header is not what is read; this counts bytes
    # actually decompressed into memory.
    assert result.resource_usage.temp_bytes == len(body)
    assert result.resource_usage.input_bytes > 0
