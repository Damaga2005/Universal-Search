# Phase 050, part 3 - what the first CI runs proved, and one failure that would not stay still

> **Not phase 050's report.** Phase 050 is the 3.x product gate and is still
> open. This is the second half of what the CI work found, written after the
> workflow had actually been executed — the part `050-ci-gates-audit.md` could
> only predict.

## The matrix has run. That section is now obsolete.

`050-ci-gates-audit.md` says "the matrix has still never run" and "the five
latency gates have never concluded". Both were true when written and are now
false. Run 37333334969, commit `470c81d`:

| job | result |
|---|---|
| `evidence gates (scale and storage)` | **pass** |
| `evidence gates (latency and distribution)` | **pass** |
| `tests, lint and migrations (Windows)` 3.13 | **pass** |
| `tests, lint and migrations (Windows)` 3.14 | **pass** |
| `tests, lint and migrations (Windows)` 3.12 | fail — one test |
| `platform-independent core (probe, non-gating)` | fail — by design, `continue-on-error` |

**All nineteen gates have now executed on a runner, and all five latency gates
concluded for the first time** under `--require-conclusive`. The wiring that
`cce2bfb` added is no longer a claim about a file.

The `KeyboardInterrupt` at `tests/test_reliability.py:378` that had been failing
since before the matrix ran is gone on all three versions.

## Three bugs, all the same species

Each was a test asserting something about the machine rather than about the
code. None was a product defect.

1. **`Path.cwd() == folder.resolve()`** (`5066a7c`) — cannot hold when `TEMP`
   is an 8.3 short name. `os.chdir` does not expand `RUNNER~1`.
2. **A hardcoded `.venv\Scripts\python.exe`** (`0290359`) — three encoding tests
   had never run anywhere except the machine that wrote them.
3. **The icon test asserted deflate output, not the drawing** (`0290359`) —
   passed on 3.14 (zlib-ng), failed on 3.12/3.13/Linux (classic zlib). The
   pixels were identical; re-encoding under eight compressor settings showed
   pixels match in all eight and bytes in one.

Each was verified by breaking it on purpose and watching it fail, because a
test that has only ever passed proves nothing.

## The fourth failure: a red herring I nearly committed

`tests/test_gui.py::test_superseded_control_action_refreshes_snapshot` failed on
the runner at Python 3.12.10 and passed locally. Chasing it produced the most
useful negative result in this phase.

**What is real.** The assertion is `assert control_center.pump(2.0)`. `pump`
returns `False` only when `_inflight != 0` for the full two seconds. The window
is a `tk.Toplevel` subclass whose 50 ms poll re-arm is a Tk `after` callback,
and the work it waits on is a real snapshot — measured at 0.05–0.3 s on an idle
box, against a 2.0 s budget. So the test asserts on wall-clock.

**What is not real.** I found an apparent fix and it was wrong. Adding
`from universal_search.gui import control_center` to `tests/conftest.py` took the
module from 8/8 failures to 0/8, reproducibly, and interleaved A/B runs ruled
out drift. It looked like an import-order defect.

It is not. The same import applied to the **other** invocation moves the failure
the other way:

| invocation | plain | with the conftest import |
|---|---|---|
| `pytest tests/test_gui.py` | 3/3 fail | **0/3 fail** |
| `pytest tests/test_gui.py tests/test_gui_ux.py` | 0/3 fail | **3/3 fail** |

An import that trades one red invocation for another is not a fix; it is a
timing shift wearing a fix's clothes. The import was not committed, and
`tests/conftest.py` is unmodified.

**What else was ruled out, so the next attempt does not repeat it:**

- *Machine load.* The module fails 6/6 at **1–9 % CPU**. Not load.
- *Invocation style.* `python -m pytest`, `pytest.main()` and the `pytest`
  console script all fail identically. Not how pytest is started.
- *Test selection.* The same 24 tests pass as explicit node ids and fail as a
  module path — but that difference also disappeared under other conditions, so
  it is an artefact of timing, not a selection effect.
- *An import cycle.* There is none; `ControlCenterService`, `ControlSnapshot`
  and `ActionResult` are the same objects under every import order tried.
- *Thread accumulation.* One thread (`MainThread`) alive at failure.
- *Leaked Tk resources.* `close()` cancels the `after` callbacks cleanly.
- *A real product path.* Reproduced the exact test sequence in a bare process:
  a user opening the control centre as their first action gets a working
  window, and the superseded-action sequence drains correctly. **The product is
  fine.**

Every probe that wrapped `pump` made the test pass, and I initially read that as
"instrumentation perturbs timing". It was not — each probe imported a gui
submodule, which is the same shift as the conftest change. Two hours of that
was measurement error, not signal.

## What this leaves

`test_superseded_control_action_refreshes_snapshot` needs a wait that does not
encode a machine's speed. The honest fix is to give the control-centre window a
completion signal that does not depend on how many 50 ms `after` ticks the host
manages in two seconds — and to assert on that, rather than on elapsed
wall-clock. That is a change to how the test synchronises, not a change to the
product, and it has not been made here.

Until then it is a known flake: red on 3.12, green on 3.13 and 3.14 in the same
run, on the same commit.

## Still not closed

- **`evaluation/*_baseline.json` drift.** Running the suite locally rewrites the
  committed baselines with this machine's timings (`render_ms` moved 119.6 →
  87.0 ms across runs). Twice now these had to be discarded rather than
  committed. Phase 049 fixed this for the artefact manifest; it is still open
  for the baselines, and it is the same defect the WAL and installer
  investigations kept hitting.
- **`test_gui_ux.py` trio** — `test_no_results_state_explains_itself`,
  `test_query_error_is_explained_not_traced`,
  `test_clearing_the_box_drops_pending_results` — fail intermittently in a
  full-suite run and pass in isolation. Same signature as the control-centre
  one: a module-scoped Tk window plus a timing-based wait.
- **Three Tk roots.** Measured 5/15 concurrent runs failing when
  `test_gui.py` and `test_gui_ux.py` run together (two live Tk roots) versus
  0/15 with one, with the intermittent
  `Can't find a usable tk.tcl ... ttk/cursors.tcl` — a file that exists.
  `test_gui.py`'s own fixture docstring names the hazard and mitigates it
  per-module; `test_gui_ux.py` then opens a second root while the first is
  alive.
- **The M01 / WAL / installer-cost measurements** still need a machine at rest.
  Nothing here substitutes for that.