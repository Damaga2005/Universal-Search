# Changelog

All notable changes to Universal Search. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project version is
`src/universal_search/__init__.py::__version__`, single-sourced into the
PyInstaller resource and the installer (enforced by `test_release.py`).

## [Unreleased]

### Phase 048 - The support matrix, and an encoding defect twenty-five phases of incantations were hiding
Phase 048 asks that the CI/runtime mismatch be made explicit and resolved rather
than treated in silence as support. It was worse than a documentation gap.

- **The mismatch, measured in all three directions.** `requires-python` said
  `>=3.12` -- an open promise covering 3.15, 3.16 and everything after -- the CI
  proved exactly one version (3.12), and the development machine ran **CPython
  3.14.6**. The project was being written and run on a version nobody tested, and
  the package advertised versions nobody had measured.
- **Both halves closed at once, because fixing one leaves the problem.** The
  gating CI job now runs a 3.12/3.13/3.14 matrix, and `requires-python` is bounded
  at `<3.15`, so what the project promises and what it proves are the same set.
  Bounding costs a `pyproject.toml` edit when 3.15 ships, which is the right
  price for refusing to promise what has not been measured.
- **15 classifiers, where there had been none.** A package that ships a
  Windows-only application and declares nothing lets `pip` guess its platform.
  There is deliberately **no `POSIX` classifier**: the Ubuntu job exists but is a
  non-gating probe, and a classifier reads as a support claim.
- **A real encoding defect, found by measuring instead of assuming.** Every file
  this project writes is UTF-8 declared explicitly -- config, metrics, events,
  control-center state, logs. The CLI's own output was the single exception and
  inherited the machine's code page. On this Spanish Windows install
  (`locale.getlocale()` is `('es_ES', 'cp1252')`), redirecting `search` to a file
  produced **bytes that are not valid UTF-8**, failing to decode at byte 19. The
  characters survived -- `ó` and `ñ` exist in cp1252 -- so **nothing looked broken
  on screen** and the damage only appeared downstream. The CLI now declares
  `encoding="utf-8"`, and `errors="replace"` is kept because it is what stops the
  CLI dying while printing.
- **Twenty-five phases of `$env:PYTHONIOENCODING="utf-8"` retired.** Every command
  in this project's documentation and every shell in its history was prefixed with
  it, and nobody had checked whether it did anything. It was not preventing
  anything: it was hiding the defect. The declared tradeoff is that a legacy
  cp1252 console needs `chcp 65001` for accented output, which is the same
  requirement the project's own UTF-8 files already impose -- a wrong-looking
  character beats a stream no tool can read.
- **A contract change in an existing test, deliberately.** `test_cli.py` asserted
  that the CLI left the stream's encoding alone and emitted `?` for an
  unrepresentable arrow. Phase 048 changes that contract on purpose, so the test
  was rewritten to assert the new one -- the arrow survives, as UTF-8 -- with the
  reason written next to it. A test rewritten to pass is only legitimate when the
  contract changed on purpose and the reason is recorded; this one says so.
- **`docs/SUPPORT.md`**, stating what is supported, what is probed, and what is
  neither, with the measured value beside every claim. Including the two things it
  cannot answer: `windows-latest` is a Windows Server image rather than the
  Windows a user runs, and exactly one DPI/Tk combination has ever been observed
  (96 DPI, Tk 8.6, 2560x1440).
- **New gate, `python -m evaluation.portability_gate`: 9 invariants, all passing.**
  W6 -- "the interpreter running this gate is inside the declared range" -- is the
  check that would have caught the original problem. W2 and W3 make the promise
  and the proof the same set; W5 forbids a classifier that promises a platform the
  matrix refuses; W8 and W9 keep "probe" and "support" distinct in both the CI and
  the docs.
- **An error in the gate itself, on its first run.** W2 failed against a correct
  configuration: it read `<3.15` as a closed interval and counted 3.15 as
  declared. The instrument was wrong, not the project, and a test now pins that
  `<` is exclusive and `<=` is not.
- **Declared and not done: the CI matrix has never been executed.** The change was
  made without CI access, and the comment in `ci.yml` says so. What is measured is
  the full suite on 3.14.6. Until a green run exists, 3.12 and 3.13 are declared
  and configured, not proven. Also unmeasured: ARM64, macOS (no job, no claim),
  and any real installation -- which is phase 049's work.

### Phase 047b - one load check for every gate that measures latency
Four gates were unable to close, and one of them was closing *wrongly*.
`suggest_gate` reported NO SHIP at 12.6 ms against an 8.0 ms threshold on a
machine running a game at 88% CPU -- and 14.7 ms with these changes stashed
away, which is what established the machine as the cause rather than the code.
`fuzzy_gate` passed at 4.4 ms on a machine at 76%, which is the same mistake in
the opposite direction: a verdict that happened to land well for no reason.

- **`perf_gate.load_gate()` is now the single implementation.** Phase 045b gave
  `perf_gate` a two-signal load check -- a pure-arithmetic calibration for
  "was this process slowed", plus the OS figure for "was the machine busy" --
  and the weaker signal vetoes. `interaction_gate` had it. `fuzzy_gate` had a
  private `_cpu_load()` that only decorated a detail string and could not
  change the verdict. `suggest_gate` had nothing. All three now import the check
  instead of writing one: a load check written twice diverges, and the
  divergence is invisible until the two disagree on a verdict for the same
  machine.
- **Two readings, not one.** The first version of this fix sampled the load once
  at the start and still reported NO SHIP while the machine was at 76%: the
  neighbour's load arrives in bursts, and a single sample catches the gap
  between them. The check is repeated after the measurement, and a busy machine
  at either end withholds the verdict.
- **Both readings are recorded** in the baseline, along with the calibration, so
  the next run has its own reference instead of only the OS signal. That is why
  `LoadVerdict` now carries `calibration_best_s`: `suggest_gate` and
  `fuzzy_gate` indexed it as if it were the dict `measure_load()` returns, and
  two shapes under one name is two bugs waiting.
- **12 new tests in `tests/test_load_veto.py`**, with the load signals injected,
  because testing this for real would mean the test only runs when the machine
  is quiet -- which is exactly when the veto is not needed. Busy before, busy
  during, quiet throughout, and a check that the thresholds are not redefined
  anywhere else.
- **A debt retracted, because it was never real.** The 045 report declared that
  `runner.measure()` still used `datetime.now()` and that "its recency figures
  are not reproducible between years". False, and written without checking:
  `evaluation/runner.py` imports `time` and measures with
  `time.perf_counter()`. There is no `datetime.now()` anywhere in `src/` or
  `evaluation/` -- the only occurrence of that string is a *comment* in
  `quality_gate.py`. The correction is recorded in place rather than the line
  deleted, because this is the fourth time in three phases that a claim about
  the code was made without running it.
- The three remaining INCONCLUYENTE verdicts are the machine's, not the code's.
  No threshold was moved to make them disappear.

### Phase 047 - Storage and data lifecycle, and the operation that returned nothing
Phase 047 asks whether a user can understand and control the storage footprint
without touching the database. The measured answer was no, and not for want of
commands.

- **`maintenance(vacuum=True)` reclaimed 0 of 74,956,800 bytes while reporting
  that it had reclaimed 18,038 pages.** In WAL mode a VACUUM writes the rewrite
  into the WAL and the main file is only truncated at a later checkpoint; the
  method checkpointed before vacuuming and never after. On 4000 inserted-then-
  deleted rows: checkpoint-then-VACUUM returned 29,744 bytes (1.7%), VACUUM-
  then-checkpoint returned 1,595,600 (89%). Four mechanisms were measured on
  copies of the real database -- raw VACUUM plus checkpoint, VACUUM with
  `mmap_size=0`, VACUUM in DELETE journal mode, and `VACUUM INTO` -- and all
  four returned the bytes.
- **`SearchDatabase.compact()`** now does the swap in two phases: `VACUUM INTO`
  a candidate, verify it with `PRAGMA integrity_check` and a row count of all 20
  tables, then checkpoint, drop the sidecars and replace. A failed verification
  raises the new `DatabaseCompactionError` and the user's index is untouched --
  which is the reason the swap is two-phase. Measured on 1000 documents:
  71.2 MiB with the derived layers built, 4.4 MiB after purging and compacting.
  `maintenance(vacuum=True)` delegates to it, because an operation whose whole
  purpose is returning storage may not quietly return nothing.
- **The deletion paths the inventory already promised returned no storage at
  all.** `intelligence clear`, `SemanticIndex.remove_all()`,
  `FuzzyIndex.remove_all()` and the graph's `clear()` free pages; the bytes stay.
  66.7 MiB of free pages sat in a 71.2 MiB file with nothing reporting it.
- **95% of the index is derived, and it only exists after you use it.** Nothing
  derived is built at index time -- measured, the semantic, fuzzy and graph
  tables hold zero rows after indexing 1000 documents. Canonical index 3,801,088
  bytes (4.8%), semantic 55,799,808 (70.6%), fuzzy 4,395,008 (5.6%), graph
  15,130,624 (19.2%). By rows: 4,293 canonical against 293,252 derived. The
  semantic layer stores 200 content words per document, so its cost is
  superlinear in corpus size: derived is 5% of the index at 400 documents and
  95% at 1000.
- **The lifecycle contract is now complete for all 15 datasets.** `DataItem` had
  8 fields and the contract asks for seven items, so owner, schema/version,
  rebuild path and migration behaviour had nowhere to live -- which is why a
  derived dataset with no rebuild path was indistinguishable from a canonical
  one that cannot be rebuilt at all.
- **Two datasets that nobody declared.** The inventory described 13 categories
  while the schema has 21 tables. The five FTS5 shadow tables hold the actual
  inverted index and were invisible to anything reading `sqlite_master` by
  declared name; `schema_migrations` is the record every migration-behaviour
  claim rests on. Both are now declared, and `sqlite_sequence` is excluded by
  name so the gap is a decision rather than an oversight.
- **Bytes per table are impossible in this build and are not estimated.**
  Measured: `dbstat`, `sqlite_dbpage`, `sqlite_stat1` and `sqlite_stat4` all
  raise `no such table`, and `compile_options` lists only ENABLE_FTS3/4/5 and
  ENABLE_RTREE. The report returns `per_table_bytes: None` with the reason. What
  it does return -- file bytes, pages, free pages, rows per category -- is exact,
  and the gate checks it against a `SELECT COUNT(*)` per table rather than
  trusting its own code.
