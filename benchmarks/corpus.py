"""Deterministic synthetic corpora for the benchmark suite (spec 011).

Every byte is derived from arithmetic over the index — no RNG, no clock —
so two runs of ``--profile 1000`` index bit-identical files and produce
comparable numbers.
"""

from pathlib import Path

# Domain-flavoured vocabulary so queries like "bjt", "mux cmos" and the
# phrase "ebers moll" hit meaningful result sets.
WORDS = (
    "bjt transistor amplifier bias base emitter collector mosfet cmos nmos pmos "
    "gate threshold mux demux decoder encoder alu register clock timing "
    "ebers moll model spice simulation schematic netlist layout drc lvs "
    "fourier laplace transform convolution integral derivative spectrum "
    "voltage current resistance impedance power gain bandwidth noise "
    "microcontroller assembly linker section segment interrupt vector "
    "database index hash tree graph traversal sorting searching"
).split()

# Phrase deliberately present in a deterministic minority of files.
PHRASE = "ebers moll"

FOLDERS = (
    "universidad/electromagnetismo",
    "universidad/circuitos",
    "universidad/programacion",
    "trabajo/informes",
    "trabajo/datos",
    "personal/notas",
)

# ~200 words ≈ 1.3 KB per file: small enough that 100k stays under
# ~200 MB, large enough that content ranking behaves realistically.
WORDS_PER_FILE = 200


def build(root: Path, count: int) -> list[Path]:
    """Create ``count`` .txt files under ``root``; return their paths."""
    files: list[Path] = []
    for index in range(count):
        folder = root / FOLDERS[index % len(FOLDERS)]
        folder.mkdir(parents=True, exist_ok=True)
        # Word k of file i is WORDS[(i * 7 + k * 13) % len(WORDS)]: every
        # file shares vocabulary with its neighbours (realistic overlap)
        # while its exact content stays unique.
        body = " ".join(
            WORDS[(index * 7 + step * 13) % len(WORDS)]
            for step in range(WORDS_PER_FILE)
        )
        if index % 10 == 0:
            body = f"{PHRASE} lecture notes. {body}"
        path = folder / f"doc-{index:06d}.txt"
        path.write_text(body, encoding="utf-8")
        files.append(path)
    return files


def modify(files: list[Path], count: int) -> None:
    """Rewrite the first ``count`` files with different content."""
    for path in files[:count]:
        path.write_text(
            path.read_text(encoding="utf-8") + " modified revision delta",
            encoding="utf-8",
        )


def delete(files: list[Path], count: int) -> list[Path]:
    """Delete the LAST ``count`` files (returns the removed paths)."""
    removed = files[-count:]
    for path in removed:
        path.unlink()
    return removed
