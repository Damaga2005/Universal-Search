"""One typed, validated, migrated settings schema for everything the user can change.

Phase 043. Before this module, a setting was a field on ``AppConfig`` with a
default and nothing else: no bounds, no migration, no version, no way to ask
what a setting is called or what it does, and three different surfaces -- the
CLI, the config file and the GUI -- each validating a different subset of
nothing.

What lives here, and what deliberately does not:

* **Here**: for every user-configurable value, its type, its default, its valid
  range, whether it is advanced, whether changing it needs a restart, and which
  part of the application it governs. That is a fact about the software and it
  belongs in the core.
* **Not here**: the label and the explanation a person reads. Those live in
  ``gui/strings.py`` under ``SETTINGS.<KEY>``, so the catalogue stays the single
  list of visible text and the phase 039 string audit still covers it. A schema
  that carried its own Spanish strings would be a second place to translate.

The module has no Tk and no I/O. It answers questions; ``appconfig`` persists and
``gui`` displays.
"""

from __future__ import annotations

from dataclasses import dataclass

# -- setting kinds ------------------------------------------------------------
# One vocabulary for every setting, so the window can pick a control and the
# validator can pick a check without a per-setting special case.
BOOL = "bool"
INT = "int"
FLOAT = "float"
CHOICE = "choice"
TEXT = "text"
PATHLIST = "pathlist"

# -- groups, by what a user is trying to do -----------------------------------
#
# The *name* of a group is visible text and lives in `gui/strings.py` under
# `SETTINGS.GROUP.<NAME>`, not here. A first version of this module carried a
# `GROUP_LABELS` dict with the Spanish text inline, which is precisely the
# second place to translate that the module's own docstring rules out.
GROUP_SOURCES = "sources"
GROUP_INDEXING = "indexing"
GROUP_SEARCH = "search"
GROUP_APPEARANCE = "appearance"
GROUP_PRIVACY = "privacy"
GROUP_DIAGNOSTICS = "diagnostics"

# Ordered, because the window shows them in this order and "one coherent
# experience" depends on the grouping being stable rather than alphabetical.
GROUP_ORDER: tuple[str, ...] = (
    GROUP_SOURCES,
    GROUP_SEARCH,
    GROUP_INDEXING,
    GROUP_APPEARANCE,
    GROUP_PRIVACY,
    GROUP_DIAGNOSTICS,
)

# Bounds. Phase 043 measured what happens without them: a negative
# `indexer_file_delay` reaches `time.sleep(-1)`, which raises ValueError inside
# the indexing loop and fails the whole pass; a zero or negative interval spins
# the worker's wait loop with no sleep at all. Neither was reachable from a
# typed settings window, and both were reachable by editing a JSON file.
MIN_INDEXER_INTERVAL_SECONDS = 30
MAX_INDEXER_INTERVAL_SECONDS = 86400
MIN_FILE_DELAY_SECONDS = 0.0
MAX_FILE_DELAY_SECONDS = 60.0
MIN_ONEDRIVE_MB = 0.0
MAX_ONEDRIVE_MB = 1024.0
MIN_RESULT_LIMIT = 10
MAX_RESULT_LIMIT = 500
MIN_UI_SCALE = 0.75
MAX_UI_SCALE = 2.5

THEME_CHOICES = ("system", "light", "dark")
LOG_LEVEL_CHOICES = ("WARNING", "INFO", "DEBUG")

# Settings whose change needs the window (or the worker) to restart, because the
# value is read once at construction. Stated here so the window can say so
# instead of the user discovering it.
RESTART_REQUIRED = frozenset(
    {
        "theme",
        "ui_scale",
        "hotkey",
        "indexer_interval_seconds",
        "indexer_file_delay",
        "log_level",
    }
)

# Settings that describe where data is kept rather than how to behave. A reset
# to defaults must never touch these: forgetting the folders a user indexes is
# not a reset, it is data loss.
DATA_SETTINGS = frozenset({"roots", "ignore_dirs", "ignore_patterns",
                           "contexts", "saved_searches", "recent_queries",
                           "active_context", "window_geometry"})


@dataclass(frozen=True, slots=True)
class Setting:
    """One setting: what it is, what it accepts, and what it governs."""

    key: str
    group: str
    kind: str
    default: object
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    advanced: bool = False
    unit: str = ""

    @property
    def restart_required(self) -> bool:
        return self.key in RESTART_REQUIRED

    @property
    def is_data(self) -> bool:
        """Whether resetting to defaults would discard user data."""
        return self.key in DATA_SETTINGS

    def clamp(self, value):
        """Bring a number inside its bounds, or return it unchanged."""
        if self.kind not in {INT, FLOAT} or not isinstance(value, (int, float)):
            return value
        if self.minimum is not None:
            value = max(self.minimum, value)
        if self.maximum is not None:
            value = min(self.maximum, value)
        return value


def _setting(key, group, kind, default, **kwargs) -> Setting:
    return Setting(key=key, group=group, kind=kind, default=default, **kwargs)