- **New `universal-search storage show` and `storage compact`**, answering the
  question `privacy show` could not: why is my index 80 MB when my documents are
  4 MB. The report's reclaimable figure is labelled a *floor* (`freelist_count *
  page_size`), because a VACUUM also defragments pages SQLite never freed -- the
  gate measured a 20.0 KiB floor returning 84.0 KiB.
- **New gate, `python -m evaluation.storage_gate`: 9 invariants, all passing.**
- **Three defects in the measuring instrument, all the same shape.** The gate
  defined `use_every_layer`, documented it at length, and never called it -- so
  it measured an empty index and still reported SHIP on all nine invariants. L7
  demanded the returned bytes match the announced ones within 2% and failed at
  76%, because the announcement is a floor and not a quote; the invariant is now
  one-directional. And the `storage` command's dispatch was inserted inside
  `_privacy_command`, unreachable, because the chain is keyed on `args.command`.
- Two probes asserted false things before being refined: one claimed
  `maintenance()` vacuums when its default is `vacuum=False`, and one concluded
  `compact()` broke search on "0 results" for a term the corpus does not
  contain -- the same direct MATCH returned 0 *before* compacting.
- Declared and not done: the superlinear semantic cost (55.9 MB per 1000
  documents) is not optimised, because deciding whether 200 content words per
  document is the right number is a product question rather than a storage one.

### Phase 046 - Indexing measured at scale, three correctness defects, no optimisation
Phase 046's rule is that optimisation requires a *measured* bottleneck. The
honest outcome was that **no optimisation ships**: the bottleneck is FTS5's
segment merging, which lives inside SQLite, and three candidate changes were
measured and rejected.

- **Three real defects, invisible to all 1410 tests**, because each only appears
  when a real object meets a real reader.
  - `index_root` bound `cancel=cancel.cancelled` -- the token's boolean
    *property* where the extractors expect a `CancelCheck` callable. Measured:
    6 documents in, 6 extraction errors, all 6 indexed with empty bodies, and
    zero errors surfaced anywhere a user would see.
  - The loop never consulted the token. `scan_local` does not receive one, so
    the default path -- the one the background worker uses -- indexed a
    *cancelled* pass in full.
  - `ExtractionResult(error=...)` left `status = OK`. `ExtractionStatus`
    documents that `error` outranks everything and did not enforce it, so a
    failed extraction was recorded as a successful one. The consequence is the
    serious one: the fast path compares size and mtime, so a blanked row looked
    unchanged forever. Measured: one cancelled extraction, then a clean pass
    reporting `unchanged=6` with the document still blank and unsearchable.
    The fix is a `__post_init__` that makes "an error with status ok"
    unrepresentable, because a status field that can lie is worse than none.
  - `no_content` deliberately stays on the fast path, or every metadata-only
    binary in the corpus would be re-extracted on every pass forever.
- **A false claim of ours, corrected in the code and in three prose copies.**
  Phase 045 explained a 16 KiB index-size spread by saying the traversal is not
  sorted. It is sorted: `providers/local.py:140`. The second theory worth
  testing -- absolute path length, since every pass runs in a fresh `mkdtemp()`
  root -- was refuted by measurement (four roots, four 79-character paths, four
  identical totals). What moves those bytes is still unknown, and the gate now
  records its ignorance instead of guessing a third time.
- **The curve, measured.** Space is linear and memory nearly constant: 3.79 ->
  3.59 KiB per document and 2.42 -> 3.36 MiB peak from 1k to 4k documents, while
  cost per document rises 1.64x. The time is not ours: raw SQL through 10k rows
  takes 0.249 s without FTS and 1.539 s with it, so FTS5 is ~90% of a pass, and
  its cost is bursty rather than smooth (5k took 2.779 s, 10k took 1.539 s) --
  the signature of segment merging.
- **Three optimisations measured and rejected.** `wal_autocheckpoint=20000`
  halved an insert-only probe (1.521 s -> 0.695 s at 10k) and changed the
  product by nothing: best of three through `index_root` at 5k, 51.86 s against
  SQLite's 50.74 s, 0.98x and inside the noise. The probe measured a different
  workload -- the indexer reads once per file *between* writes, so a larger WAL
  penalises the reads the probe never performs. `cache_size=-64000` and
  `mmap_size=1GiB` (one run at 55 s) were worse. All three numbers are written
  next to the pragma that was **not** changed.
- **New gate, `python -m evaluation.scale_gate`: 8 invariants, all passing.**
  It measures the *shape* of the curve rather than an absolute latency, because
  a millisecond threshold on a shared machine is a coin toss and `perf_gate`
  already owns that job with a load veto and a machine fingerprint.
- **Two defects in the measuring instrument itself, found and fixed.** The first
  curve was taken with `tracemalloc` attached, which instruments every
  allocation, so the pass measured the profiler as much as the indexer. And S6
  computed `blank_before - blank_after` -- the set of documents that *were*
  repaired -- so it reported one unrepaired document on a run where the repair
  worked perfectly.
- Declared and not done: the 100k profile is extrapolated, not measured; the
  WAL's run-to-run variance is measured (1.5 s to 5.6 s on identical input) and
  unexplained; and the junction case in `scan_local`, which has no visited set
  and is the one resource without a bound.

### Phase 045b - Every gate run on a quiet machine, and four real defects
The 044/045 work was committed with two gates (U9, V11) and the fuzzy gate
unrun because the machine was busy. With the machine idle, all of them ran, and
running them was worth more than another phase would have been: four defects
surfaced that no amount of code reading had found.

- **U9 and V11 close.** 50 rows in **106.0 ms** against a 200 ms budget, and
  keystroke-to-result best of 3 in **174 ms** against 600 ms. The 176 ms
  reported for U9 in the phase-045 report was a loaded number; on a quiet
  machine the corpus being 44% larger does not threaten the budget.
- **`test_cli_module_guard_propagates_tray_exit_code` failed and is fixed.** It
  is the one failure in the whole 1395-test suite, and phase 043 changed
  `setup_logging` from one argument to two while this stub kept the old shape,
  so the test died on the signature before reaching the thing it is about. The
  stub now records both arguments and the test asserts the tray is configured
  from the resolved log level -- so a regression there fails on the value
  instead of on a `TypeError`.
- **`fuzzy_gate` T1 had fallen to 6/10 and the reason was a broken label, not a
  broken layer.** `ROBUST_QUERIES` expected a *string* matched with `in` against
  a corpus id, so `"bjt"` had always meant "an id that starts with bjt-". The
  corpus grew `T6_BJT_Apuntes.md`, whose body reads "el transistor bipolar" and
  whose file name is that acronym; the fuzzy layer returns it first for a
  typo'd "transisto", which is *correct*, and the gate called it a miss. The
  expectation is now an explicit set of ids, the threshold stayed at 0.80, and
  the one genuinely unreachable query ("azaarfann", more than the two-edit
  budget from every word in the recipe) is declared in `OUT_OF_SCOPE` with a
  measured reason instead of being quietly absorbed.
- **`perf_gate` blamed the machine for its own metric.** When two passes of the
  same build disagreed, it printed "the machine is not at rest or another
  process is compiling" -- unconditionally, without measuring either. It was
  observed doing so at 2% CPU with a calibration of 1.02x. The message now
  reports the load it actually measured and says which way it points.
- **`perf_gate` demanded byte-exact index size, and the premise was false.**
  The special case asserted "two runs produce the same bytes or one of them is
  broken". Measuring all three components separately showed the WAL is already
  0 bytes in this build and the 16 KB difference is in the *main database
  file*. **The mechanism phase 045 gave for it was wrong**, and phase 046 says
  so: the traversal is sorted (`providers/local.py:140`), and the second
  theory worth testing -- every pass runs in a fresh `mkdtemp()` root and the
  absolute path text is stored twice -- was refuted by measurement (four roots,
  four 79-character paths, four identical totals). What moves those bytes is
  still unknown, and the gate now records its own ignorance instead of
  guessing a cause a third time. The size is a count, not a fingerprint, and is
  now compared with the tolerance the metric already declares (10%, 0.10 MiB floor)
  against a measured variation of 0.2%.

Plus two cleanups found while measuring: dead code (`probe_index` and its
`IndexProbe`, written for phase 045 and never called) removed, and the four
names the quality gate and the tests reach across module boundaries in
`diagnose.py` made public, because a leading underscore on a cross-module
import is a lie about the shape of the code.

New `tests/test_fuzzy_gate.py`, 15 tests: every id the gate will accept must
contain a word within the layer's own two-edit budget of the query, so a future
corpus addition cannot widen the gate for free. Two earlier versions of that
check were wrong and both are recorded in the file -- a character-by-character
comparison against the start of the path, and a trigram threshold that
rejected "azarfan" even though the layer answers it because a transposition is
inside a budget trigrams cannot see.

### Phase 045 - Search quality measured by cause, and a ranking left alone
- **The phase's rule is negative**, so the phase begins by measuring whether a
  ranking change is justified at all. It is not: `evaluation/diagnose.py` probes
  every missed (query, document) pair down the phase's ten causes and reports
  the evidence, and **none of the eight measured failures enters the candidate
  pool even at `limit=500`**. Ranking only reorders what retrieval returned.
- **`evaluation/quality_gate.py`: 13 invariants, all passing.** Nine candidate
  weights were measured end to end; none moved MRR. Q10 pins the weights, so a
  dial turned inside this phase without a reproducible failure behind it is a
  failing gate.
- **The corpus grew from 27 documents / 18 queries to 39 / 30**, covering the
  workloads it had none of: code, Office, a *readable* PDF, English, French, a
  second duplicated pair, and filenames that are nothing but abbreviations. The
  binary cases are built by deterministic generators in `corpus.py` -- a real
  PDF with a real cross-reference table, real OOXML packages -- because a
  workload that is not in the corpus cannot regress, and a workload that cannot
  regress is not a workload that is tested.
- **Growing the corpus made five existing relevance sets visibly incomplete.**
  Two queries looked like ranking regressions until each was judged
  individually, and two of the new intruders turned out to be distractors that
  had to stay *out* of the relevance set. The committed baseline was
  regenerated with the measurement that justifies it.
- **Two metrics the repository had never had.** Filter accuracy, because a
  relevance metric cannot see a filter leak; and zero-result accuracy, which
  the corpus supported with exactly one query. P@10 and R@10 now exist too --
  `K_VALUES` was `(1, 3, 5)` in four places.
- **`failure_class` is now a computed diagnosis, not a remembered one.** It was
  a human label on 4 of 18 queries, copied into a JSON row and never used to
  decide anything. Two probes matter most: one separates "the words are not
  there" from "the words are there and it still fails", and one distinguishes
  simple inflection from genuine vocabulary absence.
- **One limitation declared, challenged on every run.** `unicode61` indexes a
  run of CJK ideographs as one token, so no substring query reaches it. The
  gate measures the alternative rather than asserting the limitation: `trigram`
  answers that query and then answers **four** labelled queries less well,
  because a three-character tokenizer cannot match a two-character term. Net
  loss, so the exchange is declined and the threshold is computed over what the
  build claims to answer, with the exclusion named and asserted by tests.
- **A stale measurement was re-measured, not inherited.** The `docs/RANKING.md`
  headroom table was from phase 013 on a corpus that no longer exists. Two rows
  had changed without a line of ranking code changing.
- Test count 1365 -> 1395. `evaluation.gate` stays PASS 23/23. No push.

### Phase 044 - Local learning that actually learns, and a weight measured
- **`learn.py` is the value of a set of events**, as pure functions over numbers.
  A ranking signal that cannot be evaluated on its own cannot be argued about.
- **Learning is scoped to the query it was learned from.** `USAGE_COUNTS_SQL`
  counted opens and never read the `query` column it had been writing since
  phase 008, so a document opened for `examen` was also boosted for `receta
  paella`. An event now counts 80% for the query it was recorded under and 20%
  for the document in general; four opens under a *different* query move nothing
  at all.
- **Old events fade.** `opened_at` was in the schema and unread, so a 2024 open
  carried full weight with no cap and no expiry anywhere. Four buckets now
  (30/90/365 days, then a floor) — a step rather than a curve, because a step can
  be read on a screen. Four opens inside a month are the whole signal; the same
  four spread over a year and a half are worth nothing.
- **One accidental open changes nothing.** Below two effective events the signal
  is exactly zero, which is what makes cold start *deterministic* rather than
  merely quiet.
- **The weight was measured, not inherited.** `ACTIVATED_USAGE_WEIGHT` 0.5 ->
  0.25. At 0.5 the signal was 3.45% of the final score against `recency`'s
  2.14%, i.e. stronger than the signal the project already documents as
  secondary — a direct contradiction of the contract. The 23 consecutive score
  gaps in the evaluation corpus are bimodal (near-ties under 0.024, real margins
  over 0.044); halving the weight costs two reorderable pairs out of 23 and both
  are near-ties.
- **Dominance is enforced, not argued.** The weight ordering cannot promise exact
  matches stay first — a quarter of `filename_exact` is nothing when a rival's
  content signals are stronger. So `ranking.py` refuses the boost outright for
  any candidate whose file name *is* the query, and the rule lives in the ranker
  rather than in the SQL so a hand-computed boost gets the same answer.
- **Learning explains itself.** `explain_notes` had an entry for the personal
  context and none for the one signal that silently moved results.
- **`universal-search usage effect`** reports what the recorded history is worth
  *now*, per (document, query): opens surviving the decay, current weight, and
  how many signals no longer move anything while their row is still on disk.
- **Orphaned events are cleaned up.** `Indexer._delete()` removes a deleted
  document's usage rows, so its query text does not outlive the file.
- **New gate, `python -m evaluation.learning_gate`: 11 invariants**, all
  passing over a synthetic interaction history on the fixed corpus. Learning
  promotes 6 of 33 (query, candidate) pairs and demotes none; with no history
  MRR is unchanged and not one of the 18 corpus queries moves by a rounding step.
- **Declared, not built:** source and document-type preference. Both are by
  definition query-independent, which is precisely the defect this phase
  removed, and `SOURCE_SCORES` is still all 1.0.
- Test count 1342 -> 1365. Per instruction only the tests touching the changed
  modules were run. No push.

### Phase 043 - One typed, validated, migrated settings system
- **`settings.py` is the schema.** For every user-configurable value: type,
  default, valid range, whether it is advanced, whether changing it needs a
  restart, and what it governs. Eighteen settings in six groups. No Tk, no I/O.
- **The labels are not in it, on purpose.** They live in `gui/strings.py` under
  `SETTINGS.<KEY>.LABEL`/`.HELP`, so the catalogue stays the single list of
  visible text and the phase 039 audit keeps covering it. A first commit had a
  `GROUP_LABELS` dict with Spanish inline — the second place to translate that
  the module's own docstring rules out — and a test caught it.
- **Ranges, because without them the numbers were landmines.** A negative
  `indexer_file_delay` in `config.json` reached `time.sleep(-1)`, raises
  `ValueError` inside the indexing loop and **fails the whole pass**. An interval
  of `0` makes the worker's wait loop spin with no sleep. Neither was reachable
  from a typed settings window; both were reachable by editing a JSON file. An
  out-of-range value now loads (forgiving) but is *reported*, and repaired only
  when the user asks — a loader that silently rewrote values would be deciding
  for them.
- **`config.json` declares which build wrote it.** `CONFIG_VERSION = 2`, with
  the model copied from the index database, which already had `PRAGMA
  user_version` and a migrations ledger: a version, steps, and a refusal to read
  a *newer* file as if it were ours. A file with no `version` is version 1.
- **A newer build's keys no longer die on the next save.** `save()` read the
  file, kept what it does not own, and did not downgrade a higher version. Down
  and back up no longer loses a setting.
- **The temporary file carries the pid.** It was `config.json.tmp`, a fixed name,
  written by the window, its service, the control centre and `set_autostart`;
  two writers in the same second collided. Three characters, one class of bug.
- **A settings window**, grouped by intent — what is indexed, how you search,
  when it indexes, how it looks, what is remembered, diagnostics — and not by
  module. Every control explains itself; the six that need a restart say so; the
  three that are data are shown read-only because they belong to the control
  centre, and a second surface for them is the thing this phase removes. Reset
  asks, and the question names what it will *not* delete.
- **Export and import.** The prompt marked them "where justified"; they are
  justified by needing to be able to check them. The file carries preferences,
  carries no data, and says in its own text that it carries no passwords.
  Import refuses a newer file, applies what is valid, rejects what is out of
  range and ignores what it does not know.
- **Four settings that existed only as command-line flags**: `semantic_enabled`,
  `tray_enabled`, `result_limit` and `log_level`. `setup_logging` had `INFO`
  hard-coded; the window read `theme` and `ui_scale` once at construction.
- **Precedence is data now.** `appconfig.PRECEDENCE` is an ordered, explained
  table and a gate checks it. Nothing in it overrides a *setting*: the
  environment chooses where the settings live, never what they say.
- **The tray's "Configuración" item now opens something.** It has advertised a
  settings window since it existed and opened the search window instead.

- **New gate, `python -m evaluation.settings_gate`: 13 invariants**, all
  passing, and the first gate of this programme that needs no window.
- Catalogue grows from 160 to 221 entries, every one of them a label or an
  explanation somebody can read.
- Test count 1288 -> 1342. Per instruction only the tests touching the changed
  modules were run: the window, the service, the configuration, the CLI, the
  gates and the settings window itself. No push.

### Phase 042 - Search as one interactive flow, and the error that never arrived
- **A rejected query could not reach the user.** `SearchService.search` swallows
  `QueryError` into `last_query_error` and returns `[]`, so the window's
  `except QueryError` never ran and phase 041's "Consulta no válida" state was
  dead code in production. Phase 041's gate passed it because its test stubbed
  the service to raise — which proves the *handler* works and says nothing about
  whether the application reaches it. `SearchService.search_or_error()` returns
  the results and the feedback together, and `search` becomes a one-line wrapper
  over it so the CLI's contract is untouched. Reading the field on the next line
  would have been a race anyway: every keystroke runs on its own thread against
  the same service instance.
- **Six mechanisms existed and had never reached the screen**: `last_query_error`,
  `sort_results`/`group_results`/`pool_size`, `SavedSearch`, `QuerySuggester`
  (never imported by `gui/`), `explain=True` (never passed by anyone since phase
  004) and the fuzzy layer, which the CLI has wired since phase 031 and the
  service never did. Each phase's own gate measured its mechanism; none of them
  could ask whether the window called it.
- **The fuzzy layer now runs in the window**, with `AppConfig.fuzzy_enabled` to
  turn it off, assembled in one place for both directions.
- **Sorting and grouping** come from the phase 036 vocabulary, not a second
  implementation. Reordering what is on screen is free; a sort other than
  relevance re-runs the search with the widened pool, exactly as the CLI does,
  rather than quietly reordering a truncated set. Group header rows carry an id no
  result index can be, so every selection helper skips them by asking whether an
  id is an index.
- **Saved searches, history and suggestions** get a surface. A saved search
  restores all six fields and holds no more. Disabling history recording
  **does not delete** what is already stored — two different intentions, and the
  one that deletes says so. Applying a suggestion is `query_var.set(...)`, which
  is the same thing as typing the corrected words: there is no second path to
  run a query. The retention rules were explicit in code and implicit everywhere
  else; `MAX_QUERY_CHARS` now has a name and the window states both numbers and
  the file.
- **Explanations are asked for, never assumed**: `explain=True` is real work per
  result, so it is a menu check, off by default.
- **Rendering is incremental and counted.** A row is identified by its position,
  so a row whose five values are unchanged is left alone. An unmeasured claim of
  incrementality is not a claim.

- **New gate, `python -m evaluation.interaction_gate`: 11 invariants**, all
  passing, about the seam rather than the mechanisms. Exit 0.
- **Four real defects found by the tests of this phase, three of them seams:**
  `explain` was annotated `dict[str, float]` while the fuzzy layer stores a list
  of dicts there (the annotation was wrong, not the layer, and the shape is
  pinned by a phase 031 test); the fuzzy wrapper exposes `search` and `database`
  and nothing else, so wrapping with it made `record_open` raise `AttributeError`,
  the service swallowed it, and **local learning quietly stopped working** with
  no symptom but a log line nobody reads — three delegations added, the ones
  `HybridSearchEngine` already had; `explain_var.get()` was read on the worker
  thread, which turns every search into a hard failure the moment anyone asks
  for explanations; and `_on_view_changed` compared against the wrong pool size,
  which the gate caught on its first run.
- **Phases 031 and 042 overlap, and the order is now pinned by a test.** With the
  fuzzy layer on, a typo already produces results and there is nothing to
  suggest. The suggestion is the second line of defence, which is where it
  belongs; a gate that described it as the first would be describing a
  configuration nobody runs.
- Test count 1249 → 1288. Query semantics, ranking, MRR and every existing gate
  are untouched — no assertion in `test_query_parser`, `test_ranking`,
  `test_evaluation` or the phase 031/032/036 gates was changed.

### Phase 041 - Product experience: the results pane finally has a hierarchy
- **The audit found the panel had none.** The results were a `tk.Listbox`
  holding one string per row — `[source] name · TYPE · folder — snippet` — five
  different things flattened together, with no headings, and **clipped at the
  widget edge**: a long filename did not shrink or get an ellipsis, it vanished.
  It is now a `ttk.Treeview` with five real, labelled columns (Nombre, Carpeta,
  Tipo, Fuente, Coincidencia) over a three-line detail pane.
- **The widget choice was measured, not preferred.** A `Canvas` would have
  allowed per-column typography, but it is not in the phase 039 list of
  interactive controls — so the surface carrying the whole product would have
  been invisible to every accessibility instrument this project owns. What a
  Treeview cannot do, the report says: the type is per row, not per column.
- **The source column hides itself** when every result is local, which is the
  rule the row already had ("a column of *local* on every row is noise")
  applied one level up.
- **"No results" was a status line and a blank rectangle.** The largest region
  of the window said nothing in the state a user meets most after typing
  something that does not exist. There is now one message surface with a title
  and a hint, used for the empty, zero-result and failed states, and a fresh
  window explains the product and its two most useful shortcuts.
- **The search box now says what it is.** It had an accessible name and nothing
  a sighted user could see; the placeholder follows `gui_app.placeholder_visible`,
  a rule rather than widget logic, because whether a window holds the desktop
  focus is not something a test can promise.
- **`ui_scale` scales the gaps now, not only the fonts.** Padding, row height
  and fixed column widths come from `theme.spacing()`. Until now "larger text"
  gave bigger words inside the same tight padding; an AST test fails if a
  non-zero padding literal comes back.
- **Two error paths, two defects, both of the kind the 039 audit exists to
  find.** A rejected query was drawn in exactly the same muted grey as an
  ordinary status line — the twin of the defect 039 fixed on the *other* error
  path. And a failed search left the previous query's answers listed on screen
  as if they were the answer to the new one. Both fixed, both measured by
  invariants U4 and U5.

- **`Treeview.selection_clear()` with no arguments clears nothing.** Tcl reads
  the missing item list as "no items to clear", not "everything". Measured, not
  assumed: with it, arrowing down the results *accumulated* selection and the
  detail pane kept showing the first row. `Treeview.bbox()` only describes
  visible rows and raises for absent ones, so the Page Down size — previously a
  literal 10, now derived from the rows actually on screen — had to count
  upward until the pane says "no further".
- **The phase 039 palette gate caught `accent` becoming undrawn** the moment the
  `Listbox` went, because its `highlightcolor` was the only thing drawing it. The
  gate said so before any of this was invented. `accent` now draws the column
  headings and the focused search field, and `CONTRAST_PAIRS` grows by the one
  pair that really appeared ("column headings", `AA_LARGE`).
- **Two tables answered "what kind of file is this"** — `rows.extension_label`
  said `DOCX`, a `TYPE_LABELS` table in the window said `Word` — and the row
  and the detail pane could disagree. One table now (`rows.type_label`), and
  `rows.format_result_row` is deleted: with the pane changed it had no callers,
  and a formatter with no callers is a second place to change the next row.
- **Nine visible status lines were f-strings**, which is why the phase 039
  string audit never saw them: an `ast.JoinedStr` is not a literal. All are
  catalogued now, along with the "forget documents" confirmation — the most
  safety-relevant string in the product, where "your files on disk are not
  touched" was buried mid-sentence. An AST test fails if a literal or an
  f-string ever reaches `_set_status` or `_show_message` again.
- **The diagnostics window was left behind by the spacing work**, with `padx=8`
  literals and an inline title. The AST test found it.

- **New gate, `python -m evaluation.ux_gate`: nine invariants**, thresholds
  zero, one number each — one labelled column per part of a result, an empty
  state with words in it, a primary flow that needs no mouse, failures drawn as
  failures on both paths, no stale answers after a failure, long paths and
  snippets reachable, gaps that follow the UI scale, six of six pane roles that
  change with the theme, and a full page of rows inside the interaction budget.
  **Eight PASS; U9 is INCONCLUSIVE with exit code 2**, because the machine was
  busy with something that is not this project and the phase 038 load veto said
  so. The parts that *were* measurable are in the report: 50 rows insert in
  2.5–5.6 ms, a plain Treeview lays them out in 0.8–1.1 ms, and each custom
  style option adds 4.4–8.8 ms — so the pane costs ~20–30 ms per page, which is
  what "compact and themed" buys. The total was not claimed, because it was not
  measured.
- `evaluation/ux_baseline.json` records the run, including the load note.
- `docs/development/041-windows-ux-checklist.md` is the manual Windows
  checklist the prompt asked for, with each of the ten validation areas marked
  automatic, manual, or **declared out of scope** — column sorting is out of
  scope, and the reason is written down rather than left as an omission.
- Test count 1216 → 1249. `python -m evaluation.gate` and the phase 039
  accessibility gate both stay green; the latter grows to 8 contrast pairs.

### Roadmap 041–050 — 041 done, 042–050 planned
- The former duplicate product roadmap numbered 031–040 is now the planned 041–050 programme.
- Phases 031–040 remain the completed Universal Search 2.x programme and are not renumbered.
- **Phase 041 is implemented** (see above). Phases 042–050 remain planning only;
  no implementation status is implied for them.


### Phase 040 - Quality gate v3: re-measure everything and decide
- The gate goes from **13 invariants to 23**. The thirteen from phase 030 are
  unchanged; the ten new ones are one per promise the 031–040 programme made,
  so a gate that stopped checking at the old boundary would have quietly stopped
  guarding the nine phases it had just added — which is how gates rot.
- `PHASES` widens from `range(1, 31)` to `range(1, 41)`. A gate that documents
  001-030 and calls itself closed is guarding a programme that ended nine
  phases ago.
- The new invariants, and what each one is standing in for:
  - **optional layers are removable** — "optional" means *removable*, not merely
    present. After `remove_all()` on both the fuzzy and the semantic layer, 0
    rows remain and exact search still works. Phase 031's own gate could not see
    this because it always had the layers switched on.
  - **hostile archives are bounded** — the per-entry budget and the zip-slip
    refusal are in the extraction path, not in a document.
  - **batch actions stay inside the results** — one action opens exactly the
    paths it was handed, never a path of its own, capped at 50.
  - **portable never writes to the user directory** — everything inside
    `UniversalSearch-data`, **0** files under `%LOCALAPPDATA%`.
  - **portable never falls back silently** — an unusable folder raises instead
    of relocating the index somewhere the user does not know about.
  - **both builds pin one version** — 2.0.0 in all three places, the icon in
    both, and the one-file spec does not call `COLLECT`, which is the defect
    the 2.0.0 release published.
  - **the perf gate can decline to conclude** — three distinct exit codes and a
    machine under load genuinely vetoes the run.
  - **every visible string is catalogued** — 86 entries, 0 literals left inline.
  - **colour contrast meets WCAG AA** — 7 pairs in both themes.
  - **saved searches hold no user data** — query and presentation only, so the
    config file never becomes an index of someone's documents.
- Every phase gate re-run: 031 SHIP 6/6, 032 SHIP, 033 SHIP, 034 SHIP, 035
  SHIP, 036 SHIP 9/9, 037 SHIP 11/11, 039 SHIP 6/6. Search quality unchanged at
  MRR 0,833. pyflakes clean.
- **The performance gate reported INCONCLUYENTE (exit 2) while the full suite
  was running**, having executed no measurement code at all, and this report
  does not quote a latency figure from it. The idle-machine run is in the phase
  038 report: 5% CPU, calibration 1.00x, all nine metrics inside tolerance,
  **PASS**. Mixing the two as if they were comparable is exactly the document
  phase 038 exists to prevent.
- The gate also gained its own gate-suite job in CI: `test_v2_gate.py` tests
  the gate, so leaving it ungated would make the newest invariants the first to
  rot unnoticed.
- **A gate that crashed while reporting.** The accessibility gate prints the
  interface's own strings and one of them contains U+25BE ("Recientes ▾"),
  which the Windows cp1252 console cannot encode. Piping the output — which is
  exactly how CI and the test that verifies the gate run it — raised
  UnicodeEncodeError halfway through the report and the gate printed nothing.
  It did not show up by itself: `cli.py` has done
  `sys.stdout.reconfigure(errors="replace")` since phase 005 and all ten gates
  were written without it. A gate that dies reporting is worse than one that
  reports a failure, because the crash looks like a broken gate rather than a
  broken interface. Fixed in all ten.
- **Open, declared: the 031 and 032 gates measure latency without checking the
  machine's load.** The 032 gate failed T5 at 16.47 ms against an 8 ms
  threshold with a game client at 93% CPU — the same measurement 031 reported
  as 18.70 ms loaded and 7.50 ms idle, and precisely the gap phase 038 was
  built to close. Migrating those two to `perf_gate`'s methodology is future
  work; until then their T5 figures have to be read alongside the CPU load the
  gate prints on the same line.
- **Decision: published.** `docs/RELEASE.md` step 15 says to push only on
  explicit instruction, and when this phase was written there was none — so the
  recorded decision was *not* to publish, which was the right answer then. The
  instruction arrived afterwards and explicit, and the decision changes with
  it. That is what a procedure written as a rule is for: not a promise to
  release, but a condition to be checked. Pushed to `main` after the gate
  reported 23/23, the tree was clean, and all three executables had been built
  and started by `packaging/build.ps1`.
- **Deliberately not in that push**, because they are separate steps with their
  own checks in the release list: the version tag, publishing the artefacts
  with their hashes, and the release notes. The version stays `2.0.0`; this
  work does not bump it, because raising it is a decision about what the
  software is, not a side effect of finishing a programme of phases.
- **The six skipped tests are not all the same kind**, which is the easy thing
  to get wrong when counting them: two are the symlink tests this operating
  system will not create (unchanged since phase 020), one is the tray's
  native-icon smoke (opt-in by design, behind
  `UNIVERSAL_SEARCH_TRAY_NATIVE_SMOKE=1`), and three are the Tk window tests in
  `test_accessibility.py`, skipped with the reason when the runtime does not
  start in that instant. None counts as passing, and the number the docs state
  is the 1216 *collected*, because that is the figure `pytest --collect-only`
  can know without running anything.
- **Full suite: 1210 passed, 6 skipped, 0 failed** (1216 collected). Two
  earlier runs of the same tree failed two `test_gui_ux` tests, because the
  Tcl/Tk runtime sometimes cannot read its own library for a fraction of a
  second when the suite saturates the machine; both pass in isolation. All three
  runs are recorded rather than only the flattering one, because a gate that
  publishes a number and hides the variance is exactly what this programme has
  spent forty phases correcting. No threshold was raised to hide it.
- `tests/test_v2_gate.py` grows from 20 tests to 34, and two of them assert the
  gate's own *extent*: that there are exactly 23 invariants, that all ten new
  ones are present by name, and that `PHASES` reaches 040. A gate that stopped
  checking at the old boundary would do so without turning red, which is why
  the boundary needs a test of its own. Two of the new ones assert that the new
  checks can fail — an uncatalogued string and a palette that washes out.

### Phase 039 - Accessibility and interface, measured instead of promised
- **Every user-visible string moves to a catalogue** (`gui/strings.py`, 86
  entries), keyed by what the string *is* (`MENU.INDEXER.PAUSE`) and never by
  what it says. `untranslated_literals()` scans the AST of the GUI for literals
  still written inline, so a new button with its label in the code fails the
  suite instead of shipping untranslatable. A missing key raises rather than
  rendering an empty label, and both a missing and a **misspelled**
  `{placeholder}` are errors — `str.format` would put a literal `{count}` on
  screen and say nothing.
- **Accessibility is an instrument, not a claim.** `gui/accessibility.py`
  walks the widget tree the way Tab does, reads what a screen reader would
  announce, and computes WCAG 2.1 contrast ratios for the pairs the window
  actually draws. Evidence: `python -m evaluation.accessibility_gate`, **6/6
  PASS**, zero tolerance on every gate.
- **Four defects found and fixed, all in the shipped window**:
  - the three comboboxes and the recents button relied on the platform default
    for `takefocus`, so their keyboard reachability depended on the style. The
    entry and the listbox had declared it since phase 017; these four had not.
  - an error was drawn in exactly the same muted grey as "Listo — escribe para
    buscar", so a failure a user needed to read looked like a message they could
    ignore. `_set_status` now takes a severity, and `danger` and `busy` — which
    were in both palettes and drawn nowhere — finally have a use.
  - `surface` was declared in both palettes and rendered by no widget. Rather
    than invent a contrast requirement for it, it was removed, and a gate now
    fails if a palette colour is not used somewhere.
  - `cget("text")` on a `ttk` widget configured with `textvariable=` returns the
    **Tcl variable name** (`PY_VAR16`), which the first draft of
    `accessible_name` would have announced as an accessible name. And a
    combobox showing "(todos)" is called "Tipo:", not "(todos)".
- **Accessible names are declared, not guessed.** Tk has no `aria-label` and no
  `labelwidget`, and three labels in this window share one parent frame, so any
  positional heuristic would name three different filters "Contexto:". The
  association is declared where the widget is built, and the audit fails if an
  interactive control has none. That is also the data a real screen-reader
  bridge would consume.
- The catalogue's own `note` field was removed: the key already says where a
  string appears, and a second copy of that information is a second copy to
  forget.
- **Two bugs in the coverage detector itself**, both found by using it. It
  compared the call's *name* against the keyword names, so it never inspected a
  single widget label and reported the interface as covered when it had checked
  nothing; the rule is about the argument, not the constructor. And its
  `col_offset` is a **UTF-8 byte** offset, not a character offset, which
  corrupted every line with an accent — the rewrite script refused to write
  because it validates with `ast.parse` first, which is the only reason that
  was a near miss rather than a disaster.
- `tests/test_accessibility.py`: 34 tests (1 skipped where the window has no OS
  focus, with the reason), including the whole journey with no mouse — type,
  arrow down, open — with no `invoke()` and no `<Button-1>` anywhere.
- The gate is exercised **in a subprocess**, and the reason is worth stating:
  its first version called `main()` in-process, and the destroyed Tk
  interpreter left the process unable to create another ("Can't find a usable
  init.tcl"), quietly turning fourteen later tests in the same file into skips.
- **Not measured: a real screen reader.** No Narrator or NVDA was available on
  this machine. The names are prepared and verified where a bridge would read
  them; hearing one announce them is the check that is still missing.
- Full suite: **3 failed, 1196 passed, 3 skipped** (1202 collected). All three
  are the known Tk `pump` flake under load — two in `test_gui_ux`, and the gate
  subprocess (which builds a real window) in `test_accessibility`. Each passes
  in isolation, and `test_gui_ux` + `test_accessibility` together are green.
  Recorded with the evidence rather than smoothed over.

### Phase 038 - A reproducible performance gate that can decline to conclude
- `python -m evaluation.perf_gate` compares indexing, search and index size
  against the committed `evaluation/perf_baseline.json` and returns **0 PASS,
  1 FAIL, 2 INCONCLUYENTE**. The third code exists because there are three
  situations and two answers cannot represent them: the 2.0.0 audit had to
  publish a footnote instead of a verdict for exactly this reason.
- **Load is measured before the number and can veto it.** A pure-arithmetic
  calibration workload is timed first, and the OS CPU figure is sampled (lowest
  of three, so the gate does not veto itself against its own previous run).
  Either being bad ends the run with nothing measured and nothing written.
- **Every timing is the best of N, never a mean.** Interference only adds time,
  so the minimum is the cleanest estimate of the real cost; a mean reports the
  neighbours' workload as if it were ours.
- **The measurement must repeat before it may conclude.** The suite runs twice
  and the spread between the passes is a gate in its own right. Two runs of the
  same build that disagree by more than the tolerance mean the tolerance cannot
  resolve the question, and PASS against it would be self-deception.
- **A busy machine cannot rewrite the baseline**, and the machine is named, so a
  baseline from another CPU is reported as indicative rather than compared as
  if it were comparable.
- Two tolerances per metric, because a percentage alone is meaningless at
  either end: a 25% regression on a 2 ms operation is not worth a red build and
  a 25% regression on 6 s of indexing is real. The allowance is
  `max(percentage, floor)`.
- **The gate found three bugs in itself.** Its docstring promised that two
  independent signals would veto and the code consulted only one: the first run
  reported "0.98x of the reference" — conclusive — with the machine at 88% CPU.
  Its machine comparison read `processor` at the top level of the baseline file
  instead of inside `machine`, so it printed "different machine" while
  displaying two identical dicts. And `db_open_mean_ms` varied 23% between runs
  of unchanged code, because it was timing the first connection in a fresh
  process (module load, SQLite load, WAL creation) rather than the steady open
  a worker actually pays; it now warms up first.
- The number this phase exists for: phase 031's own gate fails at **18.70 ms**
  with the CPU at 94% and passes at **7.50 ms** idle. Same code, same
  thresholds, opposite verdict.
- Full suite: **1 failed, 1162 passed, 3 skipped** (1166 collected). The failure
  is the same `test_gui_ux` Tk `pump` timeout as in phase 037, already shown
  there to fail identically with this code absent.
- `tests/test_perf_gate.py`: 34 tests over the decision logic with synthetic
  measurements, so they run in milliseconds and do not depend on how busy the
  machine is. Seven cover the wiring: that `main()` returns 2 without executing
  the measurement or writing the baseline on a busy machine, that it records
  one when there is no reference, that two identical passes return 0 *after
  measuring twice*, that a regression returns 1, that two inconsistent passes
  return 2 and **not** 0 even though every metric is inside its tolerance, and
  that the inconclusive report says nothing was written. Deliberately **no test
  runs the real benchmark**: a performance test that passes or fails depending
  on the machine is the instrument this phase removes.
- **The acceptance criterion was eventually measured and the phase is closed.**
  A game client external to this repository held the CPU between 63% and 94% for
  most of the phase — the same obstacle and the same cause as the 2.0.0 audit,
  and it was not touched — so the phase was committed but left open in the
  roadmap, by the same rule that made it applicable to itself. When the machine
  came free the gate ran for real: **PASS, exit 0**, calibration 1.00x of its
  reference at 5% CPU, all nine metrics inside tolerance and the spread between
  the two passes smaller than a regression would be tolerated.
- **Running it on an idle machine found two more defects.** With 15 repetitions
  per query the p95 sat on the 57th ordered sample, and two runs of the same
  code disagreed by 11.4% at 15% CPU — not noise, under-sampling. The honest
  response was to sample more, not to widen the threshold until the noise fit:
  40 repetitions now, so the p95 lands on the 152nd value. And the spread check
  used a flat percentage, which is simply wrong for an operation that takes
  about 4 ms; it now uses **the same allowance as the comparison**, on the
  argument that if a change smaller than the tolerance would not fail the
  build, two runs of the same code must be allowed to differ by that much too.
- **Not a blocking CI job.** A shared virtualised runner would report
  INCONCLUYENTE nearly always. It is a local measurement, and phase 040 decides
  its place in the release procedure.
- The committed baseline carried `"provisional": ["db_open_mean_ms"]` while its
  reference was still the pre-fix measurement; once the machine came free the
  reference was re-recorded with the corrected method (**16.9 ms -> 3.9 ms**)
  and the marker is gone. The mechanism stays, and a test still asserts that any
  metric marked provisional exists in the baseline and explains itself.

### Phase 037 - Distribution: portable mode and a single executable
- **Portable mode** (`portable status|on|off`): index, configuration, logs and
  every coordination file live in a `UniversalSearch-data` folder next to the
  executable, and **nothing at all is written under `%LOCALAPPDATA%`**. For the
  three cases where that is the only sensible layout: a USB stick that has to
  carry the index, a corporate machine that forbids writing outside a share,
  and an environment where `%LOCALAPPDATA%` is managed by someone else.
  Switched on by the `portable.txt` marker beside the executable or
  `UNIVERSAL_SEARCH_PORTABLE=1`; `UNIVERSAL_SEARCH_HOME` always wins, which is
  how the background worker passes its resolved home to its child process.
- Two things portable mode deliberately does **not** do: it does not move an
  existing index (that is a decision about hours of work, so the command
  writes a marker and says where to look), and it never falls back. If the
  folder cannot be written it says so and stops, because an index in a place
  the user does not know about is worse than one that refuses to start. Every
  writer goes through `AppPaths.ensure()`, so there is one place where it can
  refuse.
- **A real single-file build.** `packaging/universal-search-onefile.spec`
  produces one `.exe` with no `_internal/` folder, for the copies that get
  copied somewhere. The two-dir build remains the installed copy: it starts
  fast and carries both the windowed and the console executable.
- **`packaging/build.ps1`** builds both and then runs what it built — for the
  single-file build, copied to an empty folder first. The 2.0.0 release
  published two bare `.exe` files that **did not start** (`PYI-8: Failed to
  load Python DLL`) because a one-dir build needs its sibling `_internal/`.
  The cause was procedural — a sequence of steps with nothing checking the
  result — so the check is now part of the build and of CI.
- The single-file build is a **console** build, deliberately. The first version
  was windowed and the smoke caught what that costs: `--version` and the exit
  codes worked, but `search` printed nothing at all, because a windowed
  PyInstaller build has no stdout. The cost paid instead is a console window
  behind the search window on double-click.
- **Bug found by this phase's own gate, in phase 031.** The fuzzy layer was
  reachable but empty on a freshly built index: nothing ever marked it stale,
  so `transisto` retrieved nothing until somebody ran the rebuild by hand.
  The indexer now marks the blocking fingerprints dirty after a pass that
  changed documents, next to the line that already did it for the semantic
  layer. Phase 031's own tests missed it because they call `rebuild()`
  explicitly — a test that sets the scenario up cannot find a scenario nobody
  set up.
- The gate's first version failed for the wrong reason (it compared against a
  directory the executable was not in). The fix was to run the gate from the
  folder it is pretending to be the copy in, rather than add a production
  switch that exists only to make a test comfortable.
- The gate then leaked its own simulation: it left `LOCALAPPDATA` pointing into
  its temporary workspace and stayed inside the fake stick folder, so three
  tests in other modules failed with an index that had appeared next to the
  repository. The same failure the phase exists to prevent, aimed at the suite.
  Both the working directory and the environment are now restored by a context
  manager, including when a measurement raises, and two tests fix that. Three
  of the new tests had the same leak in a smaller form, writing `os.environ`
  instead of using `monkeypatch`.
- `diagnose self-test` gains a `deployment` area, and it is the whole report
  when a portable folder is unwritable: a self-test that crashes on the
  condition it exists to diagnose is the failure mode itself. `privacy show`
  now declares the deployment too.
- Evidence gate `python -m evaluation.distribution_gate`: **11/11 PASS**.
  `packaging/build.ps1` verified on Windows 11, Python 3.14.6: one-dir 4.1 MB
  per executable, single-file **15.1 MB**, both answering `--version`, index
  and search from a folder with nothing else in it.
- Full suite: **1 failed, 1127 passed, 3 skipped** (1131 collected). The failure
  is `test_gui_ux`'s Tk `pump` timeout and it **fails identically without this
  code**, checked by stashing the whole phase and re-running the suite; it
  passes in isolation (18/18). Recorded with the evidence rather than smoothed
  over.
- `tests/test_distribution.py`: 31 tests. `tests/test_observability.py`: +3.

### Phase 036 - Grouping, sorting and saved searches
- `--sort {relevance,name,modified,size}`, `--group
  {none,folder,type,source,date}`, and `--save` / `--use` / `--delete-saved`
  for saved searches. `query` is now an optional positional so `--use` can run
  on its own; when there is neither a query nor a saved search, the error lists
  the saved searches that do exist.
- The contract: organizing never changes retrieval, and it has one concrete
  consequence. A non-relevance order **widens the candidate pool** (5x, capped
  at 500), because sorting the 20 most relevant documents alphabetically is
  shuffling, not sorting. Without it `--sort modified` would silently return the
  20 most relevant documents in date order.
- Every sort ends with the path, so ties never depend on input order. Group
  order follows the key rather than the size, so a grouping is stable when one
  document changes. Documents with no date get their own bucket instead of
  being dropped or filed under a wrong year.
- `SearchResult` carries `modified_at` and `size`; the SQL already selected the
  date and now selects the size too.
- Saved searches are plain local configuration (`AppConfig.saved_searches`): no
  table, no migration, gone with the config file, and they hold no results or
  paths. They have a delete command, because a feature that only adds leaves
  clutter the user cannot remove. Anything typed on the command line overrides
  a saved search, so an explicit flag is never silently ignored.
- Evidence gate `python -m evaluation.organize_gate`: **9/9 PASS** (no document
  from outside the candidate pool, monotonic order, undated last, grouping
  conserves results, reproducible order, pool really widened 8 -> 40,
  round-trip and deletion, MRR unchanged at 0,8333).
- The gate's first version asserted something false: "organizing never changes
  which documents you get". It failed with 19 candidates and it was right --
  asking for alphabetical order and getting the 8 most relevant documents in
  alphabetical order is not ordering. The gate now asserts the property that
  actually matters (same pool, nothing from outside it, relevance order
  intact) and *publishes* the cost instead of hiding it: re-ordering changes the
  subset in 15 of 12 combinations, which is what was asked for.
- The gate's query was also too weak to be evidence: `informe` matches three
  documents in the corpus, so "the pool was widened" was technically true and
  evidentially worthless. It now uses a query with 19 candidates for an 8-item
  page.
- A pre-existing, load-dependent flake was confirmed while verifying this phase
  and is **not** a phase-036 regression: `test_gui_ux.py::
  test_keyboard_navigation_moves_and_opens` times out on `window.pump()` when
  many modules run together. Reproduced at commit `466dcbd` with none of the
  phase-036 code present, and bisected to a cumulative effect rather than one
  module (the same 10-module set fails at HEAD). Added tests tip an already
  fragile cross-module interaction over its threshold; the mechanism is in the
  Tk fixtures, not in search. Not fixed here because it belongs to the GUI test
  infrastructure, not to organization.
- Known limit: grouping only prints on the command line; the GUI has no sort or
  grouping selector yet, which is phase 039's work.
- Phase 036 gate: 1097 collected, clean pyflakes, `python -m evaluation.gate`
  PASS (13/13). The full-suite run behind this entry was
  `2 failed, 1092 passed, 3 skipped`, and **both failures are
  pre-existing**, each reproduced at an earlier commit with none of the
  phase-036 code present: `test_background.py::
  test_worker_keeps_index_current_and_stops_cleanly` (a timing-sensitive
  worker test, reproduced at `7bf9122` and `466dcbd`) and `test_gui_ux.py::
  test_keyboard_navigation_moves_and_opens` (bisected above). Recorded
  rather than smoothed over.

### Phase 035 - Batch operations over the selection
- The result list is now multi-selectable (Ctrl+click adds, Shift+click extends)
  and a new **Selección** menu acts on the whole selection: open (Ctrl+O),
  reveal (Ctrl+R), copy paths (Ctrl+C) and forget from the index
  (Ctrl+Shift+R).
- The logic lives in `universal_search/gui/batch.py`, free of Tk and of the
  clipboard, so the rules are testable without a display and any front end can
  reuse them.
- The contract is that **a batch never claims more than it did**: a hard cap of
  50 per batch with what was left out counted and reported, per-item failure
  isolation so one missing path does not cost the other 49, and exact counts
  where only the list of reasons is abbreviated.
- Return, Ctrl+Return and double-click all go through one code path for one
  selection and for many. A separate "open the first one" branch is exactly
  where the two behaviours drift apart and a single click ends up meaning
  something different from a single selection.
- The usage signal and the recent-query record are only written for a
  **single** successful open: a batch does not train the ranking.
- Evidence gate `python -m evaluation.batch_gate`: **9/9 PASS**.
- The gate found a real defect in the module written to prevent this class of
  lie: an unconfirmed forget reported `4 sin procesar por el limite de 50`,
  blaming the size limit for something that had not happened because nothing was
  confirmed. `BatchReport` now carries a `skip_reason`, and T9 checks that an
  unconfirmed forget says so and never mentions the limit.
- T8 failed first for the wrong reason and was fixed for the right one: it
  searched the file for the word "clipboard" and matched a docstring saying the
  module is *free* of it. It now checks imports, not words.
- Known limits: no select-all and no keyboard range selection (Tk's Listbox
  offers neither, and a custom selection model is out of scope); no undo for
  forget; the cap is not user-adjustable, on purpose.
- Phase 035 gate: **1060 passed, 3 skipped** (1063 collected), clean pyflakes,
  `python -m evaluation.gate` PASS (13/13).

### Phase 034 - Content inside ZIP archives
- `.zip` is now searchable, reusing the phase-025 `member_problem()` and
  `read_member_bounded()` rather than writing a second, weaker set of rules.
  Members are read in memory (nothing is written to disk) and only when their
  suffix is a registered text format, so a `.exe` or `.png` inside an archive is
  never interpreted as text.
- **No recursion** into nested archives, with a warning: without a depth limit
  they are an unbounded expansion path and the obvious thing to abuse.
- Each member is labelled (`=== name (size) ===`) so a hit on the text can be
  traced to a member, and member names are indexed as text so a file inside the
  archive is findable by its own name.
- Evidence gate `python -m evaluation.archive_gate`: **9/9 PASS**. It plants the
  attack in the *same* archive as the good content — three searchable members,
  an `.exe`, two traversal names (`../../windows/…` and `/raiz/…`), a nested zip
  and a member expanding 407x — so the refusal rules cannot pass by suppressing
  useful content (archive recall 4/4 while zero hostile tokens are indexed, and
  the good members survive as `PARTIAL`, never lost).
- Deliberate limitation: an archive is **one** document, not one per member. A
  hit is attributed to the `.zip`, so the result list cannot say which member
  matched. Enumerating members as virtual documents needs a provider layer, a
  content path that understands virtual paths, and an open-result action that can
  materialize a member; a half-built version of that would leave opening a result
  broken, which is worse than a documented limit.
- T6 failed on its first run with four "lost" documents, and it was my mistake
  for the second time in two phases: it measured "before" on an index that
  already contained the archive, counting queries that were never found as lost.
  Both gates now index the corpus alone, measure, and only then add the archive.
  Corrected, T6 is 0 lost and MRR does not move at all (0,8333 -> 0,8333).
- One test of mine failed because the code was right: `"a" * 5000` compresses at
  a 250x ratio, so the expansion check refused it. The content was changed to
  realistic prose and the failure recorded, because that check is the thing
  standing between a hostile archive and the materialization of its payload.
- Phase 034 gate: **1038 passed, 3 skipped** (1041 collected), clean pyflakes,
  `python -m evaluation.gate` PASS (13/13).
- A documentation update of mine silently did nothing: the ROADMAP line for 033
  reads `Email as a source (033)` and I had been replacing `Mail as a source
  (033)`, so phase 033 was left open in the roadmap while marked complete. The
  gate caught it through invariant 13 (`roadmap is honest`: no open phase may sit
  below a completed one), which is exactly what that invariant exists for. Every
  roadmap edit from here asserts the line it replaces is actually there.

### Phase 033 - Mail as a source
- `.eml`, `.mbox`, `.mbx` and `.email` are now searchable, through the stdlib
  `email` parser only: no new runtime dependency (`pypdf` and `watchdog` remain
  the only two).
- Mail is the first source where the searched text is not the whole file, so the
  extractor composes it deliberately: participants, date, subject, then body.
  HTML bodies are flattened to text with block tags acting as word breaks, so
  `celda1celda2` never becomes one unsearchable word.
- **Attachments are never read.** Their bytes are never materialized and their
  content never enters the index; their count is reported as a warning, so
  "this message had 3 attachments and none are searchable" is visible rather
  than implied. `.msg` (Microsoft OLE) is out of scope and is never opened.
- Headers are treated as untrusted input (sanitized, length-bounded), and one
  unreadable body part costs a warning and a `PARTIAL` status rather than the
  whole message.
- Mail folders need no new provider: a mailbox is a directory, and the existing
  filter `--type eml` applies. No source key was added, which would have meant
  touching the CLI, the GUI and the doc-type model for nothing.
- Evidence gate `python -m evaluation.mail_gate`: **7/7 PASS** (mail recall
  7/7, zero attachment hits in results *and* zero in stored text, zero `<script>`
  text, no labelled document lost from the top-5, 3,8 ms per message, zero new
  dependencies).
- Three real defects the gate found and the tests had not: an unreadable body
  part did not mark the result `PARTIAL`; an mbox truncated at the message limit
  did not mark the result truncated (silently dropping half a mailbox is the
  worst outcome here); and the mbox envelope sender was read as the *second*
  field of the `From` line, returning the day of the week.
- T4 changed meaning, not threshold. It first asserted a 0,05 MRR drop, a
  number invented before measuring, and failed at 0,0556: a file named
  `presupuesto.eml` ties with `presupuesto.md` and takes the top slot. Both are
  legitimate answers, so the gate now asserts the property a user actually has
  — no previously-found document leaves the top results (measured: 0 lost) —
  and still publishes the MRR change (0,8333 -> 0,7778) instead of hiding it.
- Known limitation: an `.mbox` is **one** document, not one per message, which
  is the most important limitation of this phase.
- Phase 033 gate: **1016 passed, 3 skipped** (1019 collected), clean pyflakes,
  `python -m evaluation.gate` PASS (13/13).
- Known flaky test, pre-existing and unrelated: `test_tray.py::
  test_process_death_releases_the_tray_process_lock` fails intermittently on
  this machine. Verified at commit `7bf9122` with none of the phase-033 code
  present (1 failure in 6 runs), so it is a race in the Win32 byte-range lock,
  not a regression. It is not fixed here because it belongs to the process
  model, not to mail.
- The full-suite run behind this entry was `3 failed, 1013 passed, 3 skipped`
  (1019 collected), not clean: `test_background.py::
  test_worker_keeps_index_current_and_stops_cleanly`,
  `test_background.py::test_start_stop_and_no_duplicate_process` and
  `test_reliability.py::test_a_killed_worker_leaves_a_stale_lock_that_a_new_one_recovers`
  all failed while another pytest suite was running concurrently on this
  machine. The first was reproduced at `7bf9122` with none of the phase-033
  code present, so these are timing-sensitive process tests under load and not
  phase-033 regressions. Recorded here rather than smoothed over.

### Phase 032 - Query suggestions
- When a search returns nothing, Universal Search now proposes corrections,
  and the rule is the whole feature: **a suggestion is a query that was
  actually run and actually returned a document.** The candidate words come
  only from the user's own index, read through `fts5vocab` (a view over the
  existing FTS index, created and dropped on the spot, so nothing is stored).
  There is no dictionary, no spell-checking service, no network and no list of
  common typos.
- Corrections reuse the bounded Damerau-Levenshtein budget of phase 031 and are
  ordered by edit distance, then by how often the word appears in the index.
- A rule the evidence gate added: a token that is neither indexed nor
  correctable is not a misspelling of anything, so no advice is offered. The
  first run failed this gate (T3) by suggesting something for "zzz no existe".
- `search --no-suggest` opts out, symmetric with `--no-fuzzy` and
  `--no-semantic`.
- Evidence gate `python -m evaluation.suggest_gate`: **6/6 PASS** (recall of
  corrections 1,00, zero unverified suggestions, zero advice where there is
  nothing to correct, no lexical metric moved, no persistent state left).
- The gate found two of my own mistakes and both are written down rather than
  quietly fixed: the vocabulary cache keyed on the database file's mtime never
  hit, because WAL checkpointing rewrites that file during ordinary operation;
  it is now keyed on (document count, latest indexed_at), which changes
  exactly when the vocabulary can change. And the gate itself built a fresh
  suggester per sample, so it reported the cold cost dressed as the warm one —
  it now measures and prints both (+7,19 ms warm, +23,33 ms cold).
- Phase 032 gate: **993 passed, 3 skipped** (996 collected), clean pyflakes,
  `python -m evaluation.gate` PASS (13/13).

### Phase 031 - Robust search: typos and partial words
- Typo and partial-word queries now resolve. `transisto`, `transsistor`,
  `transistorr`, `transltor`, `polarisacion` and two-word queries with one
  misspelled word retrieve nothing on the lexical engine today and the right
  document after this phase.
- The design is blocking plus verification, and the separation is the whole
  point: a bounded trigram fingerprint (at most 64 trigrams per document,
  from its 32 most distinctive words, round-robin, keyed on a small integer
  surrogate) only *proposes* candidates, and every match is decided against
  the document's real text by substring or bounded Damerau-Levenshtein. A
  document the filter loves and the verifier rejects is never returned.
- A second FTS5 `trigram` table was rejected on measured storage cost, not on
  availability: SQLite 3.50.4 does support it, and it emits one term per
  character position, which is 2 million rows for a 2 MB document.
- The layer is fallback-only and filter-disabling, like the semantic layer: any
  non-empty lexical result is returned untouched, `source`/`type` switches it
  off, `--no-fuzzy` opts out, and `explain` reports the token, the rule and the
  distance.
- Schema 9 -> 10. The new tables are derived, versioned, rebuildable lazily
  and removable; `privacy forget` deletes a document's fingerprints and the
  privacy inventory declares all three tables.
- Fixed a real gap found by inspection: `Indexer._delete` removed the document,
  its FTS rows and the graph, but left the phase-026 semantic vectors behind,
  so a document deleted from disk survived as an orphan until someone ran
  `diagnose recover orphan-derived`. Both optional derived tables are now
  scrubbed at delete time, with tests.
- Evidence gate `python -m evaluation.fuzzy_gate`: **6/6 PASS**. Recall@5 of
  0,90 on the typo/prefix set, zero leaks on the must-retrieve-nothing set, no
  change to any lexical metric, 5,8 % extra index size and +5,13 ms of added
  p95 latency. The gate failed twice first, on real defects: a global overlap
  threshold that discarded candidates the verifier had already accepted
  (T1 0,40 -> 0,90 once blocking became per token), 64-character hash keys in
  every posting (T4 34,7 % -> 5,8 % with an integer surrogate), and four
  sources of waste worth 36 ms (one connection per operation, unbounded content
  fetches, per-token accent folding, and a distance computation per word). The
  gate's own latency measurement was also wrong - it took the p95 of paired
  differences, which on a loaded machine measures noise - and now reports each
  engine's p95 separately plus the machine's CPU load.
- Documented out of scope with reasons rather than hidden: typos three edits
  away, words that appear only in a path, and mid-word transpositions in words
  of seven characters or fewer.
- Phase 031 gate: **976 passed, 3 skipped** (979 collected), clean pyflakes,
  `python -m evaluation.gate` PASS (13/13).

## [2.0.0] - 2026-09-30

Major release covering phases 011-030. Version 1.0.0 shipped phases 001-010 on
a schema-3 index with a single-purpose CLI; this release moves the index to
schema 9, adds a background worker with its own lifecycle, a notification-area
controller, a control centre, a document graph, an optional local semantic
fallback, a Windows shell integration, observability with named recovery
cases, and a verified CI plus an executable v2 quality gate.

Upgrade: installing 2.0.0 over 1.0.0 migrates the existing index in place
(schema 3 to 9, additive migrations only). Downgrades are refused on purpose:
an older build will not touch a newer index.

### Phase 030 - Universal Search v2 quality gate
- Added `evaluation/gate.py`, runnable as `python -m evaluation.gate`: thirteen
  local invariants that answer one question with evidence. Dependency budget
  (exactly `pypdf` and `watchdog` at runtime), no socket/HTTP/model import in
  the shipped package, a platform-independent data path, every Windows
  touchpoint declared with a reason, a complete privacy inventory for all 12
  tables, single-sourced versioning, no stray `breakpoint`/`pdb`, every phase
  documented, changelog coverage, documented counts equal to what pytest
  collects, and a closed roadmap.
- One of those checks is behavioural rather than textual: it indexes a real
  tree, runs all four recovery cases plus `privacy forget` and two destructive
  diagnostic repairs, and verifies every user file is byte-identical
  afterwards. A regex cannot tell an application-data `unlink` from a user's
  document; this can.
- `tests/test_v2_gate.py` gates the gate, including negative tests that break
  a temporary tree and assert each check can still say no.
- Found and fixed while writing it: `recovery.py` and `observability.py` used
  `with connection`, which commits but never closes; on Windows the database
  stayed locked after a repair. Both now close deterministically, and dead
  `winreg` code in `background.py` is gone.
- Phase 030 gate: **916 passed, 3 skipped**, clean pyflakes, `python -m
  evaluation.gate` → `VERDICT: PASS` (13/13).

### Phase 029 - Release engineering and CI
- Made the release gates a verified contract: `tests/test_ci_gates.py` fails
  when a gating job tolerates failure, when a mandatory gate disappears from
  the workflow, when the workflow is not read-only/serialized, or when a third
  runtime dependency appears. Runtime dependencies stay exactly `pypdf` and
  `watchdog`; the `build` extra is optional and disjoint.
- Strengthened the packaged gate: the smoke now checks **both** executables,
  runs `diagnose self-test`, `diagnose export` and
  `diagnose recover orphan-derived`, asserts that a destructive recovery is
  refused without `--yes`, and starts the windowed GUI to catch a build that
  dies on import. Hashes and the sanitized support bundle upload with
  `if: always()`.
- Added named per-phase steps to the `quality` job so a regression names
  itself instead of hiding inside a single large run.
- **Fixed two real semantic-layer defects found by that smoke** (test-first,
  `NGRAM_VERSION` 1 -> 2 so incompatible vectors are rebuilt):
  - smoothed idf (`log((n+1)/(df+1)) + 1`). The previous `log(n/df)` is
    exactly zero for every n-gram present in all documents, which silently
    disabled the whole layer on a one-document index.
  - the precision gate now accepts a morphological variant (`receta` /
    `recetas`) when both words are at least five characters, instead of
    requiring exact token equality. Short fragments still count for nothing,
    so the "must retrieve nothing" contract is unchanged.
  - Re-measured on the labelled corpus: hybrid failure R@5 0.762, exact-match
    correctness 1.0, zero top-1 regressions, both nonsense queries still
    empty — no metric moved. `evaluation/semantic_baseline.json` now records
    the model version, the idf formula and the gate rule, and a test fails if
    the committed record describes a model that is no longer shipped.
- Phase 029 quality gate: **891 passed, 3 skipped**, clean pyflakes, and a
  green packaged smoke over the real `.exe` files.

### Phase 028 — Local observability and recovery
- Added `EventRecorder`: bounded JSON Lines with a fixed schema
  (`at`, `component`, `event_id`, `severity`), size-based rotation (1 MB x 3)
  and field-name redaction for credentials, content and query fields. Every
  control-center action now leaves one such event; telemetry failures can never
  break an action.
- Added `universal-search diagnose self-test`, which exercises the database,
  FTS, schema, providers, extractors, worker and free disk space and returns
  the worst verdict (exit 0/1/2).
- Added `universal-search diagnose export --output PATH`, a sanitized support
  bundle that explicitly declares it contains no document content, no query
  text and no credentials.
- Added `universal-search diagnose recover CASE` with exactly four named cases
  (`orphan-derived`, `dirty-derived`, `stale-coordination`, `reset-derived`).
  A live worker owner is never disowned; destructive cases require `--yes`; no
  case can delete a user's source files.
- Exposed the self-test and the support bundle in the control center with
  typed `ActionResult`s (`data_scope="none"`), and declared `events.jsonl` in
  the privacy inventory and `docs/PRIVACY.md`.
- No new dependencies, no network, no telemetry leaving the machine.
- Phase 028 quality gate: **874 passed, 3 skipped**, clean pyflakes.

### Phase 027 — Windows shell integration
- Added `universal-search open PATH` and `universal-search reveal PATH`, both
  delegating to the platform adapter and failing with an actionable code.
- Added per-monitor DPI awareness (`SetProcessDpiAwarenessContext`, with the
  legacy `SetProcessDPIAware` fallback) before Tk creates a window.
- `install.ps1` now registers the reversible per-user Explorer verb and records
  it in the install manifest; `uninstall.ps1 -Remove` removes exactly that verb.
  `-NoExplorer` keeps test/silent installs free of HKCU writes.
- No administrator privileges, new dependencies or duplicated core logic.
- Phase 027 quality gate: **859 passed, 3 skipped**, clean pyflakes.

### Phase 026 — Evidence-first local semantic search
- Extended the fixed evaluation corpus (20 → 27 documents, 13 → 18 labelled
  queries) with the failure classes a semantic layer must fix: a BJT
  synonym that never says "BJT", a voltaje/tension synonym pair, an accented
  variant, a stopword-heavy paraphrase, a malformed binary `.md`, and two
  unrelated-domain distractors.
- Added a deterministic corpus hash, per-query latency, exact-match
  correctness and a failure inventory to the evaluation runner, and a
  `--semantic-baseline` record (`evaluation/semantic_baseline.json`).
- Measured the lexical baseline: MRR 0.833, exact-match correctness 1.0,
  with three total failures (synonym, paraphrase, morphological) and one
  partial (the BJT synonym document is unreachable).
- Shipped an optional, dependency-free, fallback-only local semantic layer
  (`src/universal_search/semantic/`): a versioned character 3-gram TF-IDF
  embedder, a versioned/rebuildable/removable vector index (schema v9) and
  a hybrid engine that consults the layer only when the lexical engine
  returns nothing. A shared-content-word precision gate rejects nonsense
  queries that a fixed cosine threshold cannot.
- The layer cleared a priori evidence gates (material gain, exact-match
  authority, no top-1 regression, no new dependency): hybrid MRR 0.833 →
  0.944, R@5 0.817 → 0.947, exact-match correctness 1.0. A bounded semantic
  boost on a non-empty pool was measured and rejected (it flipped the
  exact-token query "CMOS" and gained nothing on the failure subset).
- Exact filenames, phrases, filters and query operators stay authoritative;
  with the layer disabled or removed, search is exactly the lexical engine.
  No cloud, network, telemetry, external API or runtime dependency.
- Phase 026 quality gate: **852 passed, 3 skipped**, clean pyflakes; exact
  commands, measured behavior and limitations are in the phase report.

### Phase 025 — Content extraction v2
- Added a versioned extraction contract: `ExtractionResult` now carries
  `contract_version`, a machine-readable `status` (`ok`, `truncated`,
  `partial`, `no_content`, `error`, `cancelled`), sanitized `warnings`,
  a bounded `structure` summary (title, headings, sheets, slides), a
  `resource_usage` measurement and a `truncated` flag. `text`/`error`
  keep their meaning, so older construction sites are unaffected.
- Added `ExtractionLimits` (input bytes, characters, pages, sheets, slides,
  time, ZIP members, per-part bytes, decompression expansion) with one
  shared enforcement module (`extractors/base.py`): character-budget
  accumulation, chunked bounded ZIP reads, header-level rejection of
  traversal names / oversized parts / expansion ratios, DTD entity
  rejection, and sanitized bounded warnings on every result.
- Hardened PDF extraction: input limit before opening, encrypted files
  rejected (owner-only readable with a warning), page/character/time
  limits with visible truncation, per-page error isolation, empty text
  layers reported as `no_content` instead of empty success, Info title
  preserved, cooperative cancellation between pages.
- Hardened Office extraction: member-count cap, per-member vetting before
  reading, malformed optional parts (shared strings, workbook, single
  sheets/slides) warn and continue instead of failing the whole document,
  empty text layers are `no_content`, docx headings/title, xlsx sheet names
  and pptx slide text preserved as bounded structure, sheet/slide limits
  truncate visibly.
- Preserved text-extractor behavior (UTF-8-sig, replacement, NUL/BOM strip,
  2 M-char cut) — the cut is now flagged instead of silent — and registry
  compatibility: pre-contract single-argument extractors keep working.
- Persisted extraction status/warnings/truncation/contract in the derived
  `document_intelligence` table (schema v8, idempotent migrations): an
  intelligence rebuild preserves the columns and `privacy forget` deletes
  the row, so diagnostics are explainable without outliving the document.
  Diagnostics-only rows (`version = 0`) are not counted as stale analyses
  and do not make `analysis_for`/`related` claim an analysis exists.
- The indexer forwards the cooperative cancel to the content reader and
  never lets one document stop a pass; cloud-only placeholders no longer
  reference an unassigned outcome.
- Phase 025 quality gate: **813 passed, 3 skipped**, clean pyflakes; exact
  commands, measured behavior and limitations are in the phase report.

### Phase 024 — Provider expansion
- Formalized the provider contract: streaming `iter_files` with bounded
  error reporting (`MAX_PROVIDER_ERRORS`), a cooperative `CancelToken`, a
  nine-member capability vocabulary (adding `errors`, `watch`, `streaming`)
  and interface-version negotiation in `ProviderRegistry.register`.
- Made the provider key the canonical source discriminator: the uniqueness
  contract is now `(source, path)` via a safe additive schema migration
  (v6 → v7, lossless table rebuild), so two providers may own the same path
  without changing search or ranking semantics. `SourceKind` gains `network`
  and `removable`; the query language, CLI and GUI source filters accept
  them.
- Added `NetworkProvider` (NAS/share) and `RemovableProvider` (USB/SD) as
  mounted-path providers only — standard-library filesystem access,
  configured roots with containment validation, disconnected states
  reported as errors, no network client or credential handling.
- Integrated mixed-provider indexing: `index_root(..., provider=)` and
  `index_sources(...)` with per-provider failure isolation; a provider that
  dies mid-enumeration costs only its own pass and never triggers the
  deletion pass; deletion is scoped to the provider's own rows.
- Phase 024 quality gate: **765 passed, 3 skipped**, clean pyflakes; exact
  commands, measured behavior and limitations are in the phase report.

### Phase 023 — Indexing UX & control center
- Added a separate, asynchronous control-center window for configured sources,
  provider availability, counts and supported types, scan/pending state,
  failures and inaccessible roots, exclusions, health, derived-data state and
  measured storage.
- Added typed `ControlCenterService` actions for source add/remove, safe rescan,
  retry, pause/resume and FTS/derived/relationship/full rebuilds. Mutating
  actions fail closed as busy when another action is already running.
- Made the safety boundary explicit in every result: configuration,
  indexed records, derived data and physical files are separate scopes.
  Ordinary source removal never unlinks a user file; indexed-row removal is
  opt-in, and FTS/relationship/full rebuilds require confirmation.
- Added `control-center.json` operational state and privacy inventory coverage;
  it contains bounded counters and sanitized failure text only.
- Phase 023 quality gate: **695 passed, 2 skipped**, clean pyflakes; exact
  commands, measured behavior and limitations are in the phase report.

### Phase 022 — Related-document graph
- Added a versioned local graph of document nodes, bounded term postings and
  explainable relationship edges. Candidate generation uses inverted terms,
  phrases, safe references and directory hints with explicit per-document,
  minimum-similarity and stored-edge caps; it never performs an unbounded
  all-pairs scan.
- Added deterministic full rebuild, graph/preprocessing-version invalidation,
  incremental update refresh and deletion cleanup. The graph is derived beside
  document intelligence and is never read by `SearchEngine` or query ranking.
- Added a service-layer related list and a small GUI evidence window; the
  existing intelligence CLI remains compatible and can remove all graph data.
- Graph persistence was added to the privacy inventory. No cloud service,
  model, network client or new runtime dependency was introduced.
- Fix round: capped posting storage/retrieval and counters, durable dirty
  markers with recoverable lookup repair, FTS content-hash checks, bounded
  incremental maintenance, symmetric explicit references, and worker/queue
  GUI lookup.
- Fix round 2: bounded alias/mention/declared candidate aggregation,
  transactional FTS hash/dirty repair, and direct/reverse reference metadata
  scrubbing on privacy forget and canonical deletion.
- Fix round 3: cursor-paged, batch-capped FTS orphan cleanup with alias-aware
  reverse-reference and dirty-marker scrubbing in one transaction.
- Phase 022 quality gate: **681 passed, 2 skipped**, clean pyflakes; measured
  bounds and performance are recorded in the phase report.

### Phase 021 — Tray & background experience
- Added the optional `universal-search tray` command and a native Windows
  notification-area adapter implemented with standard-library `ctypes`; no new
  runtime dependency, Windows service, cloud component or automatic tray
  launch.
- Added an application-level background state service with `stopped`,
  `starting`, `indexing`, `paused`, `idle`, `error` and `stopping`. The tray
  reads that snapshot instead of SQLite internals.
- Tray menu: present Search / Quick Search / Settings, open the existing
  Diagnostics view, and start, stop, pause or resume the indexer according to
  its current state.
- Independent worker, GUI and tray PID identities. Worker startup now uses a
  unique generation passed to the child plus an OS-held lease; the tray claims
  only the exact generation it presented. Stop markers, cleanup and forced
  termination are owner-scoped, so a replacement or reused PID is never hit.
- Meaningful-only balloons for new errors or error text, new hotkey
  configuration problems, unexpected worker disappearance and long-pass
  completion. User-requested lifecycle actions stay silent; startup evaluation
  waits until the native icon is ready.
- The notification-area adapter restores its icon on Windows `TaskbarCreated`
  after Explorer restarts. Non-Exit command failures are bounded, retained as
  the controller's last result and logged without exception/document text.
- Autostart is unchanged: it still registers `indexer run`, not the tray.
- Current quality gate: **646 passed, 2 skipped**, clean
  pyflakes, successful PyInstaller build and frozen-package smoke. The opt-in
  native smoke posted and deleted a real notification-area icon in the final-fix
  verification; it is skipped by the normal suite.

### Phase 020 — Production release & reliability
- Schema version 5: `schema_migrations` ledger (one row per applied
  version, append-only) and **downgrade refusal** — a build older than the
  index refuses to open it instead of silently rewriting the stamp.
- `SearchDatabase.backup()`: consistent copy via SQLite's own backup API
  (safe while the worker writes), and `diagnose repair all --backup` takes
  one before dropping the index.
- Fixed: `background.start(paths)` passed only the working directory to the
  spawned worker, so a worker started with custom paths published its lock
  and status in the *default* user home. The child now inherits
  `UNIVERSAL_SEARCH_HOME`. Found by the phase-020 crash-recovery test.
- `background.start()` waits 10 s (was 5 s) for the worker handshake: on a
  loaded machine a correct worker was reported as failed.
- `diagnose summary` shows the migration ledger.
- CI: Windows quality + build/smoke gates, plus a non-gating Ubuntu probe
  for the platform-independent core.
- `docs/RELEASE.md` (reproducible checklist and update strategy) and this
  changelog.

### Phase 019 — Provider & extension architecture
- Explicit `DocumentProvider` contract: `key`, `version`, `capabilities`,
  `available()`; six named capabilities and an interface version.
- `ProviderRegistry`: capability negotiation, duplicate refusal, and
  failure isolation (a provider that cannot answer is reported
  unavailable, not propagated).
- `local` and `onedrive` registered with the capabilities they actually
  have — OneDrive deliberately does not claim `content`.
- Extractors expose `infos()`; `universal-search extensions` prints the
  whole registry.
- **No runtime third-party plugins**, documented with reasons in
  `docs/EXTENDING.md`.

### Phase 018 — Privacy & security hardening
- `docs/PRIVACY.md`: prioritised threat model, explicit assumptions and a
  full data inventory; the inventory lives in code
  (`universal_search/privacy.py::INVENTORY`) so it cannot quietly drift.
- `universal-search privacy show | forget`: `forget` removes a document
  and everything derived from it (FTS row, intelligence, usage signals)
  without touching the file.
- **Fixed an availability bug**: FTS5's `snippet()` walks every phrase
  instance, so one document repeating a term 10 000 times took 1.9 s per
  search (>150 s at 2 MB). Snippets are now produced by the linear
  `build_snippet()`; measured cost is now independent of term frequency.
- Log messages capped at 500 characters by a handler filter; status writes
  retry the atomic replace on Windows.
- 24 security regressions, including one that fails if anyone imports
  `socket`/`http`/`urllib`/`requests`/`ftplib`/`smtplib`.

### Phase 017 — UX & accessibility
- Search runs on a worker thread; the Tk main loop drains results through
  a queue, with a generation number so a superseded keystroke can never
  overwrite a newer answer.
- Explicit states: loading (previous results stay visible), no results,
  query error with the reason, and failure.
- Central theme (`gui/theme.py`, light and dark) and user UI scale;
  no colour literals left in widget code.
- Result rows: name, type, last two folders, snippet — no absolute path,
  no "(local)" noise.
- Manual Windows UX/accessibility checklist.

### Phase 016 — Windows integration
- `universal_search.platforms`: every OS touchpoint behind an adapter
  (`WindowsPlatform`, `NullPlatform`) with injectable `startfile`, `popen`,
  `winreg` and `user32`.
- Single-instance window: a second launch presents the running one.
- A hotkey that could not be registered is reported in the worker status.
- Per-user, reversible Start Menu and Explorer integration scripts.
- Ctrl+Space is deliberately **not** the default hotkey: it toggles the
  IME on a large share of Windows installations.

### Phase 015 — Index management & diagnostics
- `universal-search diagnose summary | health | repair …`: twelve health
  checks (`ok`/`warning`/`fatal`) and five repair operations, with the
  destructive ones requiring `confirm=True` in code and `--yes` in the CLI.
- GUI "Diagnóstico" menu with a read-only report and a confirmed full
  rebuild.
- Fixed: `with connection` in sqlite3 commits but does not close, so a full
  rebuild failed on Windows; and `check()` on a missing database used to
  create it.

### Phase 014 — Local document intelligence
- Deterministic, local, rebuildable analysis: language (function-word
  profiles), title/headings/sections, a bounded 24-term vector, 16
  co-occurrence pairs, and related documents by cosine similarity.
- Versioned derived table, incremental rebuild, `intelligence clear`;
  search never reads it and similarity never touches the ranking formula.

### Phase 013 — Search quality & ranking v2
- Labelled evaluation corpus with Precision@K, Recall@K, MRR and a
  committed baseline (`evaluation/baseline.json`).
- Weight headroom measured instead of asserted: a path-only document
  needs `path_match` 7.2× higher to displace a content match; no weight
  was changed because no change was justified.

### Phase 012 — Advanced search language
- Query language in four stages (lexer, parser, validation, translation)
  with phrases, `AND`/`OR`, negation, and `name:`/`path:`/`type:`/
  `source:`/`after:`/`before:`/`size:` filters, shared by CLI and GUI.

### Phase 011 — Performance & scalability
- Reproducible benchmark suite, bounded local metrics, rotating logs and
  database maintenance.

## [1.0.0] — 2026-09 (phases 001–010)
Foundation, incremental indexing, document extractors, ranking engine,
Windows desktop GUI, background indexer, OneDrive providers, personal
context with local usage learning, global search with hotkey and filters,
and the Windows installer.
