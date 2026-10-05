# 050 — Universal Search 3.x Product Gate

## What this is

A gate, not a score. It produces one thing: a release state, and the evidence
behind every claim in it. No rating, no ranking, no "quality is good".

**Verdict: RETENIDO (HOLD).** Not because a product invariant failed — all
fifteen audit areas have their evidence and it passes — but because six claims a
release would want to make cannot be proved from this machine, and one measured
behaviour is unexplained. Section 8 classifies each.

## 1. Environment

| | |
|---|---|
| Product version | 2.0.0 (`src/universal_search/__init__.py`) |
| Commit | `cce2bfb` (cce2bfb26d56b67c8a006a3d462564ef54679bff) |
| Interpreter | CPython 3.14.6, `.venv` |
| OS | Windows 11, developer machine |
| Build | PyInstaller one-dir, `dist/UniversalSearch` |
| Machine state | **not at rest** — another project's test suite and a game were resident |

This is not a clean supported Windows environment and nothing here should be read
as if it were. The only environment this project has ever exercised is Windows
Server, via `windows-latest` in CI, and no installation has ever been validated
on a user's Windows 10 or 11. `docs/SUPPORT.md` declares that; this gate asserts
it.

## 2. Commands

```
python -m evaluation.product_gate      # the gate
python evaluation/product_scenario.py  # the eighteen-step scenario
python -m evaluation.gate              # the 23-invariant quality gate
python -m pytest tests -q             # 1481 tests
```

## 3. The eighteen-step scenario

Executed against the real `install.ps1` and the real built executable.
`UNIVERSAL_SEARCH_HOME` pointed at a temporary directory, so the per-user data
directory, the index, the learned usage and the configuration all landed inside a
folder that was deleted afterwards. Nothing real was read; nothing real was
written.

| # | step | outcome | seconds | evidence |
|---|---|---|---|---|
| 1 | install | DONE | 6.8 s | exit=0, 957 ficheros instalados |
| 2 | primer arranque | DONE | 0.4 s | exit=0, universal-search 2.0.0 |
| 3 | configurar fuentes | DONE | 0.5 s | 2 fuentes creadas; el motor arranca y responde (exit=0) |
| 4 | indexado inicial | DONE | 0.5 s | exit=0, 0.47s, base de datos 192512 bytes |
| 5 | busqueda global | NOT RUN | 0.0 s | registra un atajo global en la sesion de escritorio; esta ejecucion no tiene una. El almacen de  |
| 6 | consulta avanzada | DONE | 0.4 s | exit=0,   el zorro_master cruza el puente quarterly |
| 7 | seleccion de resultado | NOT RUN | 0.0 s | es un gesto de raton en la ventana; lo mide interaction_gate, que construye la ventana real y cr |
| 8 | abrir / revelar | DONE | 0.3 s | exit=0, el fichero existe=True |
| 9 | documentos relacionados | DONE | 0.4 s | exit=0, la busqueda explica por que encaja |
| 10 | ajustes | DONE | 0.5 s | guardar exit=0, usar exit=0 |
| 11 | cambiar la configuracion | DONE | 0.4 s | exit=2; la configuracion se lee de vuelta con lo guardado en el paso 10 |
| 12 | modificar un fichero fuente | DONE | 0.4 s | el contenido cambio en el sitio; el indice se reconstruye en 13 |
| 13 | actualizacion en segundo plano | DONE | 0.4 s | reindex exit=0, el texto nuevo se encuentra=True |
| 14 | reinicio | DONE | 0.4 s | exit=0, el estado persistido se relee (consulta guardada en 10 sigue disponible) |
| 15 | mantenimiento | DONE | 0.3 s | show exit=0, compact exit=0, la busqueda sigue funcionando despues (exit=0, 294 bytes de salida) |
| 16 | actualizar | DONE | 939.6 s | exit=0, el ejecutable sigue arrancando (universal-search 2.0.0) |
| 17 | reparar | DONE | 890.6 s | exit=0, el residuo sobrevive=False, el ejecutable arranca (exit=0) |
| 18 | desinstalar | DONE | 348.2 s | exit=0, el directorio de instalacion existe=False, el atajo existe=False |

**16 of 18 steps executed.** Two are NOT RUN, and
a NOT RUN is not a pass:

* **5, global search** — registering a global hotkey needs a desktop session.
  The hotkey store and its removal are covered by `tests/test_windows_shell.py`;
  the registration itself is not exercised by hand.
* **7, result selection** — a mouse gesture in the window. `interaction_gate`
  builds the real window and times the keystroke; that is its evidence, not this.

## 4. Search quality

Cited, not re-measured — `quality_gate` owns it, on the fixed versioned corpus,
and phase 045 left the ranking deliberately untouched because no reproducible
failure justified a change. Precision@1/5/10, Recall@5/10, MRR, exact-match
accuracy, filter correctness and zero-result behaviour are all in
`evaluation/quality_baseline.json`. Interactive latency is `interaction_gate`'s,
and it declines to conclude on this machine.

## 5. Performance

Cited: `perf_gate` (cold/warm, p50/p95, startup), `scale_gate` (indexing
throughput, rescans, database size), `storage_gate` (derived-data cost).

