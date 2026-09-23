"""Local performance metrics (phase 011).

Everything stays on this machine: records hold counters, durations and
result counts — never query text, paths or document content (the same
rule the log policy of spec 018 demands). The in-memory ring is bounded
(``HISTORY`` records); the optional JSONL sink is append-only, throttled
and compacted back to ``SINK_RECORDS`` entries, so it cannot grow
without bound either.

Wiring: the core records (``SearchEngine.search`` for every query,
``Indexer.index_root`` for every pass) and entrypoints (services, CLI,
background worker) call :func:`set_sink` once so the records outlive the
process. With no sink the collector is memory-only, which keeps unit
tests free of file I/O.
"""

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# Bounded history: 512 records ≫ the gap between two throttled flushes.
HISTORY = 512
# The on-disk log is compacted back to this many records when it grows.
SINK_RECORDS = 200
# In-memory records waiting for a throttled flush are capped too; older
# unsent records are dropped (counted, never silent).
PENDING_CAP = 512
FLUSH_INTERVAL_SECONDS = 5.0
# Compact the sink once it exceeds this size (~3–4k records).
SINK_COMPACT_BYTES = 512_000


class MetricsCollector:
    """Thread-safe, bounded metrics buffer with an optional JSONL sink."""

    def __init__(self, history: int = HISTORY) -> None:
        self._records: deque[dict] = deque(maxlen=history)
        self._lock = threading.RLock()
        self._sink: Path | None = None
        self._seq = 0
        self._synced_seq = 0
        self._last_flush = 0.0
        self.dropped = 0

    # -- recording ---------------------------------------------------------

    def record_search(self, latency_ms: float, results: int) -> None:
        """One executed search. The query text is deliberately not accepted."""
        with self._lock:
            self._append(
                {
                    "kind": "search",
                    "latency_ms": round(float(latency_ms), 2),
                    "results": int(results),
                }
            )
            self._autosink(force=False)

    def record_index(
        self, duration_s: float, stats: dict, db_writes: int
    ) -> None:
        """One indexing pass: duration, IndexStats counters, rows written."""
        with self._lock:
            record = {
                "kind": "index",
                "duration_s": round(float(duration_s), 3),
                "db_writes": int(db_writes),
            }
            record.update(stats)
            self._append(record)
            # Index passes are rare and worth persisting immediately.
            self._autosink(force=True)

    def _append(self, record: dict) -> None:
        if self._records.maxlen is not None and len(self._records) == self._records.maxlen:
            self.dropped += 1
        self._seq += 1
        record["seq"] = self._seq
        record["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._records.append(record)

    # -- sink --------------------------------------------------------------

    def set_sink(self, path: Path | None) -> None:
        with self._lock:
            self._sink = Path(path) if path else None
            self._last_flush = 0.0

    def flush(self, *, force: bool = False) -> bool:
        """Append unsent records to the sink as one atomic-ish write.

        Throttled to ``FLUSH_INTERVAL_SECONDS`` unless ``force``. I/O
        errors are swallowed: metrics must never break a search or an
        indexing pass (the caller keeps the in-memory ring either way).
        """
        with self._lock:
            if self._sink is None:
                return False
            now = time.monotonic()
            if not force and (now - self._last_flush) < FLUSH_INTERVAL_SECONDS:
                return False
            pending = [r for r in self._records if r["seq"] > self._synced_seq]
            try:
                self._sink.parent.mkdir(parents=True, exist_ok=True)
                line = "".join(
                    json.dumps(record, ensure_ascii=False) + "\n"
                    for record in pending
                )
                if line:
                    handle = os.open(
                        self._sink, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644
                    )
                    try:
                        os.write(handle, line.encode("utf-8"))
                    finally:
                        os.close(handle)
                self._synced_seq = self._seq
                self._last_flush = now
                self._maybe_compact()
                return True
            except OSError:
                return False

    def _maybe_compact(self) -> None:
        """Rewrite the sink to its last records once it grows too big."""
        assert self._sink is not None
        try:
            if self._sink.stat().st_size <= SINK_COMPACT_BYTES:
                return
            kept = self.load(self._sink)[-SINK_RECORDS:]
            temporary = self._sink.with_name(self._sink.name + ".tmp")
            temporary.write_text(
                "".join(
                    json.dumps(record, ensure_ascii=False) + "\n"
                    for record in kept
                ),
                encoding="utf-8",
            )
            os.replace(temporary, self._sink)
        except OSError:
            return

    @staticmethod
    def load(path: Path) -> list[dict]:
        """Read the sink, skipping any line a crash left half-written."""
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return []
        records: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue  # torn tail after a crash — never fatal
            if isinstance(parsed, dict):
                records.append(parsed)
        return records

    def _autosink(self, *, force: bool) -> None:
        if self._sink is not None:
            self.flush(force=force)

    # -- inspection --------------------------------------------------------

    def records(self) -> list[dict]:
        with self._lock:
            return list(self._records)

    def summary(self) -> dict:
        """Aggregates for diagnostics surfaces (spec 015 reads this)."""
        with self._lock:
            records = list(self._records)
            searches = [r for r in records if r["kind"] == "search"]
            indexes = [r for r in records if r["kind"] == "index"]
            latencies = [r["latency_ms"] for r in searches]
            dropped = self.dropped
            history = self._records.maxlen
        return {
            "searches": len(searches),
            "results_total": sum(r["results"] for r in searches),
            "latency_mean_ms": (
                round(sum(latencies) / len(latencies), 2) if latencies else None
            ),
            "latency_last_ms": latencies[-1] if latencies else None,
            "latency_max_ms": max(latencies) if latencies else None,
            "index_passes": len(indexes),
            "last_index": indexes[-1] if indexes else None,
            "buffered": len(records) + dropped,
            "history": history,
            "dropped": dropped,
            "sink": str(self._sink) if self._sink else None,
        }

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._sink = None
            self._seq = 0
            self._synced_seq = 0
            self._last_flush = 0.0
            self.dropped = 0


# -- process-wide collector ---------------------------------------------------

_default = MetricsCollector()


def record_search(latency_ms: float, results: int) -> None:
    _default.record_search(latency_ms, results)


def record_index(duration_s: float, stats: dict, db_writes: int) -> None:
    _default.record_index(duration_s, stats, db_writes)


def set_sink(path: Path | None) -> None:
    _default.set_sink(path)


def flush(*, force: bool = False) -> bool:
    return _default.flush(force=force)


def summary() -> dict:
    return _default.summary()


def records() -> list[dict]:
    return _default.records()


def reset() -> None:
    _default.reset()


__all__ = [
    "HISTORY",
    "MetricsCollector",
    "flush",
    "record_index",
    "record_search",
    "records",
    "reset",
    "set_sink",
    "summary",
]
