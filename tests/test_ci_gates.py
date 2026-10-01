"""Phase 029: the release gates are a contract, not a convention.

These tests read the workflow and the packaging metadata and fail when a
required gate disappears, when a gating job is allowed to fail, or when a
third runtime dependency sneaks in. A release pipeline nobody can verify is
a release pipeline nobody can trust.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = ROOT / "pyproject.toml"

# The only two third-party packages allowed at runtime (phase 020 decision,
# re-asserted every phase since: no embedding model, no HTTP client, no
# telemetry SDK).
ALLOWED_RUNTIME_DEPENDENCIES = {"pypdf", "watchdog"}

GATING_JOBS = {"quality", "package"}
PROBE_JOB = "core-portability"


def split_jobs(workflow: str) -> dict[str, str]:
    """Split ``ci.yml`` into ``job name -> job body``.

    PyYAML is deliberately not a dependency, so the workflow is read as text.
    The parsing is intentionally simple and only ever used to look at job
    bodies; the structural invariants are asserted separately.
    """
    found: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in workflow.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if in_jobs:
            job = re.match(r"^  ([a-z0-9-]+):\s*$", line)
            if job:
                current = job.group(1)
                found[current] = []
                continue
            if current is not None and re.match(r"^ {0,3}\S", line):
                current = None  # a new top-level key: this job is finished
            if current is not None:
                found[current].append(line)
    return {name: "\n".join(body) for name, body in found.items()}


def dependency_names(entries: list[str]) -> set[str]:
    return {
        re.split(r"[<>=!\[ ]", entry, maxsplit=1)[0].strip().lower()
        for entry in entries
    }


@pytest.fixture(scope="module")
def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def jobs(workflow: str) -> dict[str, str]:
    return split_jobs(workflow)


# -- workflow shape -----------------------------------------------------------

def test_every_gating_job_exists(jobs: dict[str, str]) -> None:
    assert GATING_JOBS <= set(jobs)
    assert PROBE_JOB in jobs  # the honest non-gating probe


def test_no_gating_job_may_continue_on_error(jobs: dict[str, str]) -> None:
    for name in GATING_JOBS:
        assert "continue-on-error" not in jobs[name], (
            f"job {name} gates the release and may not tolerate failure"
        )
    # The probe is the one place where tolerating failure is the point.
    assert "continue-on-error: true" in jobs[PROBE_JOB]


def test_workflow_is_read_only_and_serialized(workflow: str) -> None:
    assert "permissions:\n  contents: read" in workflow
    assert "concurrency:" in workflow
    assert "cancel-in-progress: true" in workflow


def test_workflow_is_structurally_sane_without_a_yaml_dependency(
    workflow: str, jobs: dict[str, str]
) -> None:
    # The failure mode that actually happens with a hand-edited workflow is a
    # stray tab or a job without a runner. Both are cheap to catch here.
    assert "\t" not in workflow
    assert workflow.count("runs-on:") == len(jobs)
    for name, body in jobs.items():
        assert "steps:" in body, f"job {name} has no steps"
        assert "uses:" in body or "run:" in body, f"job {name} runs nothing"


def test_quality_job_runs_every_mandatory_gate(jobs: dict[str, str]) -> None:
    body = jobs["quality"]
    assert "python -m pyflakes src tests benchmarks evaluation" in body
    assert "python -m pytest tests -q" in body
    # Per-phase suites stay named in the log: a regression in one layer should
    # name itself instead of hiding inside a 900-test run.
    for suite in (
        "tests/test_release.py",
        "tests/test_reliability.py",
        "tests/test_evaluation.py",
        "tests/test_privacy.py",
        "tests/test_observability.py",
        "tests/test_recovery.py",
        "tests/test_windows_shell.py",
        "tests/test_ci_gates.py",
    ):
        assert suite in body, f"{suite} is not gated in CI"
    # The quality gate is a command, not a pytest module: CI must run it
    # directly, because it re-reads the tree it ships with.
    assert "python -m evaluation.gate" in body
    # Phase 040 added ten invariants, so the v2 gate suite has grown. It must
    # still be gated, or the invariants added last are the first to rot.
    assert "tests/test_v2_gate.py" in body


def test_package_job_builds_and_smokes_the_real_executables(jobs: dict[str, str]) -> None:
    body = jobs["package"]
    assert "PyInstaller packaging/universal-search.spec" in body
    assert "universal-search.exe" in body
    assert "UniversalSearch.exe" in body
    assert "artifacts.sha256" in body
    assert "needs: quality" in body


def test_package_smoke_covers_the_operator_surfaces(jobs: dict[str, str]) -> None:
    body = jobs["package"]
    assert "diagnose self-test" in body
    assert "diagnose export" in body
    assert "diagnose recover orphan-derived" in body
    assert "privacy show" in body


def test_artifacts_survive_a_failed_smoke(jobs: dict[str, str]) -> None:
    """Hashes and the sanitized bundle upload even when a later step fails."""
    body = jobs["package"]
    assert "if: always()" in body
    assert "artifacts-support.json" in body


def test_core_probe_excludes_only_windows_only_suites(jobs: dict[str, str]) -> None:
    body = jobs[PROBE_JOB]
    ignored = set(re.findall(r"--ignore=(\S+)", body))
    assert ignored == {
        "tests/test_gui.py",
        "tests/test_gui_ux.py",
        "tests/test_hotkey.py",
        "tests/test_release.py",
    }
    # Recovery and observability never touch Win32: excluding them would hide
    # exactly the regressions the probe exists to find.
    assert "tests/test_recovery.py" not in ignored
    assert "tests/test_observability.py" not in ignored


def test_core_probe_installs_no_build_or_lint_extras(jobs: dict[str, str]) -> None:
    body = jobs[PROBE_JOB]
    assert "pip install -e . pytest" in body
    assert "pyinstaller" not in body.lower()
    assert "pyflakes" not in body.lower()


# -- dependency contract ------------------------------------------------------

def test_runtime_dependencies_stay_minimal() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert dependency_names(data["project"]["dependencies"]) == (
        ALLOWED_RUNTIME_DEPENDENCIES
    )


def test_build_extra_is_optional_and_disjoint_from_runtime() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert set(extras) == {"build"}
    assert not ALLOWED_RUNTIME_DEPENDENCIES & dependency_names(extras["build"])


def test_supported_python_range_is_declared_once() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == ">=3.12"
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'python-version: "3.12"' in workflow
