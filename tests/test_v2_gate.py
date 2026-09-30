"""Phase 030: the v2 quality gate is itself gated.

A gate nobody tests is a gate that rots into always-green. Each test here
runs one invariant of ``evaluation.gate`` and asserts it holds — and, where it
is cheap, asserts that the check *can* fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation import gate


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


def test_the_roadmap_check_reports_what_is_still_open(
    tmp_path: Path, monkeypatch
):
    (tmp_path / "ROADMAP.md").write_text(
        "- [x] done phase\n- [ ] pending phase (030)\n", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "DOCS", tmp_path)

    result = gate.check_roadmap_has_no_open_phase()

    assert result.ok is False
    assert "pending phase" in result.detail


@pytest.mark.parametrize(
    "forbidden",
    ["socket", "urllib.request", "requests", "torch", "transformers", "numpy"],
)
def test_the_forbidden_import_set_covers_the_obvious_escapes(forbidden: str) -> None:
    assert forbidden in gate.FORBIDDEN_IMPORTS
