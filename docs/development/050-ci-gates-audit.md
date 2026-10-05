# Phase 050, part 2 - the build runs every gate, and a gate that cannot fail is not a gate

> **This file is not phase 050's report, and it is named accordingly.** Phase 050
> is the 3.x product gate and it is still open. `evaluation/gate.py` derives the
> range of phases it checks from `docs/development/*-report.md`, so naming this
> `-report.md` made it claim that phase 050 is finished -- and
> `test_every_phase_of_the_programme_is_in_range` failed with "a report exists for
> phase 50". The gate caught me doing the thing it exists to catch, one phase
> earlier than anyone expected. The honest name is `audit`, which is what this is:
> one piece of work inside a phase that has not concluded.

## What the audit found

An audit of everything still declared open across phases 041-049 asked a question
nobody had asked: **how many of the gates does the build actually run?**

Fifteen. That is, of the nineteen `evaluation/*_gate.py` modules, the workflow
invoked four.

| gate | was it run by CI | seconds | verdict on a busy machine |
|---|---|---|---|
| `scale_gate` | **no** | 333.3 | SHIP |
| `storage_gate` | **no** | 119.9 | SHIP |
| `quality_gate` | **no** | 49.7 | SHIP |
| `gate` | yes | 26.6 | SHIP |
| `interaction_gate` | **no** | 20.2 | INCONCLUYENTE (the machine was not at rest) |
| `learning_gate` | **no** | 19.2 | SHIP |
| `fuzzy_gate` | **no** | 18.0 | INCONCLUYENTE (the machine was not at rest) |
| `suggest_gate` | **no** | 14.1 | INCONCLUYENTE (the machine was not at rest) |
| `ux_gate` | **no** | 14.0 | INCONCLUYENTE (the machine was not at rest) |
| `perf_gate` | **no** | 12.8 | INCONCLUYENTE |
| `distribution_gate` | **no** | 12.6 | SHIP |
| `accessibility_gate` | **no** | 6.5 | SHIP |
| `archive_gate` | **no** | 6.2 | SHIP |
| `organize_gate` | **no** | 5.3 | SHIP |
| `mail_gate` | **no** | 5.0 | SHIP |
| `install_gate` | **no** | 2.0 | SHIP |
| `batch_gate` | **no** | 2.0 | SHIP |
| `settings_gate` | **no** | 1.6 | SHIP |
| `portability_gate` | **no** | 1.1 | SHIP |

`distribution_gate` is the one that should sting: eleven invariants, present since
phase 037, and the only gate that executes a real artefact. Phase 049 connected it
and said so in its report. Everything before it, nobody had.

The reason this survived nine phases is not carelessness. A gate that nothing runs
cannot fail, and there was **no test asserting that it ran**. The evidence for this
is in the same audit: `tests/test_ci_gates.py` contained `assert "artifacts.sha256"
in body`, which had been passing since phase 049 replaced that filename - against
the comment in `ci.yml` explaining why the assertion had been meaningless.

## Why wiring them in was not a copy-paste job

Five of the nineteen measure latency, and five of the nineteen **decline to
conclude by exiting 2** when the machine is busy. That behaviour was correct and
deliberate: phases 045b and 047b built it because a latency number taken on
somebody else's busy machine describes their program, and INCONCLUYENTE is the
honest verdict rather than a number nobody can use.

It stops being correct in CI. A GitHub runner is a machine nobody else is using.
If the veto fires there, the explanations are that the runner was oversubscribed
or that the gate itself started costing more, and both are things a build should
fail on. An INCONCLUYENTE on a runner is not caution; it is an unmeasured gate
reported as if it had passed.

So the gates gained `--require-conclusive`:

```
$ python -m evaluation.suggest_gate
VEREDICTO: INCONCLUYENTE (the machine was not at rest)
exit=2

$ python -m evaluation.suggest_gate --require-conclusive
VEREDICTO: NO SHIP (--require-conclusive: the machine was not at rest, and here
          there is no neighbour to explain it)
exit=1
```

Measured on this machine, with a game running, on all five: 2 without the flag, 1
with it. The load is still measured before anything is timed and the reason is
still printed. **Only the verdict changes.** `perf_gate.require_conclusive()` is
the single implementation and `perf_gate.veto_exit()` the single place the exit
code is decided, so the four gates that delegate cannot drift from it.

The alternative - connecting the gates and letting exit 2 count as a pass - was
rejected. It would have produced fifteen gates that cannot fail, which is the
finding this phase exists to correct.

