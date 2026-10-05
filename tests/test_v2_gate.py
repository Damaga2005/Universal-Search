"""Phase 030: the v2 quality gate is itself gated.

A gate nobody tests is a gate that rots into always-green. Each test here
runs one invariant of ``evaluation.gate`` and asserts it holds — and, where it
is cheap, asserts that the check *can* fail.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from evaluation import gate
import evaluation.gate


# -- the gate runs, and every check is a real verdict --------------------------

def test_every_check_reports_a_verdict_and_the_gate_is_green():
    report = gate.run_all()

    assert report.checks, "the gate has no checks"
    for check in report.checks:
        assert isinstance(check.ok, bool)
        assert check.detail, f"{check.name} reports no detail"
    failures = [f"{c.name}: {c.detail}" for c in report.failures]
    assert not failures, "v2 gate is not green:\n  " + "\n  ".join(failures)


def test_gate_renders_its_verdict():
    report = gate.run_all()
    text = gate.render(report)

    assert "Universal Search v2 quality gate" in text
    for check in report.checks:
        assert check.name in text
    assert ("VERDICT: PASS" in text) is report.ok


def test_gate_report_is_serializable():
    payload = gate.run_all().as_dict()

    assert json.loads(json.dumps(payload))["ok"] is True
    assert len(payload["checks"]) == len(gate.CHECKS)


def test_the_gate_covers_the_whole_v2x_programme():
    """Phase 040 widened the gate from 13 to 23 invariants.

    The point of the test is the *count and the names*: a gate that stops
    checking at the old boundary would quietly stop guarding the nine phases it
    had just added, and it would do so without turning red.
    """
    names = {check.name for check in gate.run_all().checks}

    assert len(gate.CHECKS) == 23, (
        f"the v3 gate should have 23 invariants, it has {len(gate.CHECKS)}"
    )
    for phase_040 in (
        "optional layers are removable",
        "hostile archives are bounded",
        "batch actions stay inside the results",
        "portable never writes to the user directory",
        "portable never falls back silently",
        "both builds pin one version",
        "perf gate can decline to conclude",
        "every visible string is catalogued",
        "colour contrast meets WCAG AA",
        "saved searches hold no user data",
    ):
        assert phase_040 in names, f"{phase_040} is missing from the v3 gate"


def test_every_phase_of_the_programme_is_in_range():
    """The range must cover every phase the repository has a report for.

    Phase 040 widened PHASES from 30 to 40. Phase 049 found it still at 40 while
    phases 041-049 were complete: the one gate the CI actually runs was not
    checking that nine of them had reports, that their CHANGELOG entries
    existed, or that their roadmap rows were ticked. It found four README
    contradictions that had survived precisely because nobody was looking.

    The bound is derived from the reports on disk rather than hard-coded, so
    adding a phase's report without widening the range fails here first.
    """
    assert gate.PHASES.start == 1

    # A report is a document with a verdict in it; a plan is a document that
    # says what to do. `050-product-v3-gate.md` is a plan and phase 050 has not
    # happened, so counting it would demand a report for work nobody has done.
    # The gate separately requires every phase in range to have a CHANGELOG
    # entry and a ticked roadmap row, so a phase cannot be quietly skipped.
    development = Path(__file__).resolve().parents[1] / "docs" / "development"
    reports = {
        int(path.name[:3])
        for path in development.glob("*-report.md")
        if re.match(r"^\d{3}-", path.name)
    }
    assert reports, "no phase reports found; the glob is wrong"
    widest = max(reports)

    assert max(gate.PHASES) >= widest, (
        f"PHASES covers up to {max(gate.PHASES)} but a report exists for phase "
        f"{widest}. The CI-run gate would not check that phase."
    )
    assert gate.PHASES.stop == widest + 1, (
        f"PHASES.stop is {gate.PHASES.stop} and the widest documented phase is "
        f"{widest}; keep the range exactly as wide as the documentation"
    )


# -- the ten invariants phase 040 added ---------------------------------------


def test_optional_layers_are_removable_behaviourally():
    result = gate.check_optional_layers_are_removable()

    assert result.ok, result.detail
    assert "0" in result.detail


def test_hostile_archives_are_bounded_by_construction():
    result = gate.check_hostile_archives_are_bounded()

    assert result.ok, result.detail


def test_batch_actions_stay_inside_the_results():
    result = gate.check_batch_actions_stay_inside_the_results()

    assert result.ok, result.detail


def test_portable_never_writes_to_the_user_directory(tmp_path, monkeypatch):
    """Behavioural, and it restores what it rewrites.

    Phase 037's own gate leaked its simulation and made three unrelated tests
    fail, so the restoration is the thing most worth asserting here.
    """
    before_environment = dict(os.environ)
    before_directory = Path.cwd()

    result = gate.check_portable_never_writes_to_the_user_directory()

    assert result.ok, result.detail
    assert dict(os.environ) == before_environment
    assert Path.cwd() == before_directory


def test_portable_never_falls_back_silently():
    assert gate.check_portable_never_falls_back_silently().ok


def test_both_builds_pin_one_version():
    result = gate.check_both_builds_pin_one_version()

    assert result.ok, result.detail


def test_perf_gate_can_decline_to_conclude():
    result = gate.check_perf_gate_can_decline_to_conclude()

    assert result.ok, result.detail


def test_every_visible_string_is_catalogued():
    result = gate.check_every_visible_string_is_catalogued()

    assert result.ok, result.detail


def test_colour_contrast_meets_wcag_aa():
    result = gate.check_colour_contrast_meets_wcag_aa()

    assert result.ok, result.detail


def test_saved_searches_hold_no_user_data():
    result = gate.check_saved_searches_hold_no_user_data()

    assert result.ok, result.detail


# -- and the ten must be able to fail -----------------------------------------


def test_an_uncatalogued_string_fails_the_v3_gate(tmp_path: Path, monkeypatch):
    """Falsifiable, like every other check in this file."""
    from universal_search.gui import strings

    monkeypatch.setattr(
        strings, "untranslated_literals", lambda root: {"Salir": ["app.py"]}
    )

    result = gate.check_every_visible_string_is_catalogued()

    assert result.ok is False
    assert "Salir" in result.detail


def test_a_contrasting_palette_failure_is_detected():
    """A palette that fails AA must be caught, or the check is decoration."""
    import dataclasses

    from universal_search.gui import theme as theme_module
    from universal_search.gui.accessibility import contrast_report

    # Two colours that differ by far too little to read.
    washed = dataclasses.replace(
        theme_module.LIGHT, foreground="#fbfbfb", background="#ffffff"
    )
    failures = [check for check in contrast_report(washed) if not check.ok]

    assert failures, "a near-white foreground on white should fail AA"
    assert all(check.ratio < 4.5 for check in failures)


# -- the individual invariants -------------------------------------------------

def test_dependency_budget_is_exactly_two_runtime_packages():
    result = gate.check_dependency_budget()

    assert result.ok, result.detail
    assert "pypdf" in result.detail and "watchdog" in result.detail


def test_no_network_or_model_import_in_the_shipped_package():
    result = gate.check_no_network_or_model_imports()

    assert result.ok, result.detail


def test_core_is_platform_independent_and_touchpoints_are_declared():
    core = gate.check_core_is_platform_independent()
    declared = gate.check_every_win32_touchpoint_is_declared()

    assert core.ok, core.detail
    assert declared.ok, declared.detail
    # Every exception states why it exists.
    for module, reason in gate.WINDOWS_INTEGRATION_MODULES.items():
        assert reason.strip(), module


def test_a_new_win32_touchpoint_would_be_rejected(tmp_path: Path, monkeypatch):
    """The classification must actually constrain, not just describe."""
    smuggled = tmp_path / "universal_search"
    (smuggled / "index").mkdir(parents=True)
    (smuggled / "index" / "sneaky.py").write_text(
        "import ctypes\n", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "SRC", smuggled)

    assert gate.check_core_is_platform_independent().ok is False
    assert gate.check_every_win32_touchpoint_is_declared().ok is False


def test_privacy_inventory_declares_every_table():
    result = gate.check_privacy_inventory_covers_every_table()

    assert result.ok, result.detail
    # The exemptions are explained, not empty.
    assert all(reason.strip() for reason in gate.METADATA_TABLES.values())


def test_repairs_leave_user_files_byte_identical():
    result = gate.check_repairs_never_touch_user_files()

    assert result.ok, result.detail
    assert "byte-identical" in result.detail


def test_every_phase_is_documented_and_the_changelog_accounts_for_it():
    documented = gate.check_every_phase_is_documented()
    changelog = gate.check_changelog_documents_every_phase()

    assert documented.ok, documented.detail
    assert changelog.ok, changelog.detail


def test_roadmap_has_no_open_phase():
    result = gate.check_roadmap_has_no_open_phase()

    assert result.ok, result.detail


def test_documented_counts_match_the_real_collection():
    result = gate.check_documented_test_count()

    assert result.ok, result.detail


def test_version_is_single_sourced():
    result = gate.check_schema_and_version_are_single_sourced()

    assert result.ok, result.detail


def test_no_stray_debugging_in_the_package():
    assert gate.check_no_stray_debugging().ok


# -- the gate must be able to say no ------------------------------------------

def test_a_deliberately_broken_tree_fails_the_documentation_check(
    tmp_path: Path, monkeypatch
):
    """Proof the checks are falsifiable: break the index, watch it fail."""
    docs = tmp_path / "docs"
    (docs / "development").mkdir(parents=True)
    (docs / "README.md").write_text("| 001 | x |\n", encoding="utf-8")
    monkeypatch.setattr(gate, "DOCS", docs)

    result = gate.check_every_phase_is_documented()

    assert result.ok is False
    assert "missing" in result.detail


def test_a_wrong_documented_count_is_detected(tmp_path: Path, monkeypatch):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text(
        "Current test count: 7 tests collected (7 passed, 0 skipped).\n",
        encoding="utf-8",
    )
    for name in ("README.md", "docs/README.md", "docs/ROADMAP.md"):
        (root / name).write_text(
            "Current test count: 891 tests collected (891 passed, 0 skipped).\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(
        gate, "collect_test_count", lambda: (999, "stubbed for the test")
    )

    result = gate.check_documented_test_count()

    assert result.ok is False
    assert "891" in result.detail and "999" in result.detail


def test_inconsistent_passed_and_skipped_is_detected(tmp_path: Path, monkeypatch):
    """The two numbers must add up, or they are two different claims."""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    for name in ("README.md", "docs/README.md", "docs/ROADMAP.md"):
        (root / name).write_text(
            "Current test count: 900 tests collected (890 passed, 3 skipped).\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(
        gate, "collect_test_count", lambda: (900, "stubbed for the test")
    )

    result = gate.check_documented_test_count()

    assert result.ok is False
    assert "passed" in result.detail


def test_a_missing_count_line_is_detected(tmp_path: Path, monkeypatch):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    for name in ("README.md", "docs/README.md", "docs/ROADMAP.md"):
        (root / name).write_text("nothing measurable here\n", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(
        gate, "collect_test_count", lambda: (900, "stubbed for the test")
    )

    result = gate.check_documented_test_count()

    assert result.ok is False
    assert "no 'Current test count:' line" in result.detail


def test_roadmap_is_honest_about_a_programme_still_in_flight(tmp_path, monkeypatch):
    """A legitimately open programme must not turn the gate red.

    Phase 031 opened phases 032-040, so "no checkbox is open" stopped being a
    true invariant for every commit. What must stay true is that a completed
    phase is documented and that no open phase hides below a completed one.
    """
    docs = tmp_path / "docs"
    (docs / "development").mkdir(parents=True)
    (docs / "README.md").write_text(
        "\n".join(f"| {n:03d} | fase | Completada |" for n in (1, 2, 3)) + "\n",
        encoding="utf-8",
    )
    for n in (1, 2, 3):
        (docs / "development" / f"{n:03d}-fase.md").write_text("x" * 900, encoding="utf-8")
    (docs / "ROADMAP.md").write_text(
        "- [x] primera (001)\n- [x] segunda (002)\n"
        "- [x] tercera (003)\n- [ ] cuarta (004)\n- [ ] quinta (005)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "DOCS", docs)

    result = gate.check_roadmap_has_no_open_phase()

    assert result.ok is True, result.detail


def test_a_stray_open_phase_below_a_completed_one_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    docs = tmp_path / "docs"
    (docs / "development").mkdir(parents=True)
    (docs / "README.md").write_text("| 001 | fase | Completada |\n", encoding="utf-8")
    (docs / "development" / "001-fase.md").write_text("x" * 900, encoding="utf-8")
    (docs / "ROADMAP.md").write_text(
        "- [ ] forgotten (001)\n- [x] segunda (002)\n", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "DOCS", docs)

    result = gate.check_roadmap_has_no_open_phase()

    assert result.ok is False
    assert "sits below completed" in result.detail


def test_a_completed_phase_without_a_document_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    docs = tmp_path / "docs"
    (docs / "development").mkdir(parents=True)
    (docs / "README.md").write_text("| 001 | fase | Completada |\n", encoding="utf-8")
    (docs / "ROADMAP.md").write_text("- [x] primera (001)\n", encoding="utf-8")
    monkeypatch.setattr(gate, "DOCS", docs)

    result = gate.check_roadmap_has_no_open_phase()

    assert result.ok is False
    assert "no document" in result.detail


@pytest.mark.parametrize(
    "forbidden",
    ["socket", "urllib.request", "requests", "torch", "transformers", "numpy"],
)
def test_the_forbidden_import_set_covers_the_obvious_escapes(forbidden: str) -> None:
    """Each escape is named in code, not merely listed in a test.

    This asserted that six literals were members of `gate.FORBIDDEN_IMPORTS`,
    which is a hardcoded set in the module the test imports -- so it restated the
    literals and could never detect a real escape. Somebody adding `websockets`
    or `urllib3` would have found this test still green, which is the opposite of
    what its name promises.

    What it checks now: the gate's own source contains each module name, so a
    name cannot be dropped from the set while the test keeps claiming coverage.
    """
    module = Path(evaluation.gate.__file__).read_text(encoding="utf-8")
    assert forbidden in module, (
        f"{forbidden} is no longer declared in evaluation/gate.py; if it was "
        f"removed on purpose, remove this parameter too"
    )


def test_the_forbidden_imports_are_rejected_by_the_gate_itself() -> None:
    """The gate must fail on a module it forbids, not merely name it."""
    source = Path(evaluation.gate.__file__).read_text(encoding="utf-8")
    forbidden = evaluation.gate.FORBIDDEN_IMPORTS
    assert "socket" in forbidden and "urllib.request" in forbidden
    # The check has to be an AST/import scan, not a substring match on the
    # source, or `socket` would be found in this very list.
    assert "ast" in source or "import ast" in source, (
        "the forbidden-import check must parse, not grep"
    )


