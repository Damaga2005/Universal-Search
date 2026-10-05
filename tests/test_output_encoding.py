"""The CLI's output encoding, which was the one thing this project left to chance.

Every file Universal Search reads or writes is UTF-8, declared explicitly: the
config, the metrics, the events, the control-center state, the logs. The single
exception was the CLI's own output, which inherited whatever code page the
machine had.

Measured on this machine -- Windows 11 Professional, `locale.getlocale()` is
`('es_ES', 'cp1252')`:

  * redirecting `universal-search search` to a file produced bytes that **are
    not valid UTF-8**; they fail to decode at byte 19;
  * the characters themselves survived, because `ó` and `ñ` both exist in
    cp1252, so **nothing looked broken on screen**;
  * which means the damage only appears downstream, when an editor, `jq` or
    another program reads the file assuming UTF-8.

Twenty-five phases of this project's own documentation prefixed every command
with `$env:PYTHONIOENCODING="utf-8"`, and nobody had checked whether it was
needed. It was not needed -- and setting it was masking a defect rather than
preventing one.

The measurement below is taken in a subprocess with the variable explicitly
*removed*, because asserting on this process's stdout would only prove something
about the test runner.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The interpreter running the tests, not a hardcoded `.venv` path.
#:
#: This was `ROOT / ".venv" / "Scripts" / "python.exe"`, which exists on the
#: development machine and is gitignored, so it does not exist on a CI runner.
#: All three subprocess tests failed there with `[WinError 2]`, having never
#: run anywhere but the one machine that had a venv. What the tests measure is
#: that the output encoding does not depend on the environment, so which
#: interpreter spawns the subprocess is incidental -- `sys.executable` is the
#: one that is guaranteed to exist and to be the version under test.
PYTHON = Path(sys.executable)

ACCENTED = {
    "configuración.md": "Retroalimentacion del amplificador\n",
    "señales.txt": "Salida del circuito y coeficiente\n",
}


def _runner(directory: Path) -> Path:
    path = directory / "cli_runner.py"
    path.write_text(
        "import sys\n"
        "from universal_search.cli import main\n"
        "sys.argv = ['universal-search', *sys.argv[1:]]\n"
        "try:\n"
        "    main()\n"
        "except SystemExit as exc:\n"
        "    raise SystemExit(int(exc.code or 0))\n",
        encoding="utf-8",
    )
    return path


def _run(argv: list[str], directory: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # The whole point: neither variable may rescue the output.
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    return subprocess.run(
        [str(PYTHON), str(_runner(directory)), *argv],
        capture_output=True, cwd=str(ROOT), env=env, timeout=300,
    )


def test_search_output_is_utf8_without_any_environment_variable(tmp_path):
    """The defect, as it was measured.

    Before the fix these bytes failed to decode as UTF-8 at byte 19. The test
    asserts the decode rather than the appearance, because on this machine the
    characters looked fine -- which is exactly why the defect survived.
    """
    tree = tmp_path / "docs"
    tree.mkdir()
    for name, text in ACCENTED.items():
        (tree / name).write_text(text, encoding="utf-8")
    database = tmp_path / "index.db"
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()

    indexed = _run(
        ["index", str(tree), "--database", str(database)], runner_dir
    )
    assert indexed.returncode == 0, indexed.stderr.decode("utf-8", "replace")

    found = _run(["search", "retroalimentacion", "--database", str(database)],
                 runner_dir)
    assert found.returncode == 0, found.stderr.decode("utf-8", "replace")

    text = found.stdout.decode("utf-8")  # raises if it is not UTF-8
    assert "configuración.md" in text, (
        f"the accented filename did not survive: {text[:300]!r}"
    )


def test_a_replaced_character_is_not_how_this_project_says_a_name(tmp_path):
    """`errors="replace"` stays -- it is what stops the CLI dying while printing.

    But it must never be *used*. If a name comes back as U+FFFD the output is
    lossy, and a lossy output is worse than a wrong-looking one: it looks like a
    missing file.
    """
    tree = tmp_path / "docs"
    tree.mkdir()
    for name, text in ACCENTED.items():
        (tree / name).write_text(text, encoding="utf-8")
    database = tmp_path / "index.db"
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()

    _run(["index", str(tree), "--database", str(database)], runner_dir)
    found = _run(["search", "retroalimentacion", "--database", str(database)],
                 runner_dir)

    text = found.stdout.decode("utf-8")
    assert "�" not in text, (
        f"output was truncated by the error handler: {text[:300]!r}"
    )


def test_the_project_does_not_need_the_incantation_anywhere(tmp_path):
    """`index`, `search` and `extensions` all run with the variables removed.

    This is the assertion that retires `$env:PYTHONIOENCODING="utf-8"`, which
    twenty-five phases of documentation and every shell in this project's history
    prefixed to every command without anyone checking whether it did anything.
    """
    tree = tmp_path / "docs"
    tree.mkdir()
    for name, text in ACCENTED.items():
        (tree / name).write_text(text, encoding="utf-8")
    database = tmp_path / "index.db"
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()

    for argv in (
        ["index", str(tree), "--database", str(database)],
        ["search", "retroalimentacion", "--database", str(database)],
        ["extensions"],
        ["privacy", "show", "--database", str(database)],
    ):
        result = _run(argv, runner_dir)
        assert result.returncode == 0, (
            f"{argv[0]} failed with the encoding variables removed: "
            f"{result.stderr.decode('utf-8', 'replace')[:300]}"
        )
        result.stdout.decode("utf-8")  # raises if any of them is not UTF-8


def test_every_project_file_is_declared_utf8():
    """The consistency the CLI was breaking.

    If the config, the metrics and the logs are all written as UTF-8 while the
    output stream is not, then a user piping `search` into `diagnose export` is
    joining two encodings that disagree.
    """
    appconfig = (ROOT / "src" / "universal_search" / "appconfig.py").read_text(
        encoding="utf-8"
    )
    assert 'encoding="utf-8"' in appconfig, (
        "the config writer must declare its encoding explicitly"
    )
    assert 'encoding="utf-8"' in (
        ROOT / "src" / "universal_search" / "background.py"
    ).read_text(encoding="utf-8")

    cli = (ROOT / "src" / "universal_search" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert 'encoding="utf-8"' in cli, (
        "the CLI must declare its output encoding like every other writer"
    )


def test_the_locale_is_what_makes_this_a_real_defect():
    """Guard against "fixing" it on a machine where it never showed up.

    If `getpreferredencoding` were already UTF-8 the bug would be invisible, and
    a future change could quietly undo the fix without any test failing. This
    asserts the condition, and skips when the machine is not the Spanish one.
    """
    import locale

    preferred = locale.getpreferredencoding(False)
    if preferred.lower().replace("-", "") in {"utf8", "cp65001"}:
        pytest.skip(
            f"this machine's preferred encoding is already {preferred}; the "
            f"defect this test pins does not reproduce here"
        )
    # On this machine it is cp1252, which is why the CLI had to declare UTF-8
    # rather than inherit it.
    assert preferred.lower().replace("-", "") in {"cp1252", "cp1250"}, (
        f"unexpected preferred encoding {preferred!r}; the fix and this test "
        f"were both written against cp1252"
    )