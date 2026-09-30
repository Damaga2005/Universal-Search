"""Batch operations over a result selection (phase 035).

Every function here is free of Tk and of the clipboard, so the rules can be
tested without a display and the same code runs under any front end.

The rule the whole module exists to keep: **a batch never claims more than it
did.** Three ways that is easy to get wrong, and all three are handled
explicitly rather than by raising:

* **A batch is bounded.** Opening five thousand files at once is a denial of
  service against the user's own machine, so there is a hard cap per batch.
  What was left out is counted and reported, never silently dropped.
* **One failure does not stop the batch.** A path that no longer exists must
  not cost the user the other forty-nine operations, and must not be hidden
  either: the report lists what failed and why.
* **Nothing destructive happens without confirmation.** Forgetting a document
  removes its indexed content, so it requires ``confirm=True`` explicitly; the
  default does nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.privacy import forget

# Opening this many documents in one action is already unreasonable; beyond it
# the rest are reported as skipped rather than queued.
MAX_BATCH_OPERATIONS = 50

# How many failure reasons the human-readable summary spells out. The count of
# failures is always exact; only the list is abbreviated.
MAX_REPORTED_FAILURES = 3


@dataclass(frozen=True, slots=True)
class BatchReport:
    """What a batch actually did, as distinct from what it was asked to do.

    ``skip_reason`` exists because "skipped" without a reason is how a report
    starts lying. A batch that stopped because the user cancelled, or because
    nothing was confirmed, must not print the same words as one that stopped at
    the size limit — the gate checks exactly that, and it caught the first
    version doing precisely this.
    """

    action: str
    requested: int
    attempted: int
    succeeded: tuple[str, ...] = ()
    failures: tuple[tuple[str, str], ...] = ()
    skipped: int = 0
    skip_reason: str = ""

    @property
    def ok(self) -> bool:
        """True only when every requested item was attempted and succeeded."""
        return not self.failures and self.skipped == 0

    @property
    def partial(self) -> bool:
        return bool(self.succeeded) and not self.ok

    def summary(self) -> str:
        """One honest sentence about what happened.

        The wording is the contract. A partial batch must not read like a
        completed one, and nothing is rounded away: the counts are exact and
        only the list of reasons is abbreviated.
        """
        if self.requested == 0:
            return f"{self.action}: no habia nada seleccionado"
        parts = [f"{self.action}: {len(self.succeeded)} de {self.requested}"]
        if self.skipped:
            reason = self.skip_reason or (
                f"el limite de {MAX_BATCH_OPERATIONS}"
            )
            parts.append(f"{self.skipped} sin procesar por {reason}")
        if self.failures:
            shown = "; ".join(
                f"{Path(path).name}: {reason}"
                for path, reason in self.failures[:MAX_REPORTED_FAILURES]
            )
            if len(self.failures) > MAX_REPORTED_FAILURES:
                shown += f"; y {len(self.failures) - MAX_REPORTED_FAILURES} mas"
            parts.append(f"{len(self.failures)} con error ({shown})")
        return "  ·  ".join(parts)


@dataclass
class BatchOperations:
    """Batch actions, with the platform seam injected rather than imported."""

    database: SearchDatabase | None = None
    max_batch: int = MAX_BATCH_OPERATIONS
    # (action, path) pairs recorded by the platform during a run. Only set by
    # tests; production code never fills it.
    calls: list[tuple[str, str]] = field(default_factory=list)

    def _platform(self):
        from universal_search.platforms import get_platform

        return get_platform()

    def _bounded(self, paths: list[Path]) -> tuple[list[Path], int]:
        limit = max(0, int(self.max_batch))
        if len(paths) <= limit:
            return list(paths), 0
        return list(paths[:limit]), len(paths) - limit

    def _run(self, action: str, paths: list[Path]) -> BatchReport:
        target_paths, skipped = self._bounded(paths)
        succeeded: list[str] = []
        failures: list[tuple[str, str]] = []
        for path in target_paths:
            try:
                if action == "open":
                    self._platform().open_path(str(path))
                elif action == "reveal":
                    self._platform().reveal(str(path))
                else:  # pragma: no cover - guarded by the callers
                    raise ValueError(f"unknown batch action {action!r}")
            except Exception as exc:
                # One unopenable path must not cost the other forty-nine.
                failures.append((str(path), type(exc).__name__))
                continue
            self.calls.append((action, str(path)))
            succeeded.append(str(path))
        return BatchReport(
            action={"open": "Abriendo", "reveal": "Mostrando"}[action],
            requested=len(paths),
            attempted=len(target_paths),
            succeeded=tuple(succeeded),
            failures=tuple(failures),
            skipped=skipped,
        )

    def open_all(self, paths: list[Path | str]) -> BatchReport:
        """Open every selected document, bounded and failure-isolated."""
        return self._run("open", [Path(path) for path in paths])

    def reveal_all(self, paths: list[Path | str]) -> BatchReport:
        """Reveal every selected document in the file manager."""
        return self._run("reveal", [Path(path) for path in paths])

    def paths_text(self, paths: list[Path | str]) -> str:
        """The clipboard text for a selection: one path per line.

        Returned rather than put on the clipboard, so the module stays free of
        any display dependency. Duplicates are removed in first-seen order:
        copying the same path twice helps nobody.
        """
        seen: set[str] = set()
        lines: list[str] = []
        for path in paths:
            text = str(path)
            if text in seen:
                continue
            seen.add(text)
            lines.append(text)
        return "\n".join(lines)

    def forget_all(
        self, paths: list[Path | str], *, confirm: bool = False
    ) -> BatchReport:
        """Forget the selected documents from the index.

        Destructive and privacy-relevant, so it does **nothing** without
        ``confirm=True``. Each document is forgotten on its own, because one
        failure must not leave the rest indexed while reporting success.
        """
        if not confirm:
            return BatchReport(
                action="Olvidando",
                requested=len(paths),
                attempted=0,
                skipped=len(paths),
                skip_reason="que no se confirmo",
            )
        if self.database is None:
            return BatchReport(
                action="Olvidando",
                requested=len(paths),
                attempted=0,
                failures=tuple((str(path), "sin base de datos") for path in paths),
            )
        target_paths, skipped = self._bounded([Path(path) for path in paths])
        succeeded: list[str] = []
        failures: list[tuple[str, str]] = []
        for path in target_paths:
            try:
                forget(self.database, path)
            except Exception as exc:
                failures.append((str(path), type(exc).__name__))
                continue
            succeeded.append(str(path))
        return BatchReport(
            action="Olvidando",
            requested=len(paths),
            attempted=len(target_paths),
            succeeded=tuple(succeeded),
            failures=tuple(failures),
            skipped=skipped,
        )


__all__ = [
    "MAX_BATCH_OPERATIONS",
    "MAX_REPORTED_FAILURES",
    "BatchOperations",
    "BatchReport",
]
