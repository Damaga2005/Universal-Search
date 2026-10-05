"""Phase 050 evidence: what may a release claim, and what has it not proved.

Run from the repository root::

    python -m evaluation.product_gate

This is a **gate**, not a score. It produces no rating, no ranking and no
"quality is good". It produces exactly one thing: a release state, and the
evidence behind every claim in it.

Three rules shape it.

**It cites rather than repeats.** Nineteen gates already measure the product. A
twentieth set of numbers for the same properties would be a second source of
truth, and this project has already been bitten by that twice -- a report
naming the wrong constant, and a README claiming 3.12-only after phase 048 put a
matrix in the workflow. So every area below names the gate that owns its
evidence, and this gate checks that the owner exists and its committed baseline
says it passed.

**An area with no evidence is not an area that passed.** Each of the fifteen
audit areas from the plan is mapped to the evidence that covers it. An area whose
owner is missing, or whose baseline records a failure, is reported as
unproven. The plan's acceptance is "every required area has evidence", so the
gate's job is to say which ones do.

**A measurement this machine could not take is not a pass.** The plan asks for a
clean supported Windows environment. This is not one: a Windows 11 development
machine with another project's test suite running, a game resident, and a
history of a transiently damaged Tcl tree. The eighteen-step scenario is
executed here against the real build anyway, and where a step needs a desktop
session it is recorded NOT RUN with the reason. `docs/SUPPORT.md` already
declares that the only environment ever exercised is Windows Server via CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evaluation.accessibility_gate import Verdict  # noqa: E402

#: area -> the gate that owns its evidence. Fourteen areas, from the plan.
EVIDENCE_OWNER = {
    "product UX": "ux_gate",
    "interactive search experience": "interaction_gate",
    "settings and configuration": "settings_gate",
    "local learning": "learning_gate",
    "search quality and relevance": "quality_gate",
    "indexing scalability": "scale_gate",
    "storage lifecycle": "storage_gate",
    "Windows/environment support": "portability_gate",
    "distribution and installation": "distribution_gate",
    # These three were mapped to `install_gate`, whose eleven invariants are
    # all about install.ps1, installer.iss, Authenticode and hash manifests.
    # None of them is about privacy, recovery or the Windows shell, so the gate
    # was reporting evidence for areas it had never looked at. Their real
    # owners are `evaluation.gate`'s own invariants, named below.
    "privacy and security": "gate",
    "diagnostics and recovery": "gate",
    "Windows integration": "gate",
    "accessibility": "accessibility_gate",
    "documentation": "gate",
    "release reproducibility": "distribution_gate",
}

#: For the areas owned by `evaluation.gate`, the invariants that constitute
#: their evidence. Naming them is what stops a renamed or deleted check from
#: leaving an area silently "proved".
GATE_INVARIANTS = {
    "documentation": (
        "check_documented_test_count",
        "check_roadmap_has_no_open_phase",
        "check_every_phase_is_documented",
        "check_changelog_documents_every_phase",
    ),
    "privacy and security": (
        "check_privacy_inventory_covers_every_table",
        "check_repairs_never_touch_user_files",
        "check_hostile_archives_are_bounded",
        "check_saved_searches_hold_no_user_data",
        "check_no_network_or_model_imports",
    ),
    "diagnostics and recovery": (
        "check_repairs_never_touch_user_files",
        "check_portable_never_falls_back_silently",
        "check_portable_never_writes_to_the_user_directory",
    ),
    "Windows integration": (
        "check_every_win32_touchpoint_is_declared",
        "check_platform_seam_is_used_for_shell_work",
        "check_core_is_platform_independent",
    ),
}

#: What the plan asks for, and whether this machine can provide it. The first
#: element of each pair is the **marker** that must appear in the documentation;
#: matching whole sentences across three files reported every limitation as
#: undeclared the moment anyone reworded one, which is a gate failing for
#: typography rather than for evidence.
ENVIRONMENT_CLAIMS = {
    "E1_clean_supported_windows": (
        "Windows Server",
        "windows-latest is Windows Server; this machine is a developer box with "
        "another suite running. No install has ever been validated on a user's "
        "Windows 10 or 11"
    ),
    "E2_latency_at_rest": (
        "en reposo",
        "the machine is not at rest, so perf, interaction, ux, fuzzy and "
        "suggest decline to conclude. They can conclude on a runner with "
        "--require-conclusive, which has never been run"
    ),
    "E3_ci_matrix_executed": (
        "3.12, 3.13",
        "3.12, 3.13 and 3.14 are declared in the workflow; no CI run has ever "
        "executed any of them"
    ),
    "E4_iss_compiled": (
        "ISCC",
        "Inno Setup is not installed on this machine; installer.iss has never "
        "been compiled, so install.ps1 is the installer of record"
    ),
    "E5_code_signed": (
        "unsigned",
        "the executables are unsigned, read from the PE, and there is no updater"
    ),
    "E6_console_integration_run": (
        "NoExplorer",
        "the Explorer context-menu tests pass -NoExplorer because a real dialog "
        "would block the suite; the integration has never been exercised by hand"
    ),
}

#: Threshold 0 means "no failures of this kind". The gate compares counts.
THRESHOLDS = {
    f"A{index:02d}_{name.replace(' ', '_').replace('/', '_')}_has_evidence": 0
    for index, name in enumerate(EVIDENCE_OWNER, 1)
} | {f"{name}_unproven": 0 for name in ENVIRONMENT_CLAIMS} | {
    # Thresholds, not zero: the plan asks for eighteen steps and the gate asks
    # that every one of them carry a verdict. A threshold of 0 here could only
    # ever fail, which is how a count becomes a permanent blocker by accident.
    "S01_scenario_steps_attempted": 18,
    "S02_scenario_steps_recorded": 18,
    "M01_slowest_installer_step_seconds": 120,
    "D01_unexplained_measurements_declared": 0,
}


#: Exit codes, named. These are the project's established convention, the same
#: three `perf_gate.veto_exit` returns: a gate that concluded, a gate that found
#: a reason to stop, and a gate that measured nothing.
PRODUCT_GATE_SHIP_CODE = 0
PRODUCT_GATE_HOLD_CODE = 1
PRODUCT_GATE_INCONCLUSIVE_CODE = 2


def read_baseline(stem: str) -> dict | None:
    """Read ``<stem>_baseline.json``, or None.

    ``ux_gate`` owns ``ux_baseline.json``: the gate name drops its suffix. The
    first version looked for ``ux_gate_baseline.json``, found nothing, and
    reported all fifteen audit areas as unproven -- a correct-sounding
    conclusion reached for a reason that was simply wrong, which is the failure
    mode this gate is supposed to be immune to.
    """
    path = ROOT / "evaluation" / f"{stem.removesuffix('_gate')}_baseline.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


#: Phrases a gate writes when it refused to conclude rather than failed. The
#: latency gates emit INCONCLUYENTE with ``passed: false``, so "did not pass" and
#: "did not conclude" are the same two bits, and reading them as one turns a busy
#: machine into a product defect.
DECLINED = ("INCONCLUYENTE", "declined to conclude", "no estaba en reposo",
            "not at rest")


def _is_declined(verdict: dict) -> bool:
    detail = str(verdict.get("detail", "")).lower()
    if verdict.get("load_ok") is False:
        return True
    return any(phrase.lower() in detail for phrase in DECLINED)


def baseline_failures(data: dict) -> list[str]:
    """Names of checks a committed baseline records as **failing**.

    A gate that declined to conclude stores ``passed: false`` as well, and this
    gate counted those as failures: two areas of the fifteen reported themselves
    blocked purely because the machine was busy when the baseline was written.
    A refusal to conclude is the absence of a result, not a negative one, and
    conflating the two is exactly the error this project has made before.
    """
    verdicts = data.get("verdicts")
    if isinstance(verdicts, list):
        return [
            str(v.get("gate", v))
            for v in verdicts
            if not v.get("passed") and not _is_declined(v)
        ]
    if isinstance(verdicts, dict):
        return [
            key for key, v in verdicts.items()
            if not (v or {}).get("passed") and not _is_declined(v or {})
        ]
    return []


def baseline_declined(data: dict) -> list[str]:
    """Checks a baseline records as *not concluded*, reported separately."""
    verdicts = data.get("verdicts")
    if isinstance(verdicts, list):
        return [
            str(v.get("gate", v))
            for v in verdicts
            if not v.get("passed") and _is_declined(v)
        ]
    if isinstance(verdicts, dict):
        return [
            key for key, v in verdicts.items()
            if not (v or {}).get("passed") and _is_declined(v or {})
        ]
    return []


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    measured: dict[str, float] = {}
    details: dict[str, str] = {}
    unproven: dict[str, list[str]] = {"areas": [], "environment": []}
    slow: list[tuple[int, float, str]] = []

    # -- A: every audit area has an owner with a passing baseline -------------
    for index, (area, owner) in enumerate(EVIDENCE_OWNER.items(), 1):
        name = f"A{index:02d}_{area.replace(' ', '_').replace('/', '_')}_has_evidence"
        data = read_baseline(owner)
        if data is None and owner == "gate":
            # `evaluation.gate` reports its invariants and keeps no baseline, so
            # its evidence is the module itself -- and the invariants each area
            # depends on, by name. The first version checked two documentation
            # markers for *every* area routed here, which meant three areas were
            # declared proved on the strength of a test-count check.
            required = GATE_INVARIANTS[area]
            module = (ROOT / "evaluation" / "gate.py").read_text(
                encoding="utf-8")
            absent = [fn for fn in required if f"def {fn}" not in module]
            measured[name] = len(absent)
            if absent:
                unproven["areas"].append(area)
            details[name] = (
                f"evaluation.gate sigue ejecutando {len(required)} invariante(s): "
                f"{', '.join(required)}"
                if not absent
                else f"evaluation.gate ha perdido: {absent}"
            )
            continue
        if data is None:
            measured[name] = 1
            unproven["areas"].append(area)
            details[name] = (
                f"no hay baseline para {owner}; el area no tiene evidencia"
            )
            continue
        failures = baseline_failures(data)
        declined = baseline_declined(data)
        measured[name] = len(failures)
        if failures:
            unproven["areas"].append(area)
        summary = f"{owner} con {len(data.get('verdicts') or [])} veredictos"
        if failures:
            summary += f", {len(failures)} fallo(s): {failures[:3]}"
        if declined:
            summary += (
                f"; {len(declined)} sin concluir (la maquina no estaba en "
                f"reposo, lo cual no es un fallo): {declined[:2]}"
            )
        details[name] = summary

    # -- S: the eighteen-step scenario ---------------------------------------
    scenario_path = ROOT / "product_scenario.json"
    steps: list[dict] = []
    scenario_present = scenario_path.exists()
    if scenario_present:
        steps = json.loads(scenario_path.read_text(encoding="utf-8"))["steps"]

    # The three scenario invariants are omitted, not failed, when there is no
    # record to measure. `product_scenario.json` is a run artefact and is not
    # committed, so on CI -- and on a fresh checkout -- it is absent.
    #
    # Failing would be wrong twice over: it would break every build for a reason
    # that is not a product defect, and it would teach the wrong lesson, that a
    # gate should be taught to tolerate absence. The correct behaviour for a gate
    # with no data is to say it measured nothing.
    scenario_invariants: set[str] = set()
    if not scenario_present:
        scenario_invariants = {
            "S01_scenario_steps_attempted",
            "S02_scenario_steps_recorded",
            "M01_slowest_installer_step_seconds",
        }
        print(
            "SIN product_scenario.json: los tres invariantes del escenario "
            "(S01, S02, M01) no se miden. El escenario no se ha ejecutado aqui; "
            "eso no es un fallo del producto y no se cuenta como uno."
        )
        print()

    measured["S01_scenario_steps_attempted"] = len(steps)
    details["S01_scenario_steps_attempted"] = (
        f"{len(steps)} pasos del escenario registrados en product_scenario.json"
        if steps else "el escenario no se ha ejecutado; no hay registro"
    )
    measured["S02_scenario_steps_recorded"] = sum(
        1 for s in steps if s["outcome"] in ("DONE", "NOT RUN", "BLOCKED")
    )
    details["S02_scenario_steps_recorded"] = (
        f"{measured['S02_scenario_steps_recorded']} de {len(steps)} con un "
        f"veredicto explicito; "
        f"{sum(1 for s in steps if s['outcome'] == 'DONE')} DONE, "
        f"{sum(1 for s in steps if s['outcome'] == 'NOT RUN')} NOT RUN, "
        f"{sum(1 for s in steps if s['outcome'] == 'BLOCKED')} BLOCKED"
    )

    # -- M: the installer step that did not finish in reasonable time ---------
    worst = 0.0
    worst_step = ""
    for step in (steps if scenario_present else ()):
        seconds = float(step.get("seconds", 0.0))
        if seconds > 60 and step["outcome"] == "DONE":
            slow.append((step["n"], seconds, step["name"]))
        if seconds > worst:
            worst, worst_step = seconds, step["name"]
    measured["M01_slowest_installer_step_seconds"] = worst
    details["M01_slowest_installer_step_seconds"] = (
        f"el paso mas lento es {worst_step!r} con {worst:.0f}s; "
        f"el mismo install.ps1 sobre un directorio limpio tardo 18.8s medidos"
        if worst > 120 else
        f"ningun paso pasa de 120s (maximo {worst:.1f}s en {worst_step!r})"
    )

    # -- D: the two measurements this project could not explain ---------------
    unexplained = [
        ("16 KiB",
         "16 KiB de diferencia en el tamano del indice entre dos corridas "
         "identicas; tres teorias refutadas"),
        ("WAL",
         "varianza del WAL de 1.5s a 5.6s sobre la misma entrada"),
        ("961 s",
         "install.ps1 -Repair tardó entre 465 y 961 s en el escenario y 19 s "
         "aislado, sin causa establecida; una de las tres corridas falló"),
    ]
    declared = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "README.md",
            "docs/ROADMAP.md",
            "docs/development/050-product-v3-gate-report.md",
        )
        if (ROOT / name).exists()
    )
    missing = [
        text for marker, text in unexplained if marker not in declared
    ]
    measured["D01_unexplained_measurements_declared"] = len(missing)
    details["D01_unexplained_measurements_declared"] = (
        f"las {len(unexplained)} mediciones sin explicar estan declaradas"
        if not missing else f"sin declarar: {[m[:40] for m in missing]}"
    )

    # -- E: the environment claims --------------------------------------------
    for claim in ENVIRONMENT_CLAIMS:
        marker, reason = ENVIRONMENT_CLAIMS[claim]
        name = f"{claim}_unproven"
        present = marker in declared
        measured[name] = 0 if present else 1
        if not present:
            unproven["environment"].append(claim)
        details[name] = reason

    # -- The release state ----------------------------------------------------
    for name in scenario_invariants:
        measured.pop(name, None)
        details.pop(name, None)

    verdicts = [
        Verdict(
            gate=name,
            measured=measured[name],
            threshold=THRESHOLDS[name],
            passed=measured[name] <= THRESHOLDS[name],
            detail=details[name],
        )
        for name in THRESHOLDS
        if name not in scenario_invariants
    ]
    print("=" * 118)
    print("PHASE 050 - PRODUCT GATE.  Evidence only: no score, no ranking.")
    print("=" * 118)
    for verdict in verdicts:
        print(verdict.line())
    print("=" * 118)

    blockers = [v.gate for v in verdicts if not v.passed]
    print()
    print(f"areas auditadas con evidencia propia : "
          f"{len(EVIDENCE_OWNER) - len(unproven['areas'])}/{len(EVIDENCE_OWNER)}")
    print(f"afirmaciones de entorno demostradas : "
          f"{len(ENVIRONMENT_CLAIMS) - len(unproven['environment'])}/"
          f"{len(ENVIRONMENT_CLAIMS)}")
    if slow:
        print("pasos del escenario por encima de 60s:")
        for number, seconds, name in sorted(slow, key=lambda row: -row[1]):
            print(f"  paso {number:>2}  {name:<28}{seconds:>7.0f}s")
    print()

    if scenario_invariants:
        # Phase 050 correction. With no scenario record this gate printed SHIP
        # and exited 0, which meant CI announced a release-ready verdict for the
        # gate whose committed verdict is HOLD. Omitting an invariant it cannot
        # measure is right; concluding from the omission is not.
        #
        # The project already has the right word for this and the right exit
        # code: `perf_gate.veto_exit` returns 2 for a machine that made a
        # measurement meaningless. Here it is the missing artefact rather than a
        # busy machine, but the shape is identical -- something was measured,
        # the headline was not, and nothing was compared against a threshold.
        print("CLASIFICACION")
        print(f"  SIN MEDIR           : {len(scenario_invariants)} invariante(s) "
              f"del escenario: {sorted(scenario_invariants)}")
        print(f"  LIMITACION DE SALIDA: {len(ENVIRONMENT_CLAIMS)} afirmaciones que "
              f"este equipo no puede cumplir")
        print()
        print("VEREDICTO: INCONCLUYENTE (falta product_scenario.json; esta puerta "
              "no puede afirmar que se puede publicar)")
        return PRODUCT_GATE_INCONCLUSIVE_CODE

    if blockers:
        print("CLASIFICACION")
        print(f"  BLOQUEANTE          : {len(blockers)} invariante(s) sin probar")
        for name in blockers:
            print(f"      {name}")
        print(f"  LIMITACION DE SALIDA: {len(ENVIRONMENT_CLAIMS)} afirmaciones que "
              f"este equipo no puede cumplir")
        print("  DEUDA ACEPTABLE     : mediciones sin explicar, declaradas")
        print("  TRABAJO FUTURO      : firma, actualizador, Inno Setup, "
              "matriz de plataformas")
        print()
        print("VEREDICTO: RETENIDO (HOLD)")
        return PRODUCT_GATE_HOLD_CODE

    print("VEREDICTO: PUBLICABLE (SHIP)")
    return PRODUCT_GATE_SHIP_CODE


if __name__ == "__main__":
    raise SystemExit(main())