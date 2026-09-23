"""Application paths, persistent configuration and logging.

The desktop window (phase 005) and the background indexer (phase 006) share
this module so neither duplicates storage-location or settings logic.
"""

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from universal_search.providers.ignore import IgnoreRules

log = logging.getLogger("universal_search.config")


def default_home() -> Path:
    """Per-user application data directory (overridable for tests)."""
    override = os.environ.get("UNIVERSAL_SEARCH_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Universal Search"
    return Path.home() / ".universal-search"


@dataclass(frozen=True, slots=True)
class AppPaths:
    home: Path

    @classmethod
    def discover(cls, home: Path | None = None) -> "AppPaths":
        return cls(Path(home) if home else default_home())

    @property
    def database(self) -> Path:
        return self.home / "index.db"

    @property
    def config_file(self) -> Path:
        return self.home / "config.json"

    @property
    def log_file(self) -> Path:
        return self.home / "universal-search.log"

    @property
    def status_file(self) -> Path:
        return self.home / "indexer-status.json"

    @property
    def metrics_file(self) -> Path:
        """Append-only local metrics log (phase 011; never leaves the PC)."""
        return self.home / "metrics.jsonl"

    @property
    def pause_file(self) -> Path:
        return self.home / "indexer-paused.flag"

    @property
    def lock_file(self) -> Path:
        return self.home / "indexer.lock"

    @property
    def stop_file(self) -> Path:
        return self.home / "indexer-stop.flag"

    @property
    def gui_pid_file(self) -> Path:
        """PID of the running desktop window (global-hotkey signaling)."""
        return self.home / "gui.pid"

    @property
    def show_request_file(self) -> Path:
        """Touched by the worker to ask a live window to present itself."""
        return self.home / "gui-show.flag"

    def ensure(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, slots=True)
class AppConfig:
    roots: tuple[str, ...] = ()
    ignore_dirs: tuple[str, ...] = ()
    ignore_patterns: tuple[str, ...] = ()
    start_with_windows: bool = False
    indexer_interval_seconds: int = 300
    indexer_file_delay: float = 0.0
    onedrive_download_max_mb: float = 0.0
    contexts: tuple[dict, ...] = ()
    active_context: str = ""
    usage_tracking: bool = False
    recent_queries: tuple[str, ...] = ()
    recent_queries_enabled: bool = True
    hotkey: str = "ctrl+alt+s"
    hotkey_enabled: bool = True
    window_geometry: str = ""

    def ignore_rules(self) -> IgnoreRules:
        return IgnoreRules.defaults(
            directories=self.ignore_dirs, patterns=self.ignore_patterns
        )

    @classmethod
    def load(cls, paths: AppPaths) -> "AppConfig":
        defaults = cls()
        try:
            raw = json.loads(paths.config_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return defaults
        except (OSError, ValueError):
            log.warning("unreadable config at %s; using defaults", paths.config_file)
            return defaults
        if not isinstance(raw, dict):
            return defaults
        return cls(
            roots=_str_tuple(raw.get("roots"), defaults.roots),
            ignore_dirs=_str_tuple(raw.get("ignore_dirs"), defaults.ignore_dirs),
            ignore_patterns=_str_tuple(
                raw.get("ignore_patterns"), defaults.ignore_patterns
            ),
            start_with_windows=_bool(raw.get("start_with_windows"), defaults.start_with_windows),
            indexer_interval_seconds=_int(
                raw.get("indexer_interval_seconds"), defaults.indexer_interval_seconds
            ),
            indexer_file_delay=_float(
                raw.get("indexer_file_delay"), defaults.indexer_file_delay
            ),
            onedrive_download_max_mb=_float(
                raw.get("onedrive_download_max_mb"),
                defaults.onedrive_download_max_mb,
            ),
            contexts=_context_dicts(raw.get("contexts"), defaults.contexts),
            active_context=_str(raw.get("active_context"), defaults.active_context),
            usage_tracking=_bool(raw.get("usage_tracking"), defaults.usage_tracking),
            recent_queries=_str_tuple(
                raw.get("recent_queries"), defaults.recent_queries
            ),
            recent_queries_enabled=_bool(
                raw.get("recent_queries_enabled"), defaults.recent_queries_enabled
            ),
            hotkey=_str(raw.get("hotkey"), defaults.hotkey),
            hotkey_enabled=_bool(
                raw.get("hotkey_enabled"), defaults.hotkey_enabled
            ),
            window_geometry=_str(raw.get("window_geometry"), defaults.window_geometry),
        )

    def save(self, paths: AppPaths) -> None:
        paths.ensure()
        payload = json.dumps(asdict(self), indent=2, ensure_ascii=False)
        temporary = paths.config_file.with_name(paths.config_file.name + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, paths.config_file)


MAX_RECENT_QUERIES = 20


def remember_query(config: AppConfig, query: str) -> AppConfig:
    """Return ``config`` with ``query`` moved to the front of the recents.

    Pure, so every caller shares the same policy: empty or disabled
    configurations come back untouched, entries are deduplicated
    case-insensitively and capped at ``MAX_RECENT_QUERIES``.
    """
    cleaned = " ".join(str(query).split())[:200]
    if not cleaned or not config.recent_queries_enabled:
        return config
    kept = [
        entry
        for entry in config.recent_queries
        if entry.casefold() != cleaned.casefold()
    ]
    updated = (cleaned, *kept)[:MAX_RECENT_QUERIES]
    if updated == config.recent_queries:
        return config
    return replace(config, recent_queries=updated)


def setup_logging(paths: AppPaths | None = None) -> logging.Logger:
    """Attach a rotating file handler to the package logger.

    Idempotent for the same file; re-binds when the application home changes
    (tests and the ``UNIVERSAL_SEARCH_HOME`` override).
    """
    paths = paths or AppPaths.discover()
    paths.ensure()
    logger = logging.getLogger("universal_search")
    logger.setLevel(logging.INFO)
    target = paths.log_file.resolve()
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler) and Path(
            handler.baseFilename
        ) != target:
            logger.removeHandler(handler)
            handler.close()
    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        handler = RotatingFileHandler(
            paths.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    return logger


def _str_tuple(value, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return fallback


def _context_dicts(value, fallback: tuple[dict, ...]) -> tuple[dict, ...]:
    """Keep only usable context entries (dict with a non-empty name)."""
    if isinstance(value, (list, tuple)):
        return tuple(
            item
            for item in value
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
    return fallback


def _bool(value, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _int(value, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _float(value, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _str(value, fallback: str) -> str:
    return value if isinstance(value, str) else fallback
