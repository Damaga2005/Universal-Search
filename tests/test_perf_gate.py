"""Phase 038: the performance gate must know when it cannot conclude.

The gate's value is not that it is fast or that its numbers are small. It is
that it refuses to publish a number it cannot defend. These tests cover that
decision logic directly, with synthetic measurements, so they run in
milliseconds and do not depend on how busy the developer's machine is.

What is deliberately *not* here: a test that runs the real benchmark. A
performance test that fails or passes depending on the machine is exactly the
instrument this phase removes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evaluation import perf_gate
from evaluation.perf_gate import (
    EXIT_FAIL,
    EXIT_INCONCLUSIVE,
    EXIT_PASS,
    LOAD_VETO_RATIO,
    METRICS,
    OS_LOAD_VETO_PERCENT,
    Metric,
    Run,
    load_verdict,
    machine_fingerprint,
    spread_verdict,
)


ROOT = Path(__file__).resolve().parents[1]


def _numbers(**overrides) -> dict[str, float]:
    """A complete, plausible metric set to perturb one key at a time."""
    base = {
        "initial_index_s": 5.0,
        "incremental_s": 0.20,
        "single_update_s": 0.24,
        "bulk_update_s": 0.28,
        "deletion_s": 0.25,
        "db_open_mean_ms": 10.0,
        "search_p50_ms": 17.0,
        "search_p95_ms": 24.0,
        "index_size_mib": 3.8,
    }
    base.update(overrides)
    return base


def _run(**overrides) -> Run:
    return Run(numbers=_numbers(**overrides), load={"calibration_best_s": 0.2})


# -- load vetoes a measurement it cannot defend ------------------------------

def test_a_quiet_machine_is_conclusive() -> None:
    verdict = load_verdict({"calibration_best_s": 0.20}, 0.20, 10)
    assert verdict.conclusive
    assert "1.00" in verdict.detail


def test_a_busy_machine_vetoes_on_the_calibration() -> None:
    """The same code ran at half speed: a latency number now describes the
    competitor for the CPU, not the product."""
    verdict = load_verdict(
        {"calibration_best_s": 0.20 * LOAD_VETO_RATIO * 1.2}, 0.20, 20
    )
    assert not verdict.conclusive
    assert "reposo" in verdict.detail


def test_a_busy_machine_vetoes_on_the_os_figure_alone() -> None:
    """Found by this phase: the first version only consulted the calibration
    and mentioned the OS figure in its docstring without enforcing it. It then
    reported "0.98x of the reference" while the machine sat at 88% CPU."""
    verdict = load_verdict({"calibration_best_s": 0.20}, 0.20, 88)
    assert not verdict.conclusive
    assert "CPU del sistema" in verdict.detail


def test_an_unknown_os_figure_does_not_veto() -> None:
    """Where the OS cannot report a load, the calibration still decides.
    Refusing to measure on a platform without a number would be as dishonest
    as ignoring one we have."""
    verdict = load_verdict({"calibration_best_s": 0.20}, 0.20, None)
    assert verdict.conclusive
    assert "CPU del sistema" not in verdict.detail


def test_a_load_just_below_the_os_threshold_still_measures() -> None:
    verdict = load_verdict(
        {"calibration_best_s": 0.20}, 0.20, OS_LOAD_VETO_PERCENT
    )
    assert verdict.conclusive


def test_no_reference_yet_is_not_a_veto() -> None:
    """The first run on a machine has nothing to compare against. That is not
    a reason to refuse: it is the run that records the reference."""
    verdict = load_verdict({"calibration_best_s": 0.20}, None, 5)
    assert verdict.conclusive
    assert "sin referencia" in verdict.detail


def test_the_calibration_workload_is_deterministic_and_not_elidable() -> None:
    assert perf_gate._calibration_workload(1000) == perf_gate._calibration_workload(1000)
    assert perf_gate._calibration_workload(1000) != perf_gate._calibration_workload(999)
    # It has to actually cost something, or it would veto nothing.
    assert perf_gate.measure_load()["calibration_best_s"] > 0


def test_the_calibration_keeps_the_best_of_several_runs() -> None:
    load = perf_gate.measure_load()
    assert load["calibration_best_s"] == min(load["calibration_all_s"])
    assert len(load["calibration_all_s"]) == perf_gate._CALIBRATION_REPEATS


# -- a metric that cannot repeat cannot conclude -----------------------------

def test_two_identical_runs_repeat() -> None:
    assert spread_verdict(_run(), _run()).repeatable


def test_two_different_runs_of_the_same_code_are_inconclusive() -> None:
    """This is the gate the phase is for. If the same build measured twice
    differs by more than what it would tolerate from another build, a
    difference of that size between two *different* builds says nothing, and
    PASS would be self-deception."""
    verdict = spread_verdict(_run(), _run(initial_index_s=6.5))
    assert not verdict.repeatable
    assert "indexado inicial" in verdict.worst


def test_a_small_difference_between_runs_is_tolerated() -> None:
    assert spread_verdict(_run(), _run(search_p50_ms=17.5)).repeatable


def test_the_spread_allowance_is_the_same_one_a_regression_gets() -> None:
    """If a change smaller than the tolerance would not fail the build, two
    runs of the *same* code must be allowed to differ by that much too.

    A flat 10% got this wrong for ``db_open_mean_ms``: an operation that takes
    about 4 ms, where one scheduler hiccup is a 100% "difference" and an
    irrelevant one. Found by running the gate on an idle machine.
    """
    metric = next(m for m in METRICS if m.key == "db_open_mean_ms")
    allowance = metric.allowed_change(4.0)
    # Half the allowance passes; double it does not.
    assert spread_verdict(
        _run(db_open_mean_ms=4.0), _run(db_open_mean_ms=4.0 + allowance / 2)
    ).repeatable
    assert not spread_verdict(
        _run(db_open_mean_ms=4.0), _run(db_open_mean_ms=4.0 + allowance * 2)
    ).repeatable


def test_index_size_must_be_identical_between_runs() -> None:
    """The index size is compared against the declared tolerance.

    This used to assert a strict equality, on the premise that "two runs
    produce the same bytes or one of them is broken". Phase 046 measured that
    premise and it does not hold: two passes of the same build over identical
    content differed by 16 KiB in the main database file, on a machine at 2%
    CPU. The traversal is sorted, the WAL is already empty, and the
    absolute-path-length theory is refuted (four fresh temp roots all produced
    79-character paths and identical totals). What moves the bytes is still
    unknown, and the gate says so instead of guessing.

    The assertion is now the one the code actually makes: the same size twice
    repeats, and a size far outside the tolerance does not. 9.9 MiB against
    3.8 is a 160% gap, which no reading of "repeatable" survives.
    """
    assert spread_verdict(_run(), _run()).repeatable
    assert not spread_verdict(_run(), _run(index_size_mib=9.9)).repeatable

    # And just inside the declared allowance is still repeatable: 3.83 MiB is
    # +0.6% on a 10% tolerance, which is the behaviour the fix intends and the
    # behaviour an equality could not express.
    assert spread_verdict(_run(), _run(index_size_mib=3.83)).repeatable


def test_the_spread_names_the_worst_metric() -> None:
    verdict = spread_verdict(
        _run(), _run(search_p95_ms=40.0, incremental_s=0.30)
    )
    assert "búsqueda p95" in verdict.worst


# -- comparing against the baseline ------------------------------------------

def test_no_change_passes() -> None:
    metric = Metric("m", "metrica", 10.0, 0.1)
    assert metric.compare(5.0, 5.0).ok


def test_an_improvement_always_passes() -> None:
    """The gate exists to catch slowdowns. Being faster is not a failure,
    however unexpected, and refusing to record it would make the next
    comparison wrong."""
    metric = Metric("m", "metrica", 1.0, 0.0)
    assert metric.compare(1.0, 5.0).ok


def test_a_small_regression_within_tolerance_passes() -> None:
    metric = Metric("m", "metrica", 10.0, 0.1)
    assert metric.compare(5.4, 5.0).ok


def test_a_large_regression_fails() -> None:
    metric = Metric("m", "metrica", 10.0, 0.1)
    comparison = metric.compare(6.5, 5.0)
    assert not comparison.ok
    assert comparison.percent == pytest.approx(30.0)


def test_the_absolute_floor_protects_fast_operations() -> None:
    """A 100% regression on a 1 ms operation is 1 ms, and failing a build over
    it would be noise. The floor is what a small metric is allowed."""
    metric = Metric("m", "metrica", 10.0, 0.5)
    assert metric.compare(1.4, 1.0).ok       # +40%, under the 0.5 floor
    assert not metric.compare(1.6, 1.0).ok   # +60%, over it


def test_the_percentage_protects_large_operations() -> None:
    """On a 10 s baseline, 10% is a second — real. The 0.5 floor that spares a
    1 ms metric would be nothing here."""
    metric = Metric("m", "metrica", 10.0, 0.5)
    assert metric.allowed_change(10.0) == pytest.approx(1.0)
    assert metric.compare(10.5, 10.0).ok
    assert not metric.compare(11.5, 10.0).ok


def test_a_metric_where_more_is_better_is_compared_the_other_way() -> None:
    """Stated rather than assumed: a future "more documents indexed per
    second" metric would otherwise be judged backwards, so a doubling of the
    good number would read as a 100% regression."""
    metric = Metric("m", "metrica", 10.0, 0.1, direction="higher_is_better")
    assert metric.compare(20.0, 10.0).ok      # twice as good
    assert not metric.compare(5.0, 10.0).ok  # half: a regression


def test_every_gated_metric_has_a_tolerance_and_a_unit() -> None:
    keys = [metric.key for metric in METRICS]
    assert len(keys) == len(set(keys))
    for metric in METRICS:
        assert metric.tolerance_pct > 0
        assert metric.tolerance_abs >= 0
        assert metric.direction in {"higher_is_worse", "higher_is_better"}
        assert metric.label and metric.label == metric.label.strip()


def test_the_gate_covers_indexing_and_search_and_size() -> None:
    """A gate that only watches one number is a gate that can be passed while
    the product regresses somewhere else."""
    keys = {metric.key for metric in METRICS}
    assert {"initial_index_s", "search_p95_ms", "index_size_mib"} <= keys


def test_a_comparison_line_shows_both_numbers_and_the_allowance() -> None:
    metric = Metric("m", "metrica", 10.0, 0.1)
    line = metric.compare(5.5, 5.0).line()
    assert "PASS" in line or "FAIL" in line
    assert "5.500" in line and "5.000" in line
    assert "permitido" in line


# -- the committed baseline ---------------------------------------------------

def test_the_baseline_declares_the_machine_it_came_from() -> None:
    payload = json.loads(
        (ROOT / "evaluation" / "perf_baseline.json").read_text(encoding="utf-8")
    )
    for key in ("processor", "python", "cpu_count"):
        assert key in payload["machine"], f"the baseline does not record {key}"
    assert payload["calibration_best_s"] > 0
    assert set(payload["numbers"]) == {metric.key for metric in METRICS}


def test_the_baseline_declares_metrics_it_does_not_yet_trust() -> None:
    """A baseline may name metrics whose reference it does not stand behind.

    The first recorded baseline did exactly that with ``db_open_mean_ms``,
    which had been measured before the warm-up fix and so carried process
    startup the current code does not measure. The file says so out loud, and
    the gate prints the warning next to the metric.
    """
    payload = json.loads(
        (ROOT / "evaluation" / "perf_baseline.json").read_text(encoding="utf-8")
    )
    provisional = payload.get("provisional", [])
    assert set(provisional) <= set(payload["numbers"]), (
        "a metric marked provisional must exist in the baseline"
    )
    if provisional:
        assert payload.get("note"), "a provisional reference must explain itself"


def test_a_baseline_from_another_machine_is_recognised() -> None:
    """Found by this phase: the comparison looked for "processor" at the top
    level of the file instead of inside "machine", found nothing, and printed
    "different machine" while displaying two identical dicts."""
    recorded = {"machine": {"processor": "Intel", "python": "3.12.0",
                             "cpu_count": 4}}
    assert not perf_gate._same_machine(recorded, machine_fingerprint())
    same = {"machine": dict(machine_fingerprint())}
    assert perf_gate._same_machine(same, machine_fingerprint())


def test_the_baseline_is_valid_json_with_a_phase_marker() -> None:
    payload = json.loads(
        (ROOT / "evaluation" / "perf_baseline.json").read_text(encoding="utf-8")
    )
    assert payload["phase"] == "038"


def _read(capsys) -> str:
    """Captured stdout as one string.

    Written out rather than inlined as `capsys.readouterr().out` because an
    `in` comparison written that way failed against output that demonstrably
    contained the text; binding it first is also easier to read.
    """
    return capsys.readouterr().out


# -- the wiring: does main() honour its own rules? ---------------------------
#
# The decision functions above are covered by synthetic values. These tests
# cover the part that actually runs in CI or on a developer's machine: that
# main() turns those decisions into exit codes, and that the promises in the
# module docstring hold at the point where the baseline is written.
#
# The measurement itself is stubbed. That is honest and it is the point: a test
# that ran the real benchmark would be a performance test, which is the
# instrument this phase exists to remove.


@pytest.fixture
def isolated_baseline(tmp_path, monkeypatch):
    """Redirect the committed baseline to a temporary file."""
    target = tmp_path / "perf_baseline.json"
    monkeypatch.setattr(perf_gate, "BASELINE_PATH", target)
    return target


def _stub_environment(monkeypatch, *, os_load: int, calibration: float,
                      calibration_reference: float | None = None) -> None:
    monkeypatch.setattr(perf_gate, "os_cpu_load", lambda: os_load)
    monkeypatch.setattr(
        perf_gate, "measure_load",
        lambda: {"calibration_best_s": calibration, "calibration_all_s": [calibration]},
    )


def _write_baseline(path: Path, numbers: dict[str, float],
                    calibration: float = 0.2) -> None:
    path.write_text(
        json.dumps({
            "phase": "038",
            "profile": 1000,
            "machine": perf_gate.machine_fingerprint(),
            "calibration_best_s": calibration,
            "numbers": numbers,
        }),
        encoding="utf-8",
    )


def _run_main(monkeypatch, numbers: dict[str, float] | None = None,
              *, repeats: int = 2) -> None:
    """Stub the measurement so main() runs its whole decision path."""
    produced = _numbers() if numbers is None else numbers
    calls = {"count": 0}

    def fake_measure(profile: int) -> Run:
        calls["count"] += 1
        return Run(numbers=dict(produced), load={"calibration_best_s": 0.2})

    monkeypatch.setattr(perf_gate, "_measure_once", fake_measure)
    monkeypatch.setattr(perf_gate, "main", perf_gate.main)
    perf_gate.main()


def test_a_busy_machine_runs_nothing_and_writes_nothing(
    isolated_baseline, monkeypatch, capsys
) -> None:
    """The central promise: a busy machine produces no number at all.

    Not "a number with a footnote". Zero, and a baseline file that does not
    exist afterwards.
    """
    _stub_environment(monkeypatch, os_load=91, calibration=0.2)
    measured = {"called": False}

    def never(profile: int) -> Run:
        measured["called"] = True
        return Run(numbers=_numbers())

    monkeypatch.setattr(perf_gate, "_measure_once", never)
    monkeypatch.setattr(sys, "argv", ["perf_gate"])

    assert perf_gate.main() == EXIT_INCONCLUSIVE
    assert not measured["called"]
    assert not isolated_baseline.exists()


def test_a_conclusive_run_records_the_baseline(
    isolated_baseline, monkeypatch, capsys
) -> None:
    _stub_environment(monkeypatch, os_load=5, calibration=0.2)
    monkeypatch.setattr(
        perf_gate, "_measure_once", lambda profile: Run(numbers=_numbers())
    )
    monkeypatch.setattr(sys, "argv", ["perf_gate"])

    assert perf_gate.main() == EXIT_PASS
    assert isolated_baseline.exists()
    payload = json.loads(isolated_baseline.read_text(encoding="utf-8"))
    assert payload["phase"] == "038"
    assert set(payload["numbers"]) == {m.key for m in METRICS}


def test_two_identical_passes_pass(isolated_baseline, monkeypatch, capsys) -> None:
    _write_baseline(isolated_baseline, _numbers())
    _stub_environment(monkeypatch, os_load=5, calibration=0.2)
    calls = {"count": 0}

    def fake_measure(profile: int) -> Run:
        calls["count"] += 1
        return Run(numbers=_numbers())

    monkeypatch.setattr(perf_gate, "_measure_once", fake_measure)
    monkeypatch.setattr(sys, "argv", ["perf_gate"])

    assert perf_gate.main() == EXIT_PASS
    assert calls["count"] == 2, "a pass that cannot repeat must not conclude"
    assert "VEREDICTO: PASS" in _read(capsys)


def test_a_regression_fails(isolated_baseline, monkeypatch, capsys) -> None:
    _write_baseline(isolated_baseline, _numbers())
    _stub_environment(monkeypatch, os_load=5, calibration=0.2)
    slower = _numbers(search_p95_ms=45.0)
    monkeypatch.setattr(
        perf_gate, "_measure_once", lambda profile: Run(numbers=dict(slower))
    )
    monkeypatch.setattr(sys, "argv", ["perf_gate"])

    assert perf_gate.main() == EXIT_FAIL
    assert "VEREDICTO: FAIL" in _read(capsys)


def test_two_inconsistent_passes_are_inconclusive_not_passing(
    isolated_baseline, monkeypatch, capsys
) -> None:
    """Two passes that disagree must not produce PASS, even though every
    metric is inside its tolerance when compared to the baseline."""
    _write_baseline(isolated_baseline, _numbers())
    _stub_environment(monkeypatch, os_load=5, calibration=0.2)
    passes = iter([_numbers(), _numbers(search_p95_ms=60.0)])
    monkeypatch.setattr(
        perf_gate, "_measure_once", lambda profile: Run(numbers=next(passes))
    )
    monkeypatch.setattr(sys, "argv", ["perf_gate"])

    assert perf_gate.main() == EXIT_INCONCLUSIVE
    out = capsys.readouterr().out
    assert "DISPERSIÓN" in out
    assert "VEREDICTO: PASS" not in out


def test_an_unrecorded_baseline_is_created_not_compared(
    isolated_baseline, monkeypatch, capsys
) -> None:
    """The first run on a machine has nothing to compare against; it records
    and says so instead of inventing a PASS."""
    _stub_environment(monkeypatch, os_load=5, calibration=0.2)
    monkeypatch.setattr(
        perf_gate, "_measure_once", lambda profile: Run(numbers=_numbers())
    )
    monkeypatch.setattr(sys, "argv", ["perf_gate"])

    assert perf_gate.main() == EXIT_PASS
    assert "BASELINE REGISTRADA" in _read(capsys)


def test_the_inconclusive_report_says_nothing_was_written(
    isolated_baseline, monkeypatch, capsys
) -> None:
    _stub_environment(monkeypatch, os_load=95, calibration=0.2)
    monkeypatch.setattr(sys, "argv", ["perf_gate"])

    assert perf_gate.main() == EXIT_INCONCLUSIVE
    out = capsys.readouterr().out
    assert "No se ha medido nada" in out
    assert "no se ha escrito ninguna cifra" in out


# -- the exit codes -----------------------------------------------------------

def test_inconclusive_is_not_failure() -> None:
    """Two answers for three situations cannot gate a pipeline. The audit of
    2.0.0 had to publish a footnote instead of a verdict because the middle
    state had no code."""
    assert EXIT_PASS == 0
    assert EXIT_FAIL == 1
    assert EXIT_INCONCLUSIVE == 2
    assert EXIT_INCONCLUSIVE != EXIT_FAIL