## The job split, by measured cost

best-of-one on a machine at ~90% CPU with a game running; ordering only, not a budget.

* `scale_gate` - 333.3 s
* `storage_gate` - 119.9 s

All nineteen took **11.2 minutes** in total on a
machine at ~90% CPU. A runner is several times quicker. Three jobs, split so that
adding a slow gate does not push the suite out of its timeout:

| job | gates | why |
|---|---|---|
| `quality` | `gate`, `quality`, `learning`, `settings`, `accessibility`, `organize`, `batch`, `archive`, `mail` | all under 50 s; they belong with the 1481 tests |
| `gates` | `perf`, `interaction`, `ux`, `fuzzy`, `suggest` (with `--require-conclusive`), `distribution`, `portability`, `install` | the latency gates, which must be able to fail |
| `heavy-gates` | `scale`, `storage` | 333 s and 120 s; minutes of work that should not hold the rest hostage |

`package` waits for all three. An artefact should not be built while the evidence
for it is still running.

## Five invariants, and two of them could not fail

The invariant whose absence caused all of this:

```python
def test_every_gate_in_the_tree_is_run_by_the_build(workflow: str) -> None:
    run = set(re.findall(r"python -m (evaluation\.[a-z_]+)", workflow))
    missing = sorted(gate_modules() - run)
    assert not missing, ...
```

`gate_modules()` globs `evaluation/*_gate.py`. The list is **derived from the
tree**, not written out, because a hand-maintained list is a list that goes stale
- which is exactly how fifteen gates ended up in no list at all.

Four more check that the latency gates carry the switch, that no gate is run with a
flag it does not declare, that the expensive gates stay out of the suite job, and
that `package` still waits.

**Verifying an invariant by asserting it is not verifying it.** The six of them
were checked by breaking the workflow six ways on purpose and confirming each
break was caught. Two were not:

1. `test_no_gate_is_run_with_a_flag_that_does_not_exist` captured the flag with
   `[a-z0-9-]*` placed directly after the module name. The flag is behind a space,
   so the capture was the empty string on every line, the loop skipped each one,
   and the assertion was **never evaluated**. It passed because it did nothing.
2. The second version tested the flag with `in`. A misspelled flag here is a
   *truncation*, and `--require-conclusiv` **is a substring of
   `--require-conclusive`**. So the typo passed. The check now compares whole
   tokens extracted by regex.

Both defects are in this report rather than quietly repaired. The pattern that
produced the original finding - a test passing against its own explanation - is
the same pattern, one level down, and the fix for it is the same: break it on
purpose and watch.

```
que se rompio                              detectado   test
una puerta desaparece del CI               SI          ...every_gate_in_the_tree_is_run_by_the_build
una puerta de latencia pierde el flag      SI          ...latency_gates_are_wired_to_demand_a_number
un flag mal escrito (typo de una letra)    SI          ...no_gate_is_run_with_a_flag_that_does_not_exist
una puerta cara vuelve al job de la suite  SI          ...expensive_gates_are_not_in_the_suite_job
package deja de esperar a la evidencia      SI          ...release_waits_for_the_gates_it_claims_to_honour
un job de evidencia se vuelve permisivo    SI          ...no_gating_job_may_continue_on_error
```

## One existing assertion changed meaning

`test_package_job_builds_and_smokes_the_real_executables` asserted the literal
string `needs: quality`. That is a format, not a contract: it broke the moment
`package` began also waiting for the two new jobs, and the failure message said
nothing about whether the release still waits for anything. It now reads the
dependency as a set.

## What this does not close

- **The matrix has still never run.** This change is a workflow edit; no CI was
  executed to verify it. Every statement above about the workflow's behaviour is a
  statement about the file, and the six deliberate breaks are the only execution
  evidence there is. The first real run may find a problem the text-level checks
  cannot see.
- **`windows-latest` is Windows Server**, not Windows 10 or 11. Running nineteen
  gates on Server is not the same as on a user's machine, and that gap is
  unchanged.
- **The five latency gates have never concluded.** They exit 2 here, and on a
  runner they will either conclude for the first time or fail the build. Both
  outcomes are new information; neither has been observed.
- **`scale_gate` at 333 s is a best-of-one** on a loaded machine. The split is
  sound either way - 30 minutes of headroom - but the number is not a budget.

## Cost

`evaluation/ci_gate_costs.json` is committed. Without it the split is a claim.
