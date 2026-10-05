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

#: Phase 050. Five gates read argv through `perf_gate.require_conclusive()`
#: rather than declaring the flag themselves, so their accepted flags live here.
PERF_GATE_SOURCE = (ROOT / "evaluation" / "perf_gate.py").read_text(
    encoding="utf-8"
)
PYPROJECT = ROOT / "pyproject.toml"

# The only two third-party packages allowed at runtime (phase 020 decision,
# re-asserted every phase since: no embedding model, no HTTP client, no
# telemetry SDK).
ALLOWED_RUNTIME_DEPENDENCIES = {"pypdf", "watchdog"}

# Phase 050: `gates` and `heavy-gates` were added when the audit found that
# fifteen of the nineteen gates had never been run by the build. They gate
# as strictly as `quality` does -- `test_no_gating_job_may_continue_on_error`
# reads this set, so leaving them out here would have quietly exempted them.
GATING_JOBS = {"quality", "gates", "heavy-gates", "package"}
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
    # Phase 049 replaced `artifacts.sha256` with `SHA256SUMS.txt` and left this
    # assertion behind. It then kept passing -- against the comment in ci.yml
    # that explains why the assertion was meaningless. A test that passes on
    # its own explanation of itself is not a test.
    assert "SHA256SUMS.txt" in body, (
        "the workflow must write the hash manifest a user can verify"
    )
    assert "verify-hashes.ps1" in body, (
        "and it must verify it with the script a user would run, not just "
        "produce a file nobody reads"
    )
    # Read the dependency as a set. This used to assert the literal string
    # "needs: quality", which is a format, not a contract: it broke the moment
    # phase 050 made `package` also wait for the two new evidence jobs, and the
    # failure said nothing about whether the release still waits for anything.
    dependencies = re.search(r"^\s*needs:\s*(.+)$", body, re.M)
    assert dependencies, "the package job declares no dependency"
    declared = set(re.findall(r"[a-z-]+", dependencies.group(1)))
    assert "quality" in declared, (
        f"package must wait for the suite; it declares {sorted(declared)}"
    )


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
    """The range is declared in one place, and the CI proves the same set.

    Phase 048 changed the value this test pinned, on purpose. It asserted
    `requires-python == ">=3.12"` and `python-version: "3.12"` -- an open promise
    and a single version of proof, which is exactly the mismatch the phase was
    written to close: the package claimed 3.15 and beyond while the development
    machine ran 3.14 and the CI proved only 3.12.

    What the test is *for* survives the change, and matters more now: the range
    must appear once, and the CI must cover exactly it. `evaluation.
    portability_gate` asserts that in full (W1-W3, W6, W7); this stays as the
    cheap CI-side check that the two files agree at all.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    spec = data["project"]["requires-python"]
    assert spec == ">=3.12,<3.15", (
        f"the declared range changed to {spec!r}; update this test and "
        f"docs/SUPPORT.md together, or they will disagree"
    )

    workflow = WORKFLOW.read_text(encoding="utf-8")
    # The gating job proves the range, rather than pinning one version.
    gating = workflow.split("  core-portability:")[0]
    assert "matrix:" in gating, (
        "the gating job pins a version instead of declaring a matrix, so an "
        "open range could never be covered"
    )
    for version in ("3.12", "3.13", "3.14"):
        assert version in gating, f"{version} is in the declared range and the CI does not prove it"
    assert 'python-version: "3.12"' not in gating, (
        "a pinned version in the gating job contradicts the matrix"
    )


# -- phase 050: no gate may exist without the build running it ----------------

#: A module is a gate if it ends in `_gate.py`. Derived from the tree rather than
#: written out, because a list maintained by hand is a list that goes stale: the
#: fifteen gates this test is about were each in the tree, each had a `main`,
# and none appeared in any list, because the only list was the workflow.
def declared_flags(source: str) -> set[str]:
    """Every ``--flag`` a module mentions, as whole tokens.

    A substring search is not good enough and fails in the one direction that
    matters: a misspelled flag is usually a truncation of the real one, and a
    truncation *is* a substring of the original.
    """
    return set(re.findall(r"--[a-z][a-z0-9-]*", source))


def gate_modules() -> set[str]:
    return {
        f"evaluation.{path.stem}"
        for path in (ROOT / "evaluation").glob("*_gate.py")
    }


def test_every_gate_in_the_tree_is_run_by_the_build(workflow: str) -> None:
    """A gate nothing runs is a document with a `main()`.

    This is the invariant whose absence produced the finding. Nineteen gates
    exist; before phase 050 the workflow ran four of them, and the audit found
    the rest by reading reports rather than by anything failing. There was no
    test to fail. This is that test.
    """
    run = set(re.findall(r"python -m (evaluation\.[a-z_]+)", workflow))
    missing = sorted(gate_modules() - run)
    assert not missing, (
        f"{len(missing)} gate(s) exist in evaluation/ and no CI step runs them: "
        f"{missing}. A gate that nothing runs cannot fail, so it is documentation"
    )


def test_no_gate_is_run_with_a_flag_that_does_not_exist(workflow: str) -> None:
    """A mistyped flag turns a gate into a step that cannot fail.

    These gates exit 2 -- INCONCLUYENTE -- when they decline to conclude, and a
    build that tolerates exit 2 has wired in a gate that cannot stop it. So the
    flag has to reach the verdict, and "reach" includes delegation: five of these
    gates call `perf_gate.require_conclusive()`, which is where the string lives,
    so demanding the literal in each gate's own source would be wrong.

    Written once already and the first version could not fail: it captured the
    flag with `[a-z0-9-]*` placed directly after the module name, so the pattern
    matched the empty string on every line, the loop skipped each one, and the
    assertion was never evaluated.

    The second version did fail, but for the wrong reason it still missed a
    misspelling: it tested the flag with `in`, and a misspelled flag here is a
    *truncation*, which is a substring of the real one. Both were found by
    breaking the workflow six ways and watching this assertion not notice --
    twice, which is the argument for doing it at all.
    """
    for gate in sorted(gate_modules()):
        name = gate.split(".")[-1]
        source = (ROOT / "evaluation" / f"{name}.py").read_text(encoding="utf-8")
        delegates = "require_conclusive" in source
        # As whole tokens, not as substrings. `"--require-conclusiv" in source`
        # is True for a source containing `"--require-conclusive"`, because the
        # truncated flag is a prefix of the real one -- so a substring test
        # declares a typo to be a valid flag, which is the exact failure this
        # invariant exists to catch.
        declared = declared_flags(source) | (
            declared_flags(PERF_GATE_SOURCE) if delegates else set()
        )
        for tail in re.findall(rf"python -m {re.escape(gate)}([^\n]*)", workflow):
            for token in tail.split():
                if not token.startswith("-"):
                    continue
                handled = token in declared
                assert handled, (
                    f"CI passes {token!r} to {gate}, which neither declares it "
                    f"nor delegates to the module that does. An unknown flag is "
                    f"ignored, and this gate would exit 2 rather than 1 when it "
                    f"declines to conclude -- a typo here turns a gate into a "
                    f"step that cannot fail"
                )



def test_the_product_gate_runs_where_its_artefact_exists(jobs: dict[str, str]) -> None:
    """The product gate needs the built executable, so it belongs in `package`.

    Wired into the `gates` evidence job, where no artefact exists, it found no
    `product_scenario.json`, reported on nothing, and then said SHIP. Running it
    after the build is what makes its verdict mean anything, and the ordering is
    the part that can regress silently: move the step up one job and it is
    green again.
    """
    package = jobs["package"]
    build = package.index("Build the executables")
    scenario = package.index("python evaluation/product_scenario.py")
    gate = package.index("python -m evaluation.product_gate")
    assert build < scenario < gate, (
        "the scenario must run after the build and before the gate that reads "
        "its record"
    )
    assert "python -m evaluation.product_gate" not in jobs["gates"], (
        "the product gate must not run in a job with no artefact: with no "
        "scenario record it has nothing to conclude from"
    )


def test_the_latency_gates_are_wired_to_demand_a_number(workflow: str) -> None:
    """The five latency gates measure time, so they must not be allowed to shrug.

    Four of the five are the ones this programme could not close locally: they
    returned INCONCLUYENTE for as long as a game was running, and the correct
    local answer was exactly that. On a runner there is no neighbour to blame, so
    the same condition has to stop the build -- and it only does if the switch is
    actually passed.
    """
    for gate in (
        "evaluation.perf_gate",
        "evaluation.interaction_gate",
        "evaluation.ux_gate",
        "evaluation.fuzzy_gate",
        "evaluation.suggest_gate",
    ):
        pattern = rf"python -m {re.escape(gate)} --require-conclusive"
        assert re.search(pattern, workflow), (
            f"{gate} measures latency and is wired without "
            f"--require-conclusive, so a busy runner exits 2 and the build "
            f"carries on as if the gate had measured something"
        )


def test_the_expensive_gates_are_not_in_the_suite_job(jobs: dict[str, str]) -> None:
    """The split by cost is a claim about the timeout; keep it honest.

    Measured on a machine at ~90% CPU (`evaluation/ci_gate_costs.json`):
    `scale_gate` 333 s, `storage_gate` 120 s, everything else under 50 s. The
    suite job already runs 1467 tests. Adding six minutes of gate to it is how a
    job starts timing out intermittently and being re-run, which is a slower way
    of saying nothing.
    """
    expensive = ("evaluation.scale_gate", "evaluation.storage_gate")
    assert expensive and "quality" in jobs
    for gate in expensive:
        assert gate not in jobs["quality"], (
            f"{gate} costs minutes; it belongs in its own job"
        )
    assert "heavy-gates" in jobs, "the expensive gates need a job of their own"


def test_a_release_waits_for_the_gates_it_claims_to_honour(jobs: dict[str, str]) -> None:
    """`package` produces the artefact. It may not ship on a red evidence job."""
    body = jobs["package"]
    needs = re.search(r"needs:\s*\[([^\]]*)\]|needs:\s*(\S+)", body)
    assert needs, "the package job declares no dependency on anything"
    declared = set(re.findall(r"[a-z-]+", needs.group(0).split(":", 1)[1]))
    for job in ("quality", "gates", "heavy-gates"):
        assert job in declared, (
            f"package does not need {job}; a release would build while the "
            f"evidence for it was still running or already red. This programme "
            f"does not ship a phase whose gate has not been measured, and the "
            f"artefact is the most consequential thing here"
        )
