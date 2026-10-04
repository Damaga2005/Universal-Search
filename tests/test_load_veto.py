"""Phase 047b: the load veto, tested instead of waited for.

`perf_gate` has measured machine load and withheld its verdict since phase 045b.
`interaction_gate` got the same. `fuzzy_gate` and `suggest_gate` did not, and
they were the two gates that produced verdicts nobody could act on: NO SHIP at
12.6 ms with a game running at 88% CPU, and SHIP at 4.4 ms with a machine at 76%
-- one failing and one passing on the same day, for the same reason.

Testing that requires a quiet machine would mean the test only runs when the
machine is quiet, which is exactly when the veto is not needed. So the load
signals are injected instead, and the three behaviours are pinned directly:

  * busy before measuring  -> nothing is measured, and the verdict is
    INCONCLUYENTE rather than a number about somebody else's software;
  * busy after measuring   -> the verdict that was about to be printed is
    withheld, because the machine became busy during the run;
  * quiet throughout       -> the gate measures and rules as before.

That middle case is the one a single pre-flight check gets wrong. A bursty
neighbour lets the machine read idle between bursts: measured here, the first
version of this fix sampled once at 76% at the start... no -- it sampled at
under 60%, ran anyway, and reported NO SHIP while the machine was at 76%. The
load arrives in bursts, so one sample catches the gap between them.

There is also a test that the methodology has exactly one implementation.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from evaluation import fuzzy_gate, perf_gate, suggest_gate


def _calibration(seconds: float = 0.05) -> dict[str, float]:
    return {"calibration_best_s": seconds, "calibration_all_s": [seconds]}


@pytest.fixture
def quiet(monkeypatch):
    """A machine that is idle, and stays idle."""
    monkeypatch.setattr(perf_gate, "measure_load", lambda: _calibration())
    monkeypatch.setattr(perf_gate, "os_cpu_load", lambda: 4)
    monkeypatch.setattr(perf_gate, "LOAD_VETO_RATIO", 1.35)
    monkeypatch.setattr(perf_gate, "OS_LOAD_VETO_PERCENT", 60)


@pytest.fixture
def busy_before(monkeypatch):
    """Another program is already using the CPU when the gate starts."""
    monkeypatch.setattr(perf_gate, "measure_load", lambda: _calibration())
    monkeypatch.setattr(perf_gate, "os_cpu_load", lambda: 88)
    monkeypatch.setattr(perf_gate, "OS_LOAD_VETO_PERCENT", 60)


@pytest.fixture
def busy_after(monkeypatch):
    """Idle at the start, saturated by the end -- the bursty neighbour."""
    readings = iter([5, 91])
    monkeypatch.setattr(perf_gate, "measure_load", lambda: _calibration())
    monkeypatch.setattr(perf_gate, "os_cpu_load", lambda: next(readings))
    monkeypatch.setattr(perf_gate, "OS_LOAD_VETO_PERCENT", 60)


@pytest.fixture
def preserve_baseline():
    """Restore the baseline files a gate writes, whatever they write.

    Every test here runs a gate's `main()`, and a gate's `main()` writes its
    record. The first version left that on disk, and the suite produced a
    baseline containing a calibration of 0.05 s -- a number injected by a test
    fixture, not measured on any machine. A test that changes a tracked
    artefact is a test that can lie to the next person who reads it.

    Both gates are covered rather than just the one under test: a fixture that
    restores only its own gate leaves the other dirty for the next test in the
    run, which is a harder failure to see.
    """
    originals = {}
    for module, name in (
        (suggest_gate, "suggest_baseline.json"),
        (fuzzy_gate, "fuzzy_baseline.json"),
    ):
        path = pathlib.Path(module.__file__).with_name(name)
        originals[path] = path.read_text(encoding="utf-8") if path.exists() else None
    yield
    for path, text in originals.items():
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(text, encoding="utf-8")


# -- perf_gate owns the methodology ------------------------------------------

def test_the_os_figure_alone_can_veto(quiet):
    """One signal is enough; the other being fine does not rescue it.

    The calibration asks whether *this process* was slowed. The OS figure asks
    whether the machine as a whole was busy, which is the difference between a
    busy disk and a CPU that was not ours. A slow neighbour leaves the
    calibration looking perfectly healthy.
    """
    monkeypatched = perf_gate.load_gate()
    assert monkeypatched.conclusive is True

    import evaluation.perf_gate as module

    original = module.os_cpu_load
    module.os_cpu_load = lambda: 88
    try:
        assert module.load_gate().conclusive is False
    finally:
        module.os_cpu_load = original


def test_a_missing_reference_only_disables_the_calibration_signal(monkeypatch):
    """Without a baseline the OS figure still has to work.

    A gate's first run has no recorded calibration, so `reference` is None. If
    that silenced the whole check, a brand-new gate would report latency on a
    saturated machine.
    """
    monkeypatch.setattr(perf_gate, "measure_load", lambda: _calibration())
    monkeypatch.setattr(perf_gate, "os_cpu_load", lambda: 75)
    assert perf_gate.load_gate(None).conclusive is False


def test_a_slower_calibration_than_the_reference_also_vetoes(monkeypatch):
    """The second signal, for when the OS figure cannot be read."""
    slow = 0.20
    monkeypatch.setattr(perf_gate, "measure_load", lambda: _calibration(slow))
    monkeypatch.setattr(perf_gate, "os_cpu_load", lambda: None)
    monkeypatch.setattr(perf_gate, "LOAD_VETO_RATIO", 1.35)

    assert perf_gate.load_gate(0.10).conclusive is False, (
        "0.20 s against a 0.10 s reference is 2x and must withhold the verdict"
    )

    # The other direction, which matters more: a machine merely *a little*
    # slower must still be allowed to conclude, or the veto becomes a gate that
    # never passes. This assertion failed first time with 0.20 s against a
    # 0.12 s reference -- 1.67x, which the rule correctly vetoes. The test was
    # wrong about the arithmetic, not the rule.
    monkeypatch.setattr(perf_gate, "measure_load", lambda: _calibration(0.12))
    assert perf_gate.load_gate(0.10).conclusive is True, (
        "0.12 s against 0.10 s is 1.2x, inside the 1.35x allowance"
    )


# -- the two gates that were missing it --------------------------------------

@pytest.mark.parametrize("module", [suggest_gate, fuzzy_gate])
def test_a_busy_machine_withholds_the_verdict(
    module, busy_before, preserve_baseline, capsys
):
    """The whole defect, in one test per gate.

    Before: `suggest_gate` printed NO SHIP at 12.6 ms and `fuzzy_gate` printed
    SHIP at 4.4 ms, on the same day, with a game running. Both verdicts were
    about the game.
    """
    code = module.main()

    assert code == 2, "a busy machine must exit 2, like perf_gate does"
    output = capsys.readouterr().out
    assert "INCONCLUYENTE" in output
    assert "88%" in output, "the reason must name the measurement that caused it"
    assert "NO SHIP" not in output
    assert "SHIP" not in output.replace("INCONCLUYENTE", "")


@pytest.mark.parametrize("module", [suggest_gate, fuzzy_gate])
def test_a_machine_that_becomes_busy_mid_measurement_also_withholds(
    module, busy_after, preserve_baseline, capsys
):
    """The case a single pre-flight check gets wrong.

    The gate starts on an idle machine, measures, and the neighbour wakes up.
    Printing SHIP then would be publishing a latency figure measured under
    conditions nobody declared.
    """
    code = module.main()

    assert code == 2
    output = capsys.readouterr().out
    assert "INCONCLUYENTE despues de medir" in output, output[-400:]
    assert "91%" in output


@pytest.mark.parametrize("module", [suggest_gate, fuzzy_gate])
def test_a_quiet_machine_still_measures_and_rules(
    module, quiet, preserve_baseline, capsys
):
    """The veto must not turn the gate into a permanent INCONCLUYENTE."""
    code = module.main()

    output = capsys.readouterr().out
    assert code in (0, 1), f"a quiet machine must reach a verdict: {output[-300:]}"
    assert "INCONCLUYENTE" not in output
    assert ("VEREDICTO: SHIP" in output) or ("NO SHIP" in output)


@pytest.mark.parametrize("module", [suggest_gate, fuzzy_gate])
def test_the_gate_records_the_load_it_measured_against(
    module, quiet, preserve_baseline
):
    """The calibration is written to the baseline, so the next run can compare.

    A gate that records only its verdict leaves no way to tell afterwards
    whether a number was taken on a busy machine, and no reference for the
    second signal.

    This runs against the real `evaluation/` directory rather than a temporary
    one. Repointing `ROOT` was the first attempt and it broke the gates in a way
    that had nothing to do with the veto: `fuzzy_gate` reads
    `ROOT / "pyproject.toml"` to check declared runtime dependencies, so a
    temporary root makes the gate fail on a missing file before it ever gets to
    the measurement. The file it writes is restored afterwards.
    """
    baseline = pathlib.Path(module.__file__).with_name(
        "suggest_baseline.json" if module is suggest_gate
        else "fuzzy_baseline.json"
    )
    module.main()
    assert baseline.exists(), f"{baseline.name} was not written"
    payload = json.loads(baseline.read_text(encoding="utf-8"))

    assert payload.get("calibration_best_s") is not None, (
        "without a recorded calibration the next run has no reference and the "
        "check loses its second signal"
    )
    assert "load_before" in payload and "load_after" in payload, (
        "both readings have to be recorded: the OS figure is the signal that "
        "caught the machine in the first place, and a baseline that says only "
        "how the run ended cannot explain how it began"
    )


def test_there_is_exactly_one_implementation_of_the_load_check():
    """A load check written twice diverges, and the divergence is invisible
    until the two disagree on a verdict for the same machine."""
    source = (
        __import__("pathlib").Path(perf_gate.__file__).read_text(encoding="utf-8")
    )
    for module in (suggest_gate, fuzzy_gate):
        text = (
            __import__("pathlib").Path(module.__file__).read_text(encoding="utf-8")
        )
        assert "perf_gate.load_gate" in text, (
            f"{module.__name__} must reuse perf_gate's check, not its own"
        )
        # No private copy of the constants that define the thresholds.
        for constant in ("OS_LOAD_VETO_PERCENT =", "LOAD_VETO_RATIO ="):
            assert constant not in text, (
                f"{module.__name__} redefines {constant!r}; the thresholds "
                f"belong to perf_gate so they cannot drift apart"
            )
    assert "OS_LOAD_VETO_PERCENT = 60" in source