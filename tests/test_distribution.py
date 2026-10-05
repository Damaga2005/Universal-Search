"""Phase 037: portable mode and the distribution artefacts.

Two things are asserted here, and the second is the one the 2.0.0 audit proved
necessary:

* portable mode is a real deployment — index, search, forget and every
  coordination file land in a folder next to the executable, and nothing at
  all is written under ``%LOCALAPPDATA%``;
* the shipped files are coherent, and the single-file build is a genuine
  single file rather than a one-dir build with a friendlier name.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from universal_search import __version__, portable
from universal_search.appconfig import AppPaths, default_home, per_user_home
from universal_search.cli import main
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine


ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    """No test may inherit a portable switch from the developer's shell.

    Also undone afterwards. Setting ``os.environ`` directly would leak the
    switch into every later module — three unrelated tests failed downstream
    with an index that had appeared next to the repository — so every test
    here goes through ``monkeypatch`` instead.
    """
    monkeypatch.delenv(portable.PORTABLE_ENV, raising=False)
    monkeypatch.delenv(portable.HOME_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LOCALAPPDATA"))


@pytest.fixture
def executable_folder(tmp_path, monkeypatch):
    """Pretend the program lives in ``tmp_path/copy`` and run from there.

    Outside a PyInstaller build the executable folder is the working
    directory, so this is the same situation a user on a USB stick is in — and
    it needs no production switch that exists only for tests.
    """
    folder = tmp_path / "copy"
    folder.mkdir()
    monkeypatch.chdir(folder)
    return folder


def _files_below(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }


# -- where the data lives -----------------------------------------------------

def test_without_a_marker_the_installed_layout_is_used(
    executable_folder, tmp_path
) -> None:
    assert default_home() == per_user_home()
    assert not AppPaths.discover().portable


def test_the_marker_moves_the_data_next_to_the_executable(
    executable_folder, tmp_path
) -> None:
    portable.enable()
    expected = executable_folder / portable.PORTABLE_DATA_DIRNAME
    assert default_home() == expected
    assert AppPaths.discover().portable


def test_the_environment_alone_is_enough(executable_folder, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(portable.PORTABLE_ENV, "1")
    assert portable.requested()[0]
    assert default_home() == executable_folder / portable.PORTABLE_DATA_DIRNAME


def test_a_false_environment_value_does_not_enable_portable(
    executable_folder, tmp_path, monkeypatch
) -> None:
    # "0" and "false" must not be read as truthy: a launcher that sets the
    # variable to switch portable mode *off* is not unusual.
    for value in ("0", "false", "no", ""):
        monkeypatch.setenv(portable.PORTABLE_ENV, value)
        assert not portable.requested()[0], value


def test_an_explicit_home_wins_over_the_marker(
    executable_folder, tmp_path, monkeypatch
) -> None:
    portable.enable()
    monkeypatch.setenv(portable.HOME_ENV, str(tmp_path / "explicit"))
    # The worker passes its resolved home to its child process, so this is
    # load-bearing rather than a preference.
    assert not portable.status().portable
    assert AppPaths.discover().home == tmp_path / "explicit"


def test_an_explicit_home_wins_over_the_environment_too(
    executable_folder, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(portable.PORTABLE_ENV, "1")
    monkeypatch.setenv(portable.HOME_ENV, str(tmp_path / "explicit"))
    assert AppPaths.discover().home == tmp_path / "explicit"


def test_removing_the_marker_returns_to_the_installed_layout(
    executable_folder, tmp_path
) -> None:
    portable.enable()
    assert portable.disable()
    assert default_home() == per_user_home()
    # And disabling twice is not an error: it just reports there was nothing.
    assert not portable.disable()


def test_disabling_the_environment_alone_needs_no_marker(
    executable_folder, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(portable.PORTABLE_ENV, "1")
    monkeypatch.delenv(portable.PORTABLE_ENV)
    assert not portable.requested()[0]


def test_status_explains_which_rule_decided(executable_folder, tmp_path) -> None:
    plain = portable.status()
    assert not plain.portable
    assert "instalado" in portable.describe() or "normal" in portable.describe()
    portable.enable()
    current = portable.status()
    assert current.portable
    assert portable.PORTABLE_MARKER in current.reason
    assert current.marker == executable_folder / portable.PORTABLE_MARKER


# -- portable mode actually works ---------------------------------------------

def test_indexing_and_searching_stay_inside_the_portable_folder(
    executable_folder, tmp_path
) -> None:
    portable.enable()
    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "nota.md").write_text(
        "BJT: transistor de union bipolar y polarizacion", encoding="utf-8"
    )
    paths = AppPaths.discover()
    database = SearchDatabase(paths.database)
    Indexer(database).index_root(documents)

    found = SearchEngine(database).search("bjt", limit=5)
    assert [item.name for item in found] == ["nota.md"]
    assert paths.database.is_file()
    assert paths.database.is_relative_to(paths.home)


def test_portable_mode_never_touches_localappdata(
    executable_folder, tmp_path, monkeypatch
) -> None:
    portable.enable()
    local = tmp_path / "LOCALAPPDATA"
    local.mkdir(exist_ok=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    before = _files_below(local)

    paths = AppPaths.discover()
    paths.ensure()
    paths.log_file.write_text("una linea\n", encoding="utf-8")
    paths.status_file.write_text("{}", encoding="utf-8")

    assert _files_below(local) == before
    assert not (local / "Universal Search").exists()


def test_the_portable_data_directory_is_separate_from_the_program(
    executable_folder, tmp_path
) -> None:
    # The index churns every second. Keeping it in a subfolder means the
    # folder a user copies to another PC has a stable part and a volatile one,
    # instead of a pile of lock files next to the executable.
    portable.enable()
    paths = AppPaths.discover()
    assert paths.home != executable_folder
    assert paths.home.name == portable.PORTABLE_DATA_DIRNAME


def test_a_read_only_portable_folder_is_refused_not_relocated(tmp_path) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_text("esto es un fichero, no una carpeta", encoding="utf-8")

    with pytest.raises(portable.PortableUnavailable) as failure:
        portable.ensure_writable(occupied / portable.PORTABLE_DATA_DIRNAME)
    assert "%LOCALAPPDATA%" in str(failure.value)


def test_writing_the_marker_over_a_file_is_refused(tmp_path) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_text("no una carpeta", encoding="utf-8")
    with pytest.raises(portable.PortableUnavailable):
        portable.enable(occupied)


def test_the_marker_explains_itself_to_a_human(executable_folder) -> None:
    portable.enable()
    text = (executable_folder / portable.PORTABLE_MARKER).read_text(
        encoding="utf-8"
    )
    # Someone will open this in Notepad before deleting it. It has to say what
    # it does and how to undo it.
    assert portable.PORTABLE_DATA_DIRNAME in text
    assert "LOCALAPPDATA" in text


def test_enable_reports_the_directory_it_prepared(executable_folder) -> None:
    assert portable.enable() == (
        executable_folder / portable.PORTABLE_DATA_DIRNAME
    )


# -- the CLI surface ----------------------------------------------------------

def test_portable_status_command_reports_the_location(
    executable_folder, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["universal-search", "portable", "status"])
    main()
    out = capsys.readouterr().out
    assert "instalado" in out
    assert str(per_user_home()) in out


def test_portable_on_and_off_round_trip_through_the_cli(
    executable_folder, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["universal-search", "portable", "on"])
    main()
    assert (executable_folder / portable.PORTABLE_MARKER).is_file()
    assert str(executable_folder) in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["universal-search", "portable", "off"])
    main()
    assert not (executable_folder / portable.PORTABLE_MARKER).exists()
    assert "%LOCALAPPDATA%" in capsys.readouterr().out


def test_switching_mode_does_not_move_the_index(
    executable_folder, capsys, monkeypatch
) -> None:
    """Turning portable on must not relocate data the user already has.

    Moving an index is a decision about hours of work: doing it silently would
    either duplicate them or lose them. So the command writes a marker and says
    where to look, nothing more.
    """
    installed = per_user_home()
    installed.mkdir(parents=True, exist_ok=True)
    (installed / "index.db").write_bytes(b"indice previo")
    before = (installed / "index.db").read_bytes()

    monkeypatch.setattr(sys, "argv", ["universal-search", "portable", "on"])
    main()
    assert (installed / "index.db").read_bytes() == before
    assert "no se mueve" in capsys.readouterr().out


def test_portable_off_without_a_marker_says_so(
    executable_folder, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["universal-search", "portable", "off"])
    main()
    assert "ya no estaba" in capsys.readouterr().out


def test_portable_on_reports_a_failure_instead_of_exiting_zero(
    tmp_path, capsys, monkeypatch
) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_text("no una carpeta", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["universal-search", "portable", "on", "--base", str(occupied)],
    )
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
    assert "error:" in capsys.readouterr().err


def test_privacy_show_declares_the_deployment(
    executable_folder, tmp_path, capsys, monkeypatch
) -> None:
    portable.enable()
    monkeypatch.setattr(
        sys,
        "argv",
        ["universal-search", "privacy", "show", "--database",
         str(tmp_path / "vacio.db")],
    )
    main()
    out = capsys.readouterr().out
    assert "deployment:" in out
    assert "portable" in out


# -- the shipped artefacts ----------------------------------------------------

def _spec(name: str) -> str:
    return (PACKAGING / name).read_text(encoding="utf-8")


def test_both_specs_pin_the_same_version_and_icon() -> None:
    one_dir = _spec("universal-search.spec")
    one_file = _spec("universal-search-onefile.spec")
    assert one_dir.count("version=str(VERSION_FILE)") == 2
    assert one_file.count("version=str(VERSION_FILE)") == 1
    assert one_dir.count("icon=str(ICON)") == 2
    assert one_file.count("icon=str(ICON)") == 1
    # One version, declared in the package and read by both specs.
    assert __version__ in (PACKAGING / "version_file.txt").read_text(
        encoding="utf-8"
    )
    assert "universal_search.__version__" in one_file


def test_the_one_file_spec_bundles_instead_of_collecting() -> None:
    spec = _spec("universal-search-onefile.spec")
    # A spec that also calls COLLECT produces a folder next to the executable.
    # That is the exact artefact the 2.0.0 release shipped and that did not
    # run when downloaded.
    assert "COLLECT(" not in spec
    assert "exclude_binaries=False" in spec
    assert "a.binaries" in spec
    assert "a.datas" in spec


def test_the_one_file_spec_keeps_the_lazily_imported_worker_imports() -> None:
    """A frozen worker with no watchers or lifecycle control is not the product."""
    spec = _spec("universal-search-onefile.spec")
    for module in (
        "universal_search.background",
        "universal_search.cli",
        "watchdog.observers",
        "watchdog.events",
    ):
        assert module in spec


def test_both_specs_use_the_same_entry_point() -> None:
    for name in ("universal-search.spec", "universal-search-onefile.spec"):
        assert "packaging" in _spec(name) and "entry-gui.py" in _spec(name)


def test_the_build_script_starts_what_it_builds() -> None:
    """The audit's worst finding was an artefact that was never executed.

    ``build.ps1`` therefore runs the real files before declaring success, and
    tests copy the executable somewhere with no siblings — the reproduction
    that caught the defect.
    """
    script = _spec("build.ps1")
    assert "--version" in script
    assert "PYI-8" in script or "_internal" in script
    assert "universal-search-onefile.spec" in script
    assert "Smoke" in script or "smoke" in script


def test_the_gate_restores_the_environment_it_simulates() -> None:
    """A gate that leaks its simulation corrupts every later measurement.

    It rewrites ``LOCALAPPDATA`` and the working directory to measure a copy
    on a stick. The first version did not restore either, and three unrelated
    tests in other modules failed with an index that had appeared next to the
    repository — which is the same failure mode the phase exists to prevent,
    just aimed at the test suite instead of at a user.
    """
    from evaluation import distribution_gate

    before_environment = dict(os.environ)
    before_directory = Path.cwd()
    workspace = Path(tempfile.mkdtemp(prefix="us-gate-scope-"))
    local = workspace / "LOCALAPPDATA"
    folder = workspace / "copy"
    local.mkdir()
    folder.mkdir()

    with distribution_gate._as_a_copy_on_a_stick(local, folder):
        assert os.environ["LOCALAPPDATA"] == str(local)
        # Resolved on both sides. `os.chdir` does not expand an 8.3 short name,
        # so `Path.cwd()` returns whatever form it was given while
        # `folder.resolve()` always returns the long one. This passed on a
        # development machine whose TEMP is a long path and failed on the CI
        # runner, whose TEMP is `RUNNER~1`, on the same code and the same
        # commit. The invariant being checked is "the gate moved us", not "the
        # two strings match", so compare identity.
        assert Path.cwd().resolve() == folder.resolve()
        os.environ[distribution_gate.HOME_ENV] = str(workspace / "explicit")

    assert dict(os.environ) == before_environment
    assert Path.cwd() == before_directory


def test_the_gate_restores_the_environment_even_when_a_measurement_fails(
    tmp_path,
) -> None:
    from evaluation import distribution_gate

    before_environment = dict(os.environ)
    before_directory = Path.cwd()
    folder = tmp_path / "copy"
    folder.mkdir()
    local = tmp_path / "LOCALAPPDATA"
    local.mkdir()

    with pytest.raises(RuntimeError):
        with distribution_gate._as_a_copy_on_a_stick(local, folder):
            raise RuntimeError("a measurement failed")

    assert dict(os.environ) == before_environment
    assert Path.cwd() == before_directory


def test_the_portable_marker_is_documented_for_users() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert portable.PORTABLE_MARKER in readme
    assert portable.PORTABLE_DATA_DIRNAME in readme


def test_ci_builds_and_smokes_the_single_file_executable() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "universal-search-onefile.spec" in workflow
    assert "UniversalSearch-onefile" in workflow