## 6. Reliability and security/privacy

Cited: `tests/test_reliability.py`, `tests/test_recovery.py`,
`tests/test_privacy.py`, and `install_gate`'s eleven invariants covering the
installer's data handling, signing claim and updater refusal.

## 7. Accessibility

`accessibility_gate`: six verdicts, including WCAG AA contrast on eight colour
pairs across both themes.

## 8. Classification

The plan asks for every failure to be classified. There are no failed product
invariants. What there is, is what could not be proved.

**Release limitations** — the release may proceed without these, provided it does
not claim them:

1. **E1 — no clean supported Windows environment.** `windows-latest` is Windows
   Server; this machine is a developer box. No installation has ever been
   validated on a user's Windows 10 or 11.
2. **E2 — the five latency gates have never concluded.** The machine is not at
   rest. They can conclude on a runner via `--require-conclusive`, which has
   never been run.
3. **E4 — `installer.iss` has never been compiled.** ISCC.exe is not installed
   here and installing it needs network access and consent. `install.ps1` is the
   installer of record for that reason.
4. **E5 — the executables are unsigned** and there is no updater. Both are read
   from the PE rather than asserted.
5. **E6 — the Explorer context-menu integration has never been run by hand.** The
   tests pass `-NoExplorer` because a real dialog would block the suite.

**Declared, and a decision rather than a gap:**

6. **E3 — the CI matrix has never executed.** 3.12, 3.13 and 3.14 are declared
   in the workflow and `requires-python` is bounded to `<3.15`, so the promise
   and the proof are the same set — but the proof has not been produced.

## 9. One measured behaviour with no cause

`install.ps1 -Repair` and `uninstall.ps1`, timed inside the scenario, three
times, against the same script run standalone on an empty directory:

| step | run 1 | run 2 | run 3 | standalone |
|---|---|---|---|---|
| clean install | 6.8 s | 13.8 s | 4.6 s | 18.8 s |
| upgrade (`-Repair`) | **940 s** | **961 s, exit 4294967295** | **465 s** | 19.1 s |
| repair after a stale file | **891 s** | 723 s | **436 s** | 11.1 s |
| uninstall | **348 s** | 147 s | **145 s** | not separately measured |

The spread is the finding as much as the size. The same command took 465 s and
961 s on consecutive runs, and **one of the three returned `4294967295` — it
failed**. Not every run is slow. Not every run succeeds.

What was measured about the slow ones:

* the process is not blocked — it accumulates CPU continuously (512 s over
  roughly 18 minutes of wall clock), so it is computing, not waiting;
* it is past the file copy and past the Start Menu shortcut, which print in
  order;
* the manifest's recursive enumeration is **0.26 s** for the same 957 files;
* `ConvertTo-Json -Depth 5` over 1908 strings is **0.14 s**, which rules out
  manifest serialisation — the hypothesis that fit best and was wrong.

So the cost sits after the shortcut and before the manifest write, in work that
has not been identified.

**What has not been established is whether this is a product defect.** The
machine was not at rest during any of the three runs — another project's test
suite was resident throughout — and the standalone figure was measured later
under different load. A twentyfold gap between "slow" and "not slow" is
consistent with an installer problem, with machine load, or with both. The gate
does not pick the flattering answer: **M01 fails and the release is held.**

**BLOCKER.** An upgrade that takes eight to sixteen minutes, varies twofold run
to run, and sometimes returns a failure code, is not shippable — and an
unexplained behaviour in the installer of record is precisely what a product
gate exists to find.

## 10. Inherited versus new, 041–049

| area | inherited | new in 041–049 |
|---|---|---|
| UX | window, palette, keyboard | phase 041 experience report; 046/047 scaling |
| search quality | ranking engine, corpus | 045 failure taxonomy, corpus 39/30 |
| learning | — | 044 local learning with scope and decay |
| scalability | streaming providers | 046 scaling curve, four cancellation defects |
| storage | FTS5 index | 047 `compact()`, lifecycle contract for 15 datasets |
| support | Windows-first | 048 bounded Python range, classifier matrix |
| distribution | portable mode | 049 real install/repair/uninstall cycle |
| CI | 4 gates | 050 all 19 wired, with a decisive veto |

## 11. Architectural debt

Carried forward, unchanged and not re-litigated here: WAL variance; the 16 KiB
index-size spread; the superlinear semantic layer (`MAX_NGRAMS_PER_DOC`);
`make-start-menu.ps1`, which nothing calls while `platforms/windows.py` names it
as the script that installs the Start Menu entry; no graphical uninstaller; no
updater.

## 12. Deferred work

Not attempted, because it needs something this machine does not have: compiling
Inno Setup, a Windows 10 or 11 machine or runner, a code-signing certificate, CI
access, and a machine at rest.

## 13. What the repository looks like afterwards

`evaluation/product_scenario.py` and `evaluation/product_gate.py` are committed.
`product_scenario.json` is **not** — it is a run artefact with timings and
temporary paths in it, exactly like the other baselines that were reverted for
carrying a clock. The gate reads it when present and reports the scenario as
unrun when it is not, which is the honest state for a fresh checkout.

No push.
