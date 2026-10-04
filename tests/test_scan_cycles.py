"""A junction loop, which is the case phases 046 and 047 both declared unbounded.

Both reports said the same thing about `scan_local`:

  * 046: "el único recurso sin cota (`scan_local` no tiene conjunto de visitados)"
  * 047: "El caso de las junctions en `scan_local` sigue sin conjunto de visitados"

and the existing defence does not cover it. `entry.is_dir(follow_symlinks=False)`
is False for a junction and `entry.is_symlink()` is also False, because a
junction is a reparse point rather than a symbolic link. So a junction pointing
at its own ancestor is walked again, and again, and the generator never
terminates.

This builds that junction for real with `mklink /J` -- the actual Windows
mechanism, not a mock -- and asserts the walk terminates and reports each file
once. If the guard regresses, this test hangs rather than passing, which is the
correct failure for an infinite loop and is why the scan runs under a timeout.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from universal_search.providers.local import FileEntry, scan_local


def _make_junction(link: Path, target: Path) -> bool:
    """Create a directory junction, the way Windows itself would."""
    if os.name != "nt":
        return False
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists():
        return False
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, timeout=60,
    )
    return result.returncode == 0 and link.exists()


@pytest.mark.skipif(os.name != "nt", reason="a junction is a Windows concept")
def test_a_junction_pointing_at_its_own_ancestor_terminates(tmp_path):
    """The declared-unbounded case, now bounded.

    Without the visited set this test does not fail -- it runs forever, because
    `scan_local` is a generator and each pass yields more documents rather than
    exhausting memory. The timeout is the assertion.
    """
    root = tmp_path / "arbol"
    root.mkdir()
    (root / "a.txt").write_text("uno", encoding="utf-8")
    nested = root / "sub"
    nested.mkdir()
    (nested / "b.txt").write_text("dos", encoding="utf-8")

    junction = nested / "vuelta"
    if not _make_junction(junction, root):
        pytest.skip("mklink /J is unavailable in this environment")

    def collect() -> list[FileEntry]:
        # A cap so a regression fails as an assertion rather than as a hang.
        found = []
        for item in scan_local(root):
            if isinstance(item, FileEntry):
                found.append(item)
                if len(found) > 50:
                    break
        return found

    found = collect()
    names = sorted(entry.path.name for entry in found)

    assert len(found) <= 50, (
        "the walk did not terminate: a junction at nested/vuelta points back at "
        "the scanned root"
    )
    assert names == ["a.txt", "b.txt"], (
        f"each file must be yielded once, got {names}"
    )


@pytest.mark.skipif(os.name != "nt", reason="a junction is a Windows concept")
def test_a_junction_to_a_sibling_directory_is_walked_once(tmp_path):
    """A second route to the same directory is one directory, not two.

    A junction pointing *sideways* is not a loop -- the walk would terminate
    either way -- so this is the subtler half: without the visited set each file
    behind the junction is indexed under two paths.
    """
    root = tmp_path / "arbol"
    root.mkdir()
    real = root / "real"
    real.mkdir()
    (real / "unico.txt").write_text("una vez", encoding="utf-8")

    junction = root / "atajo"
    if not _make_junction(junction, real):
        pytest.skip("mklink /J is unavailable in this environment")

    names = sorted(
        entry.path.name
        for entry in scan_local(root)
        if isinstance(entry, FileEntry)
    )

    assert names == ["unico.txt"], (
        f"the file behind the junction must appear once, got {names}"
    )


def test_the_depth_bound_reports_instead_of_truncating_silently(tmp_path):
    """A bound nobody is told about is indistinguishable from a broken index."""
    from universal_search.providers import local as local_module
    from universal_search.providers.base import ScanError

    root = tmp_path / "profundo"
    root.mkdir()
    deep = root
    for level in range(6):
        deep = deep / f"nivel{level}"
    deep.mkdir(parents=True)
    (deep / "al_final.txt").write_text("al fondo", encoding="utf-8")

    original = local_module.MAX_SCAN_DEPTH
    local_module.MAX_SCAN_DEPTH = 3
    try:
        items = list(scan_local(root))
    finally:
        local_module.MAX_SCAN_DEPTH = original

    errors = [item for item in items if isinstance(item, ScanError)]
    names = [item.name for item in items if isinstance(item, FileEntry)]

    assert names == [], "the bound must stop before the deep file"
    assert errors, "a bound must be visible, not silent"
    message = str(errors[0].message)
    assert "MAX_SCAN_DEPTH" in message, (
        f"the error must name the bound it hit, so a user can act: {message!r}"
    )
    assert "3" in message, "and it must say what the bound was"


def test_a_real_tree_is_not_depth_limited_by_accident(tmp_path):
    """The bound is generous, and a normal tree must walk completely."""
    root = tmp_path / "normal"
    root.mkdir()
    current = root
    for level in range(8):
        current = current / f"nivel{level}"
    current.mkdir(parents=True)
    (current / "dentro.txt").write_text("hola", encoding="utf-8")

    names = [
        item.path.name for item in scan_local(root)
        if isinstance(item, FileEntry)
    ]

    assert names == ["dentro.txt"], (
        "an eight-level tree is not a cycle, and the bound must not touch it"
    )