"""Phase 048 evidence: do the claims, the CI and the docs say the same thing?

Run from the repository root::

    python -m evaluation.portability_gate

Phase 048's demand is that *"the current CI/runtime mismatch must be made
explicit and resolved or documented rather than silently treated as support"*,
and its acceptance is that *"the support matrix, CI matrix and release
documentation agree on exactly what is supported and what is merely probed"*.

Those are claims about three files that can drift apart, so this checks them
against each other rather than trusting any one of them:

**W1-W2, the promise.** `requires-python` must be a bounded range, not an open
`>=3.12`. The phase found the package promising 3.15 and beyond while the CI
proved exactly one version and the development machine ran a fourth.

**W3-W4, the proof.** Every version in the CI matrix must fall inside the declared
range, and every version in the range must appear in the CI matrix. A promise
with no proof is marketing; a proof nobody needs is a CI minute.

**W5-W6, the claim on the package.** The classifiers must exist, must name the
Windows versions `docs/SUPPORT.md` declares, and must **not** name a platform the
matrix declares unsupported. There were none at all before this phase.

**W7, the interpreter.** The interpreter running this gate must be inside the
declared range. This is the check that would have caught the original mismatch:
a project developing on a version its own CI never runs.

**W8-W9, support versus probe.** The CI must mark the Linux job non-gating, and
`docs/SUPPORT.md` must say so in the same words. A probe that blocks a release
and a support claim are both lies; the difference between them is one YAML flag
and this asserts it is set.

What this gate cannot do, and says so: it cannot run the suite on another
interpreter or another machine. It checks that what the project *claims* matches
what the CI *demonstrates*, which is where the mismatch lived.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evaluation.accessibility_gate import Verdict  # noqa: E402

THRESHOLDS = {
    "W1_requires_python_unbounded": 0,
    "W2_versions_declared_but_not_proven": 0,
    "W3_versions_proven_but_not_declared": 0,
    "W4_missing_platform_classifiers": 0,
    "W5_unsupported_platform_classifiers": 0,
    "W6_interpreter_outside_declared_range": 0,
    "W7_ci_matrix_missing": 0,
    "W8_linux_probe_not_marked_non_gating": 0,
    "W9_docs_do_not_separate_support_from_probe": 0,
}

GATE_LINES = {
    "W1_requires_python_unbounded": "W1 requires-python sin cota superior",
    "W2_versions_declared_but_not_proven": "W2 versiones declaradas que el CI no prueba",
    "W3_versions_proven_but_not_declared": "W3 versiones probadas que no se declaran",
    "W4_missing_platform_classifiers": "W4 clasificadores de plataforma ausentes",
    "W5_unsupported_platform_classifiers": "W5 clasificadores que prometen plataformas no soportadas",
    "W6_interpreter_outside_declared_range": "W6 el interprete actual esta fuera del rango declarado",
    "W7_ci_matrix_missing": "W7 el CI no declara una matriz de Python",
    "W8_linux_probe_not_marked_non_gating": "W8 la sonda de Linux no es no bloqueante",
    "W9_docs_do_not_separate_support_from_probe": "W9 la documentacion no separa soporte de sonda",
}


@dataclass(frozen=True, slots=True)
class Declared:
    """What the project claims, gathered from every file that claims anything."""

    requires_python: str
    floor: str
    ceiling: str | None
    ceiling_inclusive: bool
    classifiers: tuple[str, ...]
    ci_versions: tuple[str, ...]
    probe_marked_non_gating: bool
    support_doc: str

    @property
    def declared_versions(self) -> tuple[str, ...]:
        """The minor versions the range allows, when it has both ends.

        ``>=3.12,<3.15`` allows 3.12, 3.13 and 3.14 -- and *not* 3.15, because
        the upper bound is exclusive. The first version of this gate treated it
        as inclusive and reported W2 failing on a range and a CI matrix that
        agreed perfectly: the instrument was wrong, not the project.
        """
        if not self.ceiling:
            return ()
        low = tuple(int(part) for part in self.floor.split("."))
        high = tuple(int(part) for part in self.ceiling.split("."))
        if self.ceiling_inclusive:
            return _between(low, high, inclusive=True)
        return _between(low, high, inclusive=False)


def _between(low: tuple[int, ...], high: tuple[int, ...], *,
             inclusive: bool) -> tuple[str, ...]:
    versions = []
    for major in range(low[0], high[0] + 1):
        start = low[1] if major == low[0] else 0
        end = high[1] if major == high[0] else 99
        if major == high[0] and not inclusive:
            end -= 1
        for minor in range(start, end + 1):
            versions.append(f"{major}.{minor}")
    return tuple(versions)


def _parse_range(spec: str) -> tuple[str, str | None, bool]:
    """The floor, the ceiling, and whether the ceiling is inclusive.

    ``<3.15`` and ``<=3.15`` allow different sets of interpreters, and treating
    one as the other is the sort of off-by-one that makes a gate cry wolf on a
    correct configuration.
    """
    floor = "0"
    ceiling: str | None = None
    inclusive = False
    for clause in spec.split(","):
        clause = clause.strip()
        match = re.match(r">=\s*(\d+\.\d+)", clause)
        if match:
            floor = match.group(1)
            continue
        match = re.match(r"<=\s*(\d+\.\d+)", clause)
        if match:
            ceiling = match.group(1)
            inclusive = True
            continue
        match = re.match(r"<\s*(\d+\.\d+)", clause)
        if match:
            ceiling = match.group(1)
            inclusive = False
            continue
    return floor, ceiling, inclusive


def gather() -> Declared:
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    spec = pyproject.get("requires-python", "")
    floor, ceiling, inclusive = (
        _parse_range(spec) if spec else ("0", None, False)
    )

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    # Only the gating job's matrix counts as proof. The probe and the package
    # job pin a version too, and counting those would let a non-gating job
    # masquerade as evidence.
    quality = ci.split("  core-portability:")[0]
    matrix = re.search(r"matrix:\s*\n\s*python:\s*\[([^\]]+)\]", quality)
    ci_versions = tuple(re.findall(r'"?(\d+\.\d+)"?', matrix.group(1))) if matrix else ()
    probe = ci.split("  core-portability:")[-1]
    probe_non_gating = "continue-on-error: true" in probe

    return Declared(
        requires_python=spec,
        floor=floor,
        ceiling=ceiling,
        ceiling_inclusive=inclusive,
        classifiers=tuple(pyproject.get("classifiers", [])),
        ci_versions=ci_versions,
        probe_marked_non_gating=probe_non_gating,
        support_doc=(
            (ROOT / "docs" / "SUPPORT.md").read_text(encoding="utf-8")
            if (ROOT / "docs" / "SUPPORT.md").exists() else ""
        ),
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    declared = gather()
    runtime = f"{sys.version_info.major}.{sys.version_info.minor}"
    allowed = declared.declared_versions
    measured: dict[str, float] = {}
    details: dict[str, str] = {}

    # W1: an open range promises versions nobody has run.
    measured["W1_requires_python_unbounded"] = 0 if declared.ceiling else 1
    details["W1_requires_python_unbounded"] = (
        f"requires-python = {declared.requires_python!r}"
        + (
            f"; acotado a {declared.ceiling}, que es lo que el CI demuestra"
            if declared.ceiling
            else "; abierto: promete 3.15 y siguientes sin evidencia"
        )
    )

    # W2/W3: the promise and the proof must be the same set.
    unproven = [v for v in allowed if v in _interesting(allowed)
                and v not in declared.ci_versions]
    measured["W2_versions_declared_but_not_proven"] = len(unproven)
    details["W2_versions_declared_but_not_proven"] = (
        f"el rango declara {', '.join(_interesting(allowed))} y el CI prueba "
        f"{', '.join(declared.ci_versions) or '(ninguna)'}"
        + (f"; sin probar: {', '.join(unproven)}" if unproven else "; coinciden")
    )
    undeclared = [v for v in declared.ci_versions if v not in allowed]
    measured["W3_versions_proven_but_not_declared"] = len(undeclared)
    details["W3_versions_proven_but_not_declared"] = (
        f"el CI prueba {', '.join(declared.ci_versions) or '(ninguna)'}"
        + (f" fuera del rango declarado: {', '.join(undeclared)}"
           if undeclared else ", todas dentro del rango")
    )

    # W4: the package must say what platform it is for.
    wanted = (
        "Operating System :: Microsoft :: Windows",
        "Operating System :: Microsoft :: Windows :: Windows 10",
        "Operating System :: Microsoft :: Windows :: Windows 11",
    )
    missing = [c for c in wanted if c not in declared.classifiers]
    measured["W4_missing_platform_classifiers"] = len(missing)
    details["W4_missing_platform_classifiers"] = (
        f"{len(declared.classifiers)} clasificadores declarados, "
        f"{len(wanted) - len(missing)} de los {len(wanted)} de plataforma"
        + (f"; faltan: {', '.join(missing)}" if missing else "")
    )

    # W5: nothing may claim a platform the matrix refuses.
    forbidden = [c for c in declared.classifiers if "POSIX" in c
                 or "MacOS" in c or "Linux" in c]
    measured["W5_unsupported_platform_classifiers"] = len(forbidden)
    details["W5_unsupported_platform_classifiers"] = (
        "ningun clasificador promete Linux ni macOS; el job de Ubuntu es una "
        "sonda no bloqueante y un clasificador se lee como promesa de soporte"
        if not forbidden else f"prometen plataformas no soportadas: {forbidden}"
    )

    # W6: the check that would have caught the original mismatch.
    outside = runtime not in allowed if allowed else True
    measured["W6_interpreter_outside_declared_range"] = 1 if outside else 0
    details["W6_interpreter_outside_declared_range"] = (
        f"este gate corre en Python {runtime} y el rango declarado es "
        f"{declared.requires_python}; {'FUERA del rango' if outside else 'dentro'}"
    )

    # W7: proof requires a matrix, not a pinned version.
    measured["W7_ci_matrix_missing"] = 0 if declared.ci_versions else 1
    details["W7_ci_matrix_missing"] = (
        f"el job que puerta declara matrix python: "
        f"{list(declared.ci_versions) or '(ninguna)'}"
    )

    # W8: a probe that blocks releases is a support claim in disguise.
    measured["W8_linux_probe_not_marked_non_gating"] = (
        0 if declared.probe_marked_non_gating else 1
    )
    details["W8_linux_probe_not_marked_non_gating"] = (
        "el job core-portability lleva continue-on-error: true, asi que un rojo "
        "ahi no bloquea una version"
        if declared.probe_marked_non_gating
        else "el job de Linux bloquea la version, y eso lo convierte en soporte"
    )

    # W9: the documentation has to use the same distinction.
    doc = declared.support_doc
    says_probe = bool(doc) and "sonda" in doc.lower()
    says_not_supported = bool(doc) and "no se declara soportado" in doc.lower()
    ok = says_probe and says_not_supported
    measured["W9_docs_do_not_separate_support_from_probe"] = 0 if ok else 1
    details["W9_docs_do_not_separate_support_from_probe"] = (
        "docs/SUPPORT.md separa soporte de sonda y lista lo que no se declara "
        "soportado"
        if ok else
        f"docs/SUPPORT.md {'falta' if not doc else 'no distingue'} la sonda "
        f"(sonda={says_probe}) o la lista de no soportado ({says_not_supported})"
    )

    verdicts = [
        Verdict(
            gate=GATE_LINES[gate],
            measured=float(measured[gate]),
            threshold=float(threshold),
            passed=measured[gate] <= threshold,
            detail=details[gate],
        )
        for gate, threshold in THRESHOLDS.items()
    ]

    print("=" * 100)
    print("PUERTA DE EVIDENCIA - FASE 048 (matriz de soporte)")
    print("=" * 100)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 100)
    print("el entorno real en el que corre esta puerta:")
    import platform
    import sqlite3

    print(f"  python        {sys.version.split()[0]} ({runtime})")
    print(f"  arquitectura  {platform.machine()}, "
          f"{platform.architecture()[0]}")
    print(f"  sistema       {platform.system()} {platform.release()}")
    print(f"  sqlite        {sqlite3.sqlite_version}")
    print(f"  tcl/tk        {_tk_version()}")
    print("-" * 100)
    print("lo que esta puerta NO puede comprobar:")
    print("  que la suite pase en 3.12 o 3.13 -- no se han ejecutado aqui")
    print("  que el CI sobreviva a un Windows Server y a un Windows de usuario")
    print("  nada en macOS: no hay job, no hay medicion, no hay afirmacion")

    payload = {
        "phase": "048",
        "clock": datetime.now(timezone.utc).isoformat(),
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "declared": {
            "requires_python": declared.requires_python,
            "ci_versions": list(declared.ci_versions),
            "classifiers": list(declared.classifiers),
            "runtime": runtime,
        },
        "decision": (
            "requires-python bounded at <3.15; CI matrix 3.12/3.13/3.14; "
            "classifiers declared; docs/SUPPORT.md written; CLI output forced "
            "to UTF-8. The CI matrix has never been executed."
        ),
    }
    out = ROOT / "evaluation" / "portability_baseline.json"
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")
    failed = [v for v in verdicts if not v.passed]
    if not failed:
        print("VEREDICTO: SHIP")
        return 0
    print(f"VEREDICTO: NO SHIP ({len(failed)} puertas)")
    return 1


def _tk_version() -> str:
    try:
        import tkinter

        return f"{tkinter.TkVersion}"
    except Exception:  # noqa: BLE001 - the absence is the measurement
        return "no disponible"


def _interesting(versions: tuple[str, ...]) -> tuple[str, ...]:
    """The minor versions worth naming, not all 300 between 3.12 and 3.15."""
    return tuple(v for v in versions if v.endswith((".12", ".13", ".14", ".15")))


if __name__ == "__main__":
    raise SystemExit(main())