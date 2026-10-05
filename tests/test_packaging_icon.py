"""The committed icon must be the one `make_icon.py` produces.

`packaging/make_icon.py` was flagged as a dead file: referenced by nothing but
its own docstring and one historical report line. It is not dead -- it is a
build tool nobody invokes, and it reproduces `packaging/universal_search.ico`.

An artefact with no executable claim on it can be replaced by anything and
nothing notices. This runs the generator against a copy of `packaging/` and
compares, so the script has a caller and the icon has a guarantee -- without
overwriting the committed file.

The comparison is on decoded pixels, not on file bytes. This file previously
asserted byte equality and therefore asserted something false: the bytes carry
`zlib.compress(raw, 9)` output, which is not identical across zlib builds. It
passed on the interpreter that generated the icon (3.14 on Windows, which
bundles zlib-ng) and failed on 3.12, 3.13 and Linux in the same matrix, with a
message blaming the drawing for a difference in the compressor. The drawing is
identical; see `_pixels`.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKER = ROOT / "packaging" / "make_icon.py"
ICON = ROOT / "packaging" / "universal_search.ico"


def _pixels(ico: bytes) -> dict[int, bytes]:
    """Decode an ICO into ``{size: RGBA}``, so it can be compared as a drawing.

    The file is compared as pixels rather than as bytes because the bytes are
    not a property of this repository. `write_png` stores each image with
    `zlib.compress(raw, 9)`, and deflate output at a fixed level is not
    required to be identical across zlib builds: CPython 3.14 on Windows
    bundles zlib-ng, while 3.12, 3.13 and Linux bundle classic zlib, and the
    two emit different bytes for the same input.

    That is what this test was doing: it passed on the machine that generated
    the file and failed on every other interpreter in the matrix, reporting
    "the committed icon is not what make_icon.py produces" when the drawing was
    in fact identical. Decoding isolates the invariant -- *is this the icon the
    generator draws* -- from the compressor's freedom to choose its own output.

    Every PNG row is prefixed with a filter byte (always 0x00 as written), so
    the row stride is ``size * 4 + 1``. Getting that wrong silently compares
    shifted data, which is why it is written out here.
    """
    _reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert kind == 1, "not an ICO"
    images: dict[int, bytes] = {}
    for index in range(count):
        entry = ico[6 + index * 16 : 6 + (index + 1) * 16]
        dimension, _, _, _, _, _, length, offset = struct.unpack(
            "<BBBBHHII", entry
        )
        size = dimension or 256  # 0 means 256 in the ICO directory
        png = ico[offset : offset + length]

        payload = b""
        position = 8  # skip the PNG signature
        while position < len(png):
            chunk_length = struct.unpack(">I", png[position : position + 4])[0]
            tag = png[position + 4 : position + 8]
            if tag == b"IDAT":
                payload += png[position + 8 : position + 8 + chunk_length]
            position += 12 + chunk_length  # length + tag + data + CRC

        raw = zlib.decompress(payload)
        stride = size * 4 + 1
        images[size] = b"".join(
            raw[row * stride + 1 : (row + 1) * stride] for row in range(size)
        )
    return images


def test_the_committed_icon_is_exactly_what_the_generator_produces(tmp_path) -> None:
    packaging = tmp_path / "packaging"
    shutil.copytree(ROOT / "packaging", packaging,
                    ignore=shutil.ignore_patterns("__pycache__"))

    committed = (packaging / ICON.name).read_bytes()

    result = subprocess.run(
        [sys.executable, str(packaging / MAKER.name)],
        capture_output=True, timeout=300,
    )
    assert result.returncode == 0, (
        f"make_icon.py failed: "
        f"{(result.stdout + result.stderr).decode('utf-8', 'replace')[-300:]}"
    )

    regenerated = (packaging / ICON.name).read_bytes()

    committed_pixels = _pixels(committed)
    regenerated_pixels = _pixels(regenerated)

    assert sorted(regenerated_pixels) == sorted(committed_pixels), (
        f"the icon carries sizes {sorted(committed_pixels)} but the generator "
        f"produces {sorted(regenerated_pixels)}"
    )
    for size, pixels in sorted(committed_pixels.items()):
        assert regenerated_pixels[size] == pixels, (
            f"the committed {size}x{size} icon is not what make_icon.py draws; "
            f"either regenerate and commit it, or the generator is stale. The "
            f"drawing differs -- this compares decoded pixels, so a zlib that "
            f"compresses differently is not mistaken for a changed icon."
        )