SETTINGS: tuple[Setting, ...] = (
    # -- what is indexed ----------------------------------------------------
    _setting("roots", GROUP_SOURCES, PATHLIST, (), advanced=True),
    _setting("ignore_dirs", GROUP_SOURCES, PATHLIST, (), advanced=True),
    _setting("ignore_patterns", GROUP_SOURCES, PATHLIST, (),
             advanced=True),

    # -- how search behaves -------------------------------------------------
    _setting("result_limit", GROUP_SEARCH, INT, 50,
             minimum=MIN_RESULT_LIMIT, maximum=MAX_RESULT_LIMIT,
             unit="documentos"),
    _setting("fuzzy_enabled", GROUP_SEARCH, BOOL, True),
    _setting("semantic_enabled", GROUP_SEARCH, BOOL, True),
    _setting("hotkey", GROUP_SEARCH, TEXT, "ctrl+alt+s", advanced=True),
    _setting("hotkey_enabled", GROUP_SEARCH, BOOL, True),

    # -- when and how indexing happens -------------------------------------
    _setting("indexer_interval_seconds", GROUP_INDEXING, INT, 300,
             minimum=MIN_INDEXER_INTERVAL_SECONDS,
             maximum=MAX_INDEXER_INTERVAL_SECONDS, unit="segundos",
             advanced=True),
    _setting("indexer_file_delay", GROUP_INDEXING, FLOAT, 0.0,
             minimum=MIN_FILE_DELAY_SECONDS, maximum=MAX_FILE_DELAY_SECONDS,
             unit="segundos por archivo", advanced=True),
    _setting("onedrive_download_max_mb", GROUP_INDEXING, FLOAT, 0.0,
             minimum=MIN_ONEDRIVE_MB, maximum=MAX_ONEDRIVE_MB,
             unit="MB"),
    _setting("start_with_windows", GROUP_INDEXING, BOOL, False),

    # -- how it looks -------------------------------------------------------
    _setting("theme", GROUP_APPEARANCE, CHOICE, "system",
             choices=THEME_CHOICES),
    _setting("ui_scale", GROUP_APPEARANCE, FLOAT, 1.0,
             minimum=MIN_UI_SCALE, maximum=MAX_UI_SCALE),
    _setting("tray_enabled", GROUP_APPEARANCE, BOOL, True),

    # -- what is remembered -------------------------------------------------
    _setting("usage_tracking", GROUP_PRIVACY, BOOL, False),
    _setting("recent_queries_enabled", GROUP_PRIVACY, BOOL, True),

    # -- diagnostics -------------------------------------------------------
    _setting("log_level", GROUP_DIAGNOSTICS, CHOICE, "INFO",
             choices=LOG_LEVEL_CHOICES, advanced=True),
)

BY_KEY: dict[str, Setting] = {setting.key: setting for setting in SETTINGS}


def settings_for(group: str) -> tuple[Setting, ...]:
    return tuple(setting for setting in SETTINGS if setting.group == group)


def as_mapping() -> dict[str, object]:
    """Every setting with its default, as a plain dict."""
    return {setting.key: setting.default for setting in SETTINGS}


def validate(key: str, value):
    """One value against one setting, or raise :class:`SettingError`.

    Separate from :meth:`Setting.clamp` on purpose. Clamping is what a window
    does while somebody drags a slider; validation is what the loader does with
    a file it did not write, where silently replacing a bad value with a
    default hides the fact that something is wrong.
    """
    setting = BY_KEY.get(key)
    if setting is None:
        raise SettingError(f"ajuste desconocido: {key!r}")
    if setting.kind == BOOL:
        if not isinstance(value, bool):
            raise SettingError(f"{key}: se esperaba verdadero o falso")
        return value
    if setting.kind == CHOICE:
        if value not in setting.choices:
            raise SettingError(
                f"{key}: {value!r} no es una de {list(setting.choices)}"
            )
        return value
    if setting.kind == PATHLIST:
        if isinstance(value, (list, tuple)) and all(
            isinstance(item, str) for item in value
        ):
            return tuple(value)
        raise SettingError(f"{key}: se esperaba una lista de texto")
    if setting.kind == TEXT:
        if not isinstance(value, str):
            raise SettingError(f"{key}: se esperaba texto")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingError(f"{key}: se esperaba un número")
    if setting.kind == INT and int(value) != value:
        raise SettingError(f"{key}: se esperaba un número entero")
    if setting.minimum is not None and value < setting.minimum:
        raise SettingError(f"{key}: {value} está por debajo de {setting.minimum}")
    if setting.maximum is not None and value > setting.maximum:
        raise SettingError(f"{key}: {value} está por encima de {setting.maximum}")
    return value


class SettingError(ValueError):
    """A setting was given a value it does not accept."""


def validate_all(values: dict) -> tuple[dict, list[tuple[str, str]]]:
    """Validate a whole mapping.

    Returns the values that passed and a list of ``(key, reason)`` for the ones
    that did not. A partial result is deliberate: refusing to load a config
    because one number is wrong would leave the user with no settings at all,
    which is a worse answer than using nineteen of twenty.
    """
    accepted: dict[str, object] = {}
    problems: list[tuple[str, str]] = []
    for key, value in values.items():
        if key not in BY_KEY:
            continue
        try:
            accepted[key] = validate(key, value)
        except SettingError as exc:
            problems.append((key, str(exc)))
    return accepted, problems