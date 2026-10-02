"""Application paths, persistent configuration and logging.

The desktop window (phase 005) and the background indexer (phase 006) share
this module so neither duplicates storage-location or settings logic.
"""

import hashlib
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from universal_search.providers.ignore import IgnoreRules
from universal_search.settings import BY_KEY

log = logging.getLogger("universal_search.config")


def per_user_home() -> Path:
    """The installed layout: one index per user, outside the program folder.

    Split out from :func:`default_home` because phase 037 makes "where the data
    lives" a *decision* with more than one answer, and the diagnostics need to
    report the installed location without going through that decision.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Universal Search"
    return Path.home() / ".universal-search"


def default_home() -> Path:
    """Application home: an explicit override, else portable, else installed.

    The order matters and is asserted by tests. An explicit
    ``UNIVERSAL_SEARCH_HOME`` wins because the background worker passes it to
    its child and a test that sets it means exactly what it says; portable mode
    comes next because it is a property of the deployment, not of the
    environment; and only then does the installed layout apply.
    """
    override = os.environ.get("UNIVERSAL_SEARCH_HOME")
    if override:
        return Path(override)
    from universal_search import portable

    return portable.resolve() if portable.requested()[0] else per_user_home()


@dataclass(frozen=True, slots=True)
class AppPaths:
    home: Path
    # Phase 037: true only for a *discovered* portable deployment. An explicit
    # ``AppPaths(tmp_path)`` in a test is never portable, so portability is a
    # property of how the paths were found, not of the folder they point at.
    portable: bool = False

    @classmethod
    def discover(cls, home: Path | None = None) -> "AppPaths":
        if home is not None:
            return cls(Path(home))
        from universal_search import portable

        current = portable.status()
        return cls(current.home, portable=current.portable)

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
    def worker_lease_file(self) -> Path:
        """Persistent sidecar whose OS lock represents worker ownership."""
        return self.home / "indexer.lock.lease"

    @property
    def worker_owner_file(self) -> Path:
        """PID and generation for the worker that owns ``indexer.lock``."""
        return self.home / "indexer.lock.owner"

    @property
    def startup_claim_file(self) -> Path:
        """One starter's generation while its child is launching."""
        return self.home / "indexer.starting"

    @property
    def events_file(self) -> Path:
        """Bounded, redacted operational events (phase 028)."""
        return self.home / "events.jsonl"

    @property
    def startup_lease_file(self) -> Path:
        """Persistent OS lease serializing startup-claim recovery."""
        return self.home / "indexer.starting.lease"

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

    @property
    def tray_pid_file(self) -> Path:
        """PID of the running tray controller."""
        return self.home / "tray.pid"

    @property
    def diagnostics_request_file(self) -> Path:
        """Touched to ask a live window to show its diagnostics view."""
        return self.home / "gui-diagnostics.flag"

    @property
    def control_state_file(self) -> Path:
        """Small local operational state for the indexing control center.

        The file contains paths, counters and sanitized failure messages only;
        it never contains document text or query text.  Keeping it separate
        from ``config.json`` means a failed scan can be reported without
        making a malformed user configuration look like a scan result.
        """
        return self.home / "control-center.json"

    @property
    def control_center_file(self) -> Path:
        """Compatibility alias for callers that use the shorter name."""
        return self.control_state_file

    def ensure(self) -> None:
        """Create the application home, or fail with a nameable reason.

        Every writer goes through here, which makes this the single place where
        portable mode can refuse to run instead of scattering the check: a
        portable deployment whose folder is read-only must say so once, at the
        moment it matters, rather than half-indexing somewhere else.
        """
        if self.portable:
            from universal_search.portable import ensure_writable

            ensure_writable(self.home)
            return
        self.home.mkdir(parents=True, exist_ok=True)


    @staticmethod
    def generation_filename_token(generation: str) -> str:
        return hashlib.sha256(generation.encode("utf-8")).hexdigest()

    def startup_claim_file_for(self, pid: int, generation: str) -> Path:
        """PID-scoped claim path whose identity survives a partial write."""
        token = self.generation_filename_token(generation)
        return self.home / f"indexer.starting.{int(pid)}.{token}.claim"

    def startup_claim_temporary_file_for(self, pid: int, generation: str) -> Path:
        """Recoverable temporary path for an atomic startup-claim replace."""
        final = self.startup_claim_file_for(pid, generation)
        return final.with_suffix(".tmp")

    def stop_request_file(self, generation: str | None) -> Path:
        """Return the marker owned by exactly one worker generation."""
        if generation is None:
            return self.stop_file
        token = self.generation_filename_token(generation)
        return self.home / f"indexer-stop.{token}.flag"


# -- versioning and migration (phase 043) -------------------------------------
#
# `config.json` had no version, so "which build wrote this?" was unanswerable
# and every field had to be guessed. The index database solved this with
# `PRAGMA user_version` and a migrations ledger; the settings file gets the same
# treatment, and the model is copied rather than invented: a version, a list of
# steps, a refusal to read a file from a *newer* build.
CONFIG_VERSION = 2


def migrate(raw: dict) -> dict:
    """Bring a raw settings mapping up to :data:`CONFIG_VERSION`.

    A file with no ``version`` key is version 1, which is what every build
    before phase 043 wrote. Version 1 needed no key changes -- the new fields
    simply appear with their defaults -- so its migration only stamps the
    version. Keeping the step explicit means the next one has a place to go,
    and keeps the rule that a file from a *newer* build is refused rather than
    half-read.
    """
    version = raw.get("version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 1
    if version > CONFIG_VERSION:
        log.warning(
            "config.json declares version %s, newer than %s; reading what is "
            "understood and keeping the rest",
            version, CONFIG_VERSION,
        )
    updated = dict(raw)
    updated["version"] = CONFIG_VERSION
    return updated


# -- operational overrides (phase 043) ---------------------------------------
#
# The environment variables that change behaviour, in the order they win. This
# is data rather than a comment because the precedence was documented in three
# different files and never in one place, and because phase 043 can then check
# the table instead of trusting the prose. Nothing here overrides a *setting*:
# they choose where the settings live, not what they say.
PRECEDENCE: tuple[tuple[str, str, str], ...] = (
    (
        "UNIVERSAL_SEARCH_HOME",
        "1",
        "La carpeta de la aplicación; manda sobre todo lo demás, y por tanto "
        "sobre dónde vive config.json",
    ),
    (
        "UNIVERSAL_SEARCH_PORTABLE",
        "2",
        "Modo portable; gana al marcador portable.txt y pierde contra "
        "UNIVERSAL_SEARCH_HOME",
    ),
    ("portable.txt", "3", "Marcador junto al ejecutable"),
    ("%LOCALAPPDATA%", "4", "Instalación por usuario, el valor más bajo"),
)


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
    # Phase 042: the fuzzy layer (031) in the window. It has always been
    # available on the command line behind `--no-fuzzy`; this makes the same
    # switch reachable from configuration, so a user who does not want the
    # extra work at query time can turn it off where they can see it.
    fuzzy_enabled: bool = True
    recent_queries: tuple[str, ...] = ()
    recent_queries_enabled: bool = True
    # Phase 036: saved searches (query + sort + group + filters). Plain
    # local configuration: no table, no migration, gone with the config file.
    saved_searches: tuple[dict, ...] = ()
    hotkey: str = "ctrl+alt+s"
    hotkey_enabled: bool = True
    # Phase 043: the semantic layer (026), which until now was only reachable
    # behind the command line's `--no-semantic`. It has the same shape as
    # `fuzzy_enabled` and the same reason to be visible: it is the layer that
    # does extra work per query, so a user who does not want it should be able
    # to say so where they can see the other switches.
    semantic_enabled: bool = True
    # Phase 043: how many results the window asks for. It was a constant in the
    # window module, which made "show me more results" something you answered
    # by editing a Python file.
    result_limit: int = 50
    # Phase 043: whether the tray icon is offered. It was decided only by which
    # command you ran, and the tray's own menu already advertised a settings
    # window that did not exist.
    tray_enabled: bool = True
    # Phase 043: how much the log records. `setup_logging` used a hard-coded
    # INFO, so a user reporting a problem had no way to ask for more detail
    # without editing a file.
    log_level: str = "INFO"
    # Appearance (spec 017): "system" follows the Windows preference.
    theme: str = "system"
    # User scale on top of the system DPI: 1.0 is the system default.
    ui_scale: float = 1.0
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
        raw = migrate(raw)
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
            fuzzy_enabled=_bool(raw.get("fuzzy_enabled"), defaults.fuzzy_enabled),
            recent_queries=_str_tuple(
                raw.get("recent_queries"), defaults.recent_queries
            ),
            recent_queries_enabled=_bool(
                raw.get("recent_queries_enabled"), defaults.recent_queries_enabled
            ),
            saved_searches=_context_dicts(
                raw.get("saved_searches"), defaults.saved_searches
            ),
            hotkey=_str(raw.get("hotkey"), defaults.hotkey),
            hotkey_enabled=_bool(
                raw.get("hotkey_enabled"), defaults.hotkey_enabled
            ),
            semantic_enabled=_bool(
                raw.get("semantic_enabled"), defaults.semantic_enabled
            ),
            result_limit=_int(raw.get("result_limit"), defaults.result_limit),
            tray_enabled=_bool(raw.get("tray_enabled"), defaults.tray_enabled),
            log_level=_str(raw.get("log_level"), defaults.log_level),
            window_geometry=_str(raw.get("window_geometry"), defaults.window_geometry),
            theme=_str(raw.get("theme"), defaults.theme),
            ui_scale=_float(raw.get("ui_scale"), defaults.ui_scale),
        )

    def problems(self) -> list[tuple[str, str]]:
        """Which of this configuration's values the schema would refuse.

        Phase 043: loading coerces a badly typed value to its default, which is
        forgiving, and silently accepts a *well typed* value that is out of
        range. Both were reachable only by editing ``config.json``; now a
        settings window can show the user which values need attention instead of
        leaving them to discover it.
        """
        from universal_search.settings import SettingError, validate

        found: list[tuple[str, str]] = []
        for name in self.__dataclass_fields__:
            if name not in BY_KEY:
                continue
            try:
                validate(name, getattr(self, name))
            except SettingError as exc:
                found.append((name, str(exc)))
        return found

    def repaired(self) -> "AppConfig":
        """This configuration with every out-of-range value pulled back.

        Used by the settings window's "repair" and by nothing else automatic: a
        loader that silently rewrote values would be making a decision for the
        user, and the fix has to be visible.
        """
        from universal_search.settings import SettingError, validate

        changes: dict[str, object] = {}
        for name in self.__dataclass_fields__:
            setting = BY_KEY.get(name)
            if setting is None:
                continue
            value = getattr(self, name)
            try:
                validate(name, value)
            except SettingError:
                changes[name] = setting.clamp(value)
        return replace(self, **changes) if changes else self

    def defaults_keeping_data(self) -> "AppConfig":
        """Every preference back to its default, keeping what the user owns.

        Phase 043's reset. A reset that forgot the folders you index and the
        searches you saved would be data loss wearing a friendly label, so the
        data settings are deliberately not part of it.
        """
        from universal_search.settings import DATA_SETTINGS

        keep = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name in DATA_SETTINGS
        }
        return AppConfig(**keep)

    def save(self, paths: AppPaths) -> None:
        """Write ``config.json`` atomically, keeping unknown keys.

        Two changes in phase 043, both about two processes:

        * The temporary file used a **fixed** name, ``config.json.tmp``. The
          window, its service, the control-centre window and ``set_autostart``
          all write here, and two of them in the same second would write the
          same temporary file and then ``os.replace`` it, so one would publish
          the other's half-written content or fail outright. The name now
          carries the process id, the way the control centre's own sidecar
          already did.
        * A **newer build's** keys used to be destroyed by the next save, since
          it rewrote the file from the fields this build knows. They are read,
          kept, and written back. Downgrading then re-upgrading no longer loses
          a setting the user had configured in between.
        """
        paths.ensure()
        payload = asdict(self)
        try:
            existing = json.loads(paths.config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        if isinstance(existing, dict):
            for key, value in existing.items():
                payload.setdefault(key, value)
            # The file declares which build wrote it, and `migrate` on load
            # reads that. A file this build does not understand keeps its
            # higher number: stamping it down would tell the next build that
            # nobody had ever used the settings it cannot see.
            try:
                written = int(existing.get("version", 1))
            except (TypeError, ValueError):
                written = 1
            payload["version"] = max(CONFIG_VERSION, written)
        else:
            payload["version"] = CONFIG_VERSION
        temporary = paths.config_file.with_name(
            f"{paths.config_file.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporary, paths.config_file)


MAX_RECENT_QUERIES = 20
# The other half of the retention policy, named because phase 042 asks for
# retention to be explicit and the window states it to the user. It used to be
# a bare `[:200]` here and nothing else knew about it.
MAX_QUERY_CHARS = 200


def remember_query(config: AppConfig, query: str) -> AppConfig:
    """Return ``config`` with ``query`` moved to the front of the recents.

    Pure, so every caller shares the same policy: empty or disabled
    configurations come back untouched, entries are deduplicated
    case-insensitively, capped at ``MAX_RECENT_QUERIES`` and truncated at
    ``MAX_QUERY_CHARS``.
    """
    cleaned = " ".join(str(query).split())[:MAX_QUERY_CHARS]
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


# Longest log message written to disk. A diagnostic that pastes a parser
# error, a SQL fragment or (by accident) a line of a document must not turn
# the log into a copy of the user's files (spec 015).
MAX_LOG_MESSAGE = 500


class _BoundedMessage(logging.Filter):
    """Truncate an oversized record before any handler sees it.

    Logging is the one place where a stray ``log.debug(text)`` would leak
    document content to disk. Truncating at the filter — not at the call
    site — means the guarantee holds for code that does not know it is
    logging, including third-party libraries on the same logger.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - broken format string
            rendered = str(record.msg)
        if len(rendered) > MAX_LOG_MESSAGE:
            record.msg = (
                f"{rendered[:MAX_LOG_MESSAGE]}... "
                f"[truncated, {len(rendered)} chars]"
            )
            record.args = ()
        return True


def resolve_log_level(value) -> int:
    """The logging level a configured name means, or INFO.

    A configured value is a string from a file somebody can edit, so this
    returns INFO for anything it does not recognise. Silently dropping to
    WARNING would look like the application had nothing to say.
    """
    from universal_search.settings import LOG_LEVEL_CHOICES

    if isinstance(value, str) and value in LOG_LEVEL_CHOICES:
        return getattr(logging, value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return logging.INFO


def setup_logging(
    paths: AppPaths | None = None, level: str | None = None
) -> logging.Logger:
    """Attach a rotating file handler to the package logger.

    Idempotent for the same file; re-binds when the application home changes
    (tests and the ``UNIVERSAL_SEARCH_HOME`` override). Local, rotated at
    1 MB with 3 backups, timestamped and severity-tagged, with every message
    bounded by :class:`_BoundedMessage`.

    ``level`` is phase 043's ``log_level`` setting. It used to be hard-coded
    here, so asking for a more detailed log meant editing a file, which is the
    one thing the settings window was built to avoid. An unrecognised value
    falls back to INFO rather than to something that would silence the log.
    """
    paths = paths or AppPaths.discover()
    paths.ensure()
    logger = logging.getLogger("universal_search")
    logger.setLevel(resolve_log_level(level))
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
        handler.addFilter(_BoundedMessage())
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
