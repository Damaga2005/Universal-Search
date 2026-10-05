"""The committed icon must be the one `make_icon.py` produces.

`packaging/make_icon.py` was flagged as a dead file: referenced by nothing but
its own docstring and one historical report line. It is not dead -- it is a
build tool nobody invokes, and it reproduces `packaging/universal_search.ico`
**byte for byte** (SHA-256
`915d98af038bd3496ea9d60472619c38a2b30a365614488f33ff1a45981fb463`, 2434 bytes,
measured).

An artefact with no executable claim on it can be replaced by anything and
nothing notices. This runs the generator against a copy of `packaging/` and
compares, so the script has a caller and the icon has a guarantee -- without
overwriting the committed file.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKER = ROOT / "packaging" / "make_icon.py"
ICON = ROOT / "packaging" / "universal_search.ico"


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

    assert hashlib.sha256(regenerated).hexdigest() == hashlib.sha256(
        committed).hexdigest(), (
        "the committed icon is not what make_icon.py produces; either "
        "regenerate and commit it, or the generator is stale"
    )
    assert len(regenerated) == 2434, (
        f"the icon was {len(regenerated)} bytes; it has been 2434 since it was "
        f"first committed, so a size change means the drawing changed and "
        f"that needs saying out loud rather than slipping into a commit"
    )
