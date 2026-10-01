"""Phase 039 evidence: the interface, measured rather than asserted.

Run from the repository root::

    python -m evaluation.accessibility_gate

Accessibility claims are the easiest kind to make and the hardest to check.
"This window is keyboard-operable" survives review for years without anyone
pressing a key, because nobody can see the difference in a screenshot. So each
gate below is an instrument:

* **T1** the window can be used with no mouse — every interactive control is in
  the Tab ring and declares ``takefocus`` instead of inheriting a platform
  default;
* **T2** every control has a name that comes from the catalogue;
* **T3** every visible string comes from the catalogue, so the interface is
  translatable as one list;
* **T4** every palette colour passes WCAG AA in both themes;
* **T5** every palette colour is drawn somewhere;
* **T6** a failure is drawn as a failure.

T1 and T2 need a real Tk runtime, so when one cannot be created they are
reported as NOT RUN rather than quietly assumed: an accessibility gate that
cannot open a window on this machine has not verified anything about this
machine.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from universal_search.gui import strings  # noqa: E402
from universal_search.gui.accessibility import (  # noqa: E402
    AA_BODY,
    CONTRAST_PAIRS,
    contrast_report,
    focus_order,
    focus_report,
    name_report_for,
)
from universal_search.gui.theme import DARK, LIGHT  # noqa: E402

THRESHOLDS = {
    "T1_unreachable_controls": 0,
    "T2_unnamed_controls": 0,
    "T3_untranslated_literals": 0,
    "T4_contrast_failures": 0,
    "T5_undrawn_colours": 0,
    "T6_error_looks_like_status": 0,
}


@dataclass
class Verdict:
    gate: str
    measured: float
    threshold: float
    passed: bool
    detail: str

    def line(self) -> str:
        return (
            f"{'PASS' if self.passed else 'FAIL'}  {self.gate:<40}"
            f"{self.measured:>9.3f}  (umbral {self.threshold})  {self.detail}"
        )


def _build_window(tmp: Path):
    """A real search window over a small index, or ``None`` with the reason."""
    try:
        import tkinter as tk
    except ImportError as exc:  # pragma: no cover - Tk ships with CPython
        return None, f"sin tkinter: {exc}"

    from universal_search.appconfig import AppPaths
    from universal_search.gui.app import SearchWindow
    from universal_search.gui.services import SearchService
    from universal_search.index.database import SearchDatabase
    from universal_search.index.indexer import Indexer

    files = tmp / "files" / "electronica"
    files.mkdir(parents=True, exist_ok=True)
    (files / "capacitor.md").write_text(
        "notas sobre el capacitor de 100 uF", encoding="utf-8"
    )
    (files / "notas.md").write_text("apuntes de clase", encoding="utf-8")
    database = SearchDatabase(tmp / "index.db")
    Indexer(database).index_root(tmp / "files")
    service = SearchService(
        paths=AppPaths.discover(home=tmp / "home"), database_path=database.path
    )
    try:
        return SearchWindow(service=service), None
    except tk.TclError as exc:
        return None, f"Tk no disponible en esta máquina: {exc}"


def main() -> int:
    # The gate prints the interface's own strings, and one of them contains
    # U+25BE ("Recientes ▾"), which the cp1252 console cannot encode. Piping the
    # output -- which is how CI and the test suite run it -- turns that into a
    # UnicodeEncodeError halfway through the report, and the gate reports
    # nothing at all. Found by the test that runs it in a subprocess.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    # -- T3: every visible string is catalogued (no display needed) -----------
    missing = strings.untranslated_literals(ROOT)

    # -- T4 and T5: contrast and coverage (pure data) ------------------------
    contrast_failures = [
        f"{theme.name}/{check.name} {check.ratio:.2f}:1 < {check.required}"
        for theme in (LIGHT, DARK)
        for check in contrast_report(theme)
        if not check.ok
    ]
    drawn = {
        field for field in LIGHT.__dataclass_fields__ if field not in {"name", "dark"}
    }
    used: set[str] = set()
    for path in strings.gui_modules(ROOT):
        if path.name in {"theme.py", "accessibility.py", "strings.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        for field in drawn:
            if f"theme.{field}" in source:
                used.add(field)
    undrawn = drawn - used

    # -- T1, T2, T6: need a real window ---------------------------------------
    workspace = Path(tempfile.mkdtemp(prefix="universal-search-039-"))
    window, reason = _build_window(workspace)

    if window is None:
        t1_detail = t2_detail = t6_detail = reason
        unreachable = unnamed = 1
        error_like_status = 1
        control_names: list[str] = []
    else:
        try:
            focus = focus_report(window)
            names = name_report_for(window)
            control_names = [
                f"{control.role}={control.name}" for control in names.controls
            ]
            unreachable = len(focus.unreachable)
            unnamed = len(names.unnamed)
            t1_detail = (
                f"{focus.reachable} controles en el anillo: "
                f"{[type(w).__name__ for w in focus_order(window)]}"
            )
            t2_detail = ", ".join(control_names) or "(sin controles)"

            window._set_status(strings.get("SEARCH.READY"))
            ordinary = str(window.status_label.cget("foreground"))
            window._set_status(strings.get("ERROR.SEARCH"), "error")
            failing = str(window.status_label.cget("foreground"))
            error_like_status = int(failing == ordinary)
            t6_detail = (
                f"estado {ordinary} vs error {failing}; "
                f"danger declarado {LIGHT.danger}"
            )
        finally:
            try:
                window.destroy()
            except Exception:  # pragma: no cover - teardown best effort
                pass

    verdicts = [
        Verdict("T1 controls unreachable by keyboard", unreachable,
                THRESHOLDS["T1_unreachable_controls"],
                unreachable == 0 and window is not None, t1_detail),
        Verdict("T2 controls with no accessible name", unnamed,
                THRESHOLDS["T2_unnamed_controls"],
                unnamed == 0 and window is not None, t2_detail),
        Verdict("T3 visible strings outside the catalogue", len(missing),
                THRESHOLDS["T3_untranslated_literals"], not missing,
                f"{len(strings.ENTRIES)} entradas catalogadas"
                + ("" if not missing else f"; faltan {sorted(missing)}")),
        Verdict("T4 WCAG AA contrast failures", len(contrast_failures),
                THRESHOLDS["T4_contrast_failures"], not contrast_failures,
                f"{len(CONTRAST_PAIRS)} pares x 2 temas, cuerpo {AA_BODY}:1"
                + ("" if not contrast_failures else f"; fallan {contrast_failures}")),
        Verdict("T5 palette colours never drawn", len(undrawn),
                THRESHOLDS["T5_undrawn_colours"], not undrawn,
                f"{len(drawn)} colores, todos usados"
                if not undrawn else f"sin dibujar: {sorted(undrawn)}"),
        Verdict("T6 errors that look like ordinary status", error_like_status,
                THRESHOLDS["T6_error_looks_like_status"],
                error_like_status == 0 and window is not None, t6_detail),
    ]

    print("=" * 100)
    print("PUERTA DE EVIDENCIA - FASE 039 (accesibilidad e interfaz)")
    print("=" * 100)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 100)
    print("contraste medido (WCAG 2.1):")
    for theme in (LIGHT, DARK):
        for check in contrast_report(theme):
            status = "ok " if check.ok else "FALLO"
            print(f"  {status} {theme.name:5} {check.name:<24} "
                  f"{check.ratio:6.2f}:1  (necesita {check.required})")
    if control_names:
        print("nombres accesibles declarados:")
        for line in control_names:
            print(f"  {line}")

    payload = {
        "phase": "039",
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "window_checked": window is not None,
        "window_reason": reason,
        "contrast": {
            theme.name: [
                {"pair": check.name, "ratio": round(check.ratio, 2),
                 "required": check.required, "ok": check.ok}
                for check in contrast_report(theme)
            ]
            for theme in (LIGHT, DARK)
        },
    }
    out = ROOT / "evaluation" / "accessibility_baseline.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")
    failed = [v for v in verdicts if not v.passed]
    print("VEREDICTO:", "SHIP" if not failed else f"NO SHIP ({len(failed)} puertas)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())