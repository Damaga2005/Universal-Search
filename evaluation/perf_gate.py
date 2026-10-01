"""Phase 038: a performance gate that can say "this measurement means nothing".

Run from the repository root::

    python -m evaluation.perf_gate                # measure, compare, decide
    python -m evaluation.perf_gate --profile 10000
    python -m evaluation.perf_gate --record       # accept a new baseline
    python -m evaluation.perf_gate --quiet        # machine busy: explain and stop

Exit codes: 0 PASS, 1 FAIL, 2 INCONCLUSIVE.

Why this phase exists
---------------------
The 2.0.0 audit could not answer a simple question: *did latency regress?* The
same code measured 4.86 s and 7.83 s on the same machine, minutes apart, with a
client game loading it to 94% CPU. The honest conclusion was "cannot say", and
the number was published anyway with a footnote.

Two measurements from this project show why that footnote is not enough:

* Phase 031's gate failed at **18.70 ms** with the CPU at 94% and passed at
  **7.50 ms** with the machine idle. Same code, same thresholds, opposite
  verdict. The machine's idle latency was the product's real cost; the loaded
  one measured the competition for the CPU.
* Phase 037's own gate leaked `LOCALAPPDATA` and made three unrelated tests in
  other modules fail with an index that had "appeared" in the repository. A
  gate that cannot be trusted when the machine is quiet cannot be trusted at
  all.

So the rules this instrument follows are not about being faster. They are
about **what a number is allowed to claim**:

1. **Load is measured before the number, and it can veto it.** A calibration
   workload that is pure arithmetic and touches nothing else is timed first;
   if it is slower than this machine's own recorded best, the machine is not
   idle and the verdict is INCONCLUSIVE, not PASS or FAIL.
2. **Every timing is the best of N, never the mean.** Interference only ever
   *adds* time, so the minimum is the cleanest available estimate of the real
   cost. A mean reports the neighbours' workload as if it were ours.
3. **The measurement must repeat before it may conclude.** The suite runs
   twice and the spread between the two runs is itself a gate. Two runs of the
   same code that disagree by more than the tolerance mean the tolerance is
   meaningless, and reporting PASS against it would be self-deception.
4. **A busy machine cannot rewrite the baseline.** Refreshing the reference
   numbers while someone else's game is running would make every later
   comparison a comparison against noise.
5. **The machine is named.** The committed baseline belongs to one CPU, one
   Python and one operating system. On a different machine the same numbers are
   indicative, not comparable, and the gate says so instead of implying a
   verdict it cannot support.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

BASELINE_PATH = ROOT / "evaluation" / "perf_baseline.json"

# Exit codes. INCONCLUSIVE is separate on purpose: a report that cannot
# distinguish "fine", "broken" and "I do not know" has two answers for three
# situations, and CI cannot gate on it.
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INCONCLUSIVE = 2


# -- the machine-load calibration ---------------------------------------------

# A fixed amount of pure integer arithmetic. No allocation, no I/O, no imports:
# anything that could be scheduled away would make it measure the scheduler
# instead of the CPU. The result is returned so the loop cannot be elided.
_CALIBRATION_ROUNDS = 300_000
_CALIBRATION_REPEATS = 3

# How much slower than its own recorded best the calibration may run before the
# machine is declared busy. 1.35 is deliberately generous: this vetoes only
# conditions under which a latency number is meaningless, and a tight bound
# would produce INCONCLUSIVE reports on a laptop on a desk, which teaches
# everyone to ignore the instrument.
LOAD_VETO_RATIO = 1.35

# The same veto from the other side: if the OS says the machine is busy *before
# anything of ours has started*, then it is not our process that is slow, and a
# latency number taken now would describe somebody else's software. Sampled
# before the benchmark starts for exactly that reason.
OS_LOAD_VETO_PERCENT = 60

# The OS figure is sampled more than once and the lowest is kept. A single
# sample taken right after this gate's own previous run catches that run's tail
# and would veto the machine against itself.
OS_LOAD_SAMPLES = 3
OS_LOAD_GAP_S = 0.3

# Search repetitions per query. p95 of 60 samples sits on the 57th ordered
# value and is not repeatable; 160 puts it on the 152nd, which is.
SEARCH_REPETITIONS = 40


def _calibration_workload(rounds: int = _CALIBRATION_ROUNDS) -> int:
    """Deterministic pure-CPU work: xorshift64 plus an accumulator."""
    value = 0x9E3779B97F4A7C15
    mask = 0xFFFFFFFFFFFFFFFF
    for index in range(rounds):
        value ^= (value << 13) & mask
        value ^= value >> 7
        value ^= (value << 17) & mask
        value = (value + index) & mask
    return value


def measure_load() -> dict[str, float]:
    """Seconds the fixed workload takes on this machine, best of N.

    The minimum, for the same reason every other timing here is a minimum:
    contention can only slow this down.
    """
    samples: list[float] = []
    for _ in range(_CALIBRATION_REPEATS):
        started = time.perf_counter()
        _calibration_workload()
        samples.append(time.perf_counter() - started)
    return {
        "calibration_best_s": min(samples),
        "calibration_all_s": samples,
    }


def os_cpu_load() -> int | None:
    """CPU load as the OS reports it, sampled a few times, lowest kept.

    A second, independent signal. The calibration says whether *this process*
    was slowed; the OS says whether the machine as a whole was busy, which is
    the difference between "the disk was busy" and "the CPU was not ours".

    The lowest of several samples is kept, because the reading taken right
    after this gate's own previous run still carries that run's tail and would
    otherwise veto the machine against itself.
    """
    readings: list[int] = []
    for attempt in range(OS_LOAD_SAMPLES):
        value = _os_cpu_load_once()
        if value is not None:
            readings.append(value)
        if attempt + 1 < OS_LOAD_SAMPLES:
            time.sleep(OS_LOAD_GAP_S)
    return min(readings) if readings else None


def _os_cpu_load_once() -> int | None:
    if sys.platform == "win32":
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor).LoadPercentage"],
                capture_output=True, text=True, timeout=20,
            )
            text = completed.stdout.strip()
            return int(text) if text.isdigit() else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    try:  # pragma: no cover - non-POSIX
        return int(os.getloadavg()[0] * 100 / max(1, os.cpu_count() or 1))
    except (AttributeError, OSError):  # pragma: no cover - non-POSIX
        return None


# -- the metrics and their tolerances ----------------------------------------


@dataclass(frozen=True, slots=True)
class Metric:
    """One measured number, its committed reference and how it may move.

    Two tolerances, because a percentage alone is meaningless at either end. A
    25% regression on a 2 ms operation is half a millisecond and not worth
    failing a build over; a 25% regression on 6 s of indexing is real. So the
    allowed change is ``max(percentage, floor)``: the floor is the smallest
    change worth mentioning at all, and the percentage is what scales.
    """

    key: str
    label: str
    tolerance_pct: float
    tolerance_abs: float
    # "higher_is_worse" for time and size. A future metric where more is better
    # would need the opposite comparison, so it is explicit rather than assumed.
    direction: str = "higher_is_worse"

    def allowed_change(self, reference: float) -> float:
        return max(reference * self.tolerance_pct / 100.0, self.tolerance_abs)

    def compare(self, measured: float, reference: float) -> "Comparison":
        delta = measured - reference
        worse = delta if self.direction == "higher_is_worse" else -delta
        allowed = self.allowed_change(reference)
        # Improvement is never a failure; the gate exists to catch slowdowns.
        return Comparison(
            metric=self,
            measured=measured,
            reference=reference,
            delta=delta,
            allowed=allowed,
            ok=worse <= allowed,
        )


@dataclass(frozen=True, slots=True)
class Comparison:
    metric: Metric
    measured: float
    reference: float
    delta: float
    allowed: float
    ok: bool

    @property
    def percent(self) -> float:
        return (self.delta / self.reference * 100.0) if self.reference else 0.0

    def line(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        sign = "+" if self.delta >= 0 else ""
        return (
            f"{status}  {self.metric.label:<26} {self.measured:>9.3f}"
            f"  (ref {self.reference:>9.3f}, {sign}{self.delta:.3f}"
            f" = {sign}{self.percent:.1f}%, permitido {self.allowed:.3f})"
        )


METRICS: tuple[Metric, ...] = (
    Metric("initial_index_s", "indexado inicial", 15.0, 0.30),
    Metric("incremental_s", "pasada incremental", 25.0, 0.05),
    Metric("single_update_s", "actualización de 1", 25.0, 0.05),
    Metric("bulk_update_s", "actualización masiva", 20.0, 0.05),
    Metric("deletion_s", "reconciliación de borrados", 20.0, 0.05),
    Metric("db_open_mean_ms", "apertura de base (media)", 25.0, 1.0),
    Metric("search_p50_ms", "búsqueda p50", 20.0, 2.0),
    Metric("search_p95_ms", "búsqueda p95", 15.0, 3.0),
    Metric("index_size_mib", "tamaño del índice", 10.0, 0.10),
)


@dataclass
class Run:
    """One pass of the benchmark suite, reduced to the metrics we gate."""

    numbers: dict[str, float]
    load: dict[str, float] = field(default_factory=dict)

    def best(self, key: str) -> float:
        return self.numbers[key]


def _measure_once(profile: int) -> Run:
    """One full benchmark pass, run inside the suite's own temporary tree."""
    from benchmarks import corpus
    from universal_search.index.database import SearchDatabase
    from universal_search.index.indexer import Indexer
    from universal_search.index.search import SearchEngine

    load = measure_load()
    temp = Path(tempfile.mkdtemp(prefix=f"universal-search-perf-{profile}-"))
    try:
        tree = temp / "tree"
        files = corpus.build(tree, profile)
        database_path = temp / "index.db"
        database = SearchDatabase(database_path)
        indexer = Indexer(database)

        # Database open is measured *after* a warm-up, and this was not the
        # first version. The first number in a fresh process carries the
        # import of the database layer, the SQLite module load and the first
        # creation of the WAL files: 16.9 ms on the baseline run and 9.6 ms on
        # the next one, a 23% spread for code that never changed. The gate's
        # own repetition check caught it, which is what that check is for. What
        # the background worker pays on every start is the *steady* open, so
        # that is what is measured.
        for _ in range(3):
            connection = SearchDatabase(database_path).connect()
            connection.close()
        opens = []
        for _ in range(20):
            probe = SearchDatabase(database_path)
            started = time.perf_counter()
            connection = probe.connect()
            connection.close()
            opens.append((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        indexer.index_root(tree)
        initial_index_s = time.perf_counter() - started

        started = time.perf_counter()
        indexer.index_root(tree)
        incremental_s = time.perf_counter() - started

        corpus.modify(files[:1], 1)
        started = time.perf_counter()
        indexer.index_root(tree)
        single_update_s = time.perf_counter() - started

        bulk = max(1, profile // 100)
        corpus.modify(files[1 : 1 + bulk], bulk)
        started = time.perf_counter()
        indexer.index_root(tree)
        bulk_update_s = time.perf_counter() - started

        corpus.delete(files, bulk)
        started = time.perf_counter()
        indexer.index_root(tree)
        deletion_s = time.perf_counter() - started

        engine = SearchEngine(SearchDatabase(database_path))
        # Warm-up: the first query pays for page cache and imports, which is
        # real but is not what "search latency" means.
        for query in ("bjt", "mux cmos", '"ebers moll"', "zzznotfound"):
            engine.search(query, limit=20)

        # 40 repetitions per query, not 15. The spread gate caught this: with
        # 60 samples the p95 is the 57th ordered value, and two runs of the
        # *same* code disagreed by 11.4% on it on a machine at 15% CPU. That is
        # the instrument reporting that the metric was under-sampled, and the
        # honest response is to sample more rather than to widen the tolerance
        # until the noise fits. 160 samples puts p95 on the 152nd value.
        samples: list[float] = []
        for query in ("bjt", "mux cmos", '"ebers moll"', "zzznotfound"):
            for _ in range(SEARCH_REPETITIONS):
                started = time.perf_counter()
                engine.search(query, limit=20)
                samples.append((time.perf_counter() - started) * 1000)

        sizes = database.sizes()
        return Run(
            numbers={
                "initial_index_s": initial_index_s,
                "incremental_s": incremental_s,
                "single_update_s": single_update_s,
                "bulk_update_s": bulk_update_s,
                "deletion_s": deletion_s,
                "db_open_mean_ms": sum(opens) / len(opens),
                # Best of N, and the reason is stated in the module docstring.
                "search_p50_ms": _percentile(samples, 0.50),
                "search_p95_ms": _percentile(samples, 0.95),
                "index_size_mib": sizes["total"] / (1024 * 1024),
            },
            load=load,
        )
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))
    return ordered[index]


# -- the decision -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoadVerdict:
    conclusive: bool
    detail: str


def load_verdict(load: dict[str, float], reference: float | None,
                 os_load: int | None) -> LoadVerdict:
    """Can this machine produce a number worth comparing?

    Two independent signals, and the weaker one vetoes. The calibration asks
    whether this process was slowed down; the OS figure asks whether the machine
    as a whole was busy, which is the difference between a busy disk and a CPU
    that was not ours. Either one being bad is enough to withhold a verdict:
    the alternative is publishing a latency number that describes somebody
    else's software.

    The first version implemented only the calibration and mentioned the OS
    figure in the docstring without acting on it. The very first run then
    reported "0.98× of the reference" — conclusive — while the machine was at
    88% CPU. A stated rule that the code does not enforce is worse than no
    rule, so both now veto.
    """
    best = load["calibration_best_s"]
    os_note = f", CPU del sistema {os_load}%" if os_load is not None else ""

    if os_load is not None and os_load > OS_LOAD_VETO_PERCENT:
        return LoadVerdict(
            False,
            f"CPU del sistema al {os_load}% antes de medir (> "
            f"{OS_LOAD_VETO_PERCENT}%): hay otro programa usando la máquina, "
            "así que una cifra de latencia no concluiría nada",
        )

    if reference is None:
        return LoadVerdict(
            True,
            f"calibración {best * 1000:.0f} ms{os_note} (sin referencia "
            "previa: se registra como nueva línea base)",
        )
    ratio = best / reference if reference else 1.0
    if ratio > LOAD_VETO_RATIO:
        return LoadVerdict(
            False,
            f"calibración {best * 1000:.0f} ms contra {reference * 1000:.0f} ms "
            f"de referencia ({ratio:.2f}× > {LOAD_VETO_RATIO}×){os_note}: el "
            "equipo no está en reposo y una cifra de latencia no concluiría nada",
        )
    return LoadVerdict(True, f"calibración {ratio:.2f}× de la referencia{os_note}")


@dataclass(frozen=True, slots=True)
class SpreadVerdict:
    repeatable: bool
    worst: str


def spread_verdict(first: Run, second: Run) -> SpreadVerdict:
    """Do two runs of the same code agree?

    This is the gate that makes the others mean something. If the same build,
    run twice, differs by more than the tolerance, then a difference of the
    same size between two *different* builds says nothing at all — and the
    honest report is that the instrument cannot resolve the question, not that
    the build passed.

    The allowance is **the same one the comparison uses**, ``max(percentage,
    floor)``, and that is the whole argument for it: if a change smaller than
    the tolerance would not fail the build, then two runs of the *same* code
    must be allowed to differ by that much too. A flat 10% got this wrong for
    ``db_open_mean_ms`` -- an operation that takes about 4 ms, where one
    scheduler hiccup is a 100% "regression" and an irrelevant one.
    """
    worst_key = ""
    worst_ratio = 0.0
    worst_detail = ""
    repeatable = True
    for metric in METRICS:
        a = first.numbers[metric.key]
        b = second.numbers[metric.key]
        if not a:
            continue
        delta = abs(a - b)
        allowed = metric.allowed_change(a)
        # The index size is not a timing: two runs produce the same bytes or
        # one of them is broken, so it must match exactly rather than
        # "closely enough".
        if metric.key == "index_size_mib":
            ratio = 0.0 if a == b else float("inf")
            ok = a == b
            allowed = 0.0
        else:
            ratio = delta / allowed if allowed else (
                0.0 if delta == 0 else float("inf")
            )
            ok = delta <= allowed
        if ratio > worst_ratio:
            worst_ratio = ratio
            worst_key = metric.key
            worst_detail = (
                f"{metric.label}: {a:.3f} frente a {b:.3f} "
                f"(difiere {delta:.3f}, permitido {allowed:.3f}, "
                f"{delta / a * 100:.1f}%)"
            )
        if not ok:
            repeatable = False
    return SpreadVerdict(repeatable, worst_detail or f"{worst_key} estable")


def machine_fingerprint() -> dict[str, object]:
    """Enough to tell whether two numbers came from comparable machines."""
    return {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }


def _same_machine(baseline: dict, current: dict) -> bool:
    """Whether two sets of numbers came from a comparable machine.

    The fingerprint lives under ``machine`` in the baseline file. The first
    version looked for ``processor`` at the top level, found nothing, and
    printed "different machine" while displaying two identical dicts — a
    warning that is always true teaches people to skip warnings.
    """
    recorded = baseline.get("machine") or {}
    return all(
        recorded.get(key) == current.get(key)
        for key in ("processor", "python", "cpu_count")
    )


def main() -> int:
    # Windows consoles default to a legacy code page, and these gates print
    # the interface's own strings. Never crash while reporting (the CLI has
    # done this since phase 005; a gate that dies printing is worse than one
    # that reports a failure).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.perf_gate",
        description="Reproducible performance gate (phase 038).",
    )
    parser.add_argument("--profile", type=int, default=1000)
    parser.add_argument(
        "--record", action="store_true",
        help="write the measured numbers as the new baseline",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="stop after the load check if the machine is busy",
    )
    args = parser.parse_args()

    baseline = (
        json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        if BASELINE_PATH.exists() else None
    )
    reference_calibration = (
        baseline.get("calibration_best_s") if baseline else None
    )
    fingerprint = machine_fingerprint()
    comparable = baseline is not None and _same_machine(baseline, fingerprint)
    os_load = os_cpu_load()

    print("=" * 96)
    print("PUERTA DE RENDIMIENTO - FASE 038")
    print("=" * 96)
    print(f"perfil:            {args.profile} documentos")
    print(f"máquina:           {fingerprint['processor']} · "
          f"{fingerprint['cpu_count']} hilos · Python {fingerprint['python']}")

    load = measure_load()
    verdict = load_verdict(load, reference_calibration, os_load)
    print(f"carga:             {verdict.detail}")

    if not verdict.conclusive:
        print()
        print("VEREDICTO: INCONCLUYENTE")
        print()
        print("No se ha medido nada y no se ha escrito ninguna cifra.")
        print("Una latencia medida con el equipo ocupado describe el trabajo de")
        print("otro, no el nuestro. Es exactamente el caso que la auditoría de")
        print("2.0.0 no pudo resolver. Cierra lo que esté usando la CPU y repite.")
        return EXIT_INCONCLUSIVE

    if baseline is None or args.record:
        measured = _measure_once(args.profile)
        payload = {
            "phase": "038",
            "profile": args.profile,
            "machine": fingerprint,
            "calibration_best_s": load["calibration_best_s"],
            "numbers": measured.numbers,
        }
        BASELINE_PATH.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        # relative_to would raise when the baseline has been redirected (the
        # tests do that), and a diagnostic that crashes is a bad diagnostic.
        try:
            where = BASELINE_PATH.relative_to(ROOT)
        except ValueError:
            where = BASELINE_PATH
        print(f"\nnueva línea base escrita en {where}")
        for key, value in measured.numbers.items():
            print(f"  {key:<22} {value:>10.3f}")
        print()
        print("VEREDICTO: BASELINE REGISTRADA (no hay con qué comparar todavía)")
        return EXIT_PASS

    if not comparable:
        print()
        print("AVISO: la línea base se midió en otra máquina o con otro Python.")
        print(f"  registrada: {baseline.get('machine')}")
        print(f"  actual:     {fingerprint}")
        print("Las cifras son indicativas, no comparables. Se muestran igual.")
        print()

    first = _measure_once(args.profile)
    if args.quiet:
        second = first  # one pass only, when explicitly asked
    else:
        second = _measure_once(args.profile)

    spread = spread_verdict(first, second)
    print_line = f"mayor dispersión {spread.worst}"
    best = {
        metric.key: min(first.numbers[metric.key], second.numbers[metric.key])
        for metric in METRICS
    }

    comparisons = [
        metric.compare(best[metric.key], baseline["numbers"][metric.key])
        for metric in METRICS
    ]
    # A baseline can name metrics it does not yet trust. The committed one does:
    # db_open_mean_ms was recorded before the measurement was corrected to warm
    # up first, so its reference carries about 7 ms of process startup that the
    # current code does not measure. Comparing against it is lenient -- it can
    # only hide a regression in that metric -- so it is disclosed rather than
    # quietly tolerated or quietly deleted.
    provisional = set(baseline.get("provisional", ()))

    print(f"repetición:        dos pasadas, {print_line}")
    print("-" * 96)
    for comparison in comparisons:
        line = comparison.line()
        if comparison.metric.key in provisional:
            line += "  [referencia provisional]"
        print(line)
    print("-" * 96)
    if provisional:
        print("AVISO: estas métricas se comparan contra una referencia "
              "provisional:")
        print(f"  {', '.join(sorted(provisional))}")
        print("  Su referencia se registró antes de corregir la medición, así")
        print("  que la comparación es benévola: sólo puede ocultar una")
        print("  regresión en ellas. Se reemplaza con `--record` cuando la")
        print("  máquina esté en reposo.")

    failed = [c for c in comparisons if not c.ok]
    if not spread.repeatable:
        print(f"DISPERSIÓN: {spread.worst} entre dos pasadas del mismo código.")
        print("VEREDICTO: INCONCLUYENTE")
        print()
        print("El mismo build medido dos veces difiere más que la tolerancia,")
        print("así que la tolerancia no resuelve nada. La máquina no está en")
        print("reposo o hay otro proceso compilando. No se declara nada.")
        return EXIT_INCONCLUSIVE

    if failed:
        print(f"{len(failed)} métrica(s) fuera de tolerancia:")
        for comparison in failed:
            print(f"  - {comparison.metric.label}: {comparison.metric.key}")
        print("VEREDICTO: FAIL")
        return EXIT_FAIL

    print("VEREDICTO: PASS")
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())