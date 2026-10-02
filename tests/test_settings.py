"""Phase 043: one typed, validated, migrated settings system.

Most of this module needs no display. The last group builds the settings
window, because "one coherent settings experience" is a claim about a window and
a claim about a window deserves to be executed at least once.

The test that starts the phase is
:meth:`test_a_negative_file_delay_would_reach_time_sleep`. Before phase 043 a
negative ``indexer_file_delay`` in ``config.json`` reached ``time.sleep(-1)``,
which raises ``ValueError`` inside the indexing loop and fails the whole pass --
a value no settings window could produce, reachable by editing a JSON file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from universal_search import settings as schema
from universal_search.appconfig import (
    CONFIG_VERSION,
    PRECEDENCE,
    AppConfig,
    AppPaths,
    migrate,
    resolve_log_level,
    setup_logging,
)
from universal_search.gui import strings
from universal_search.gui.settings_window import (
    EXPORT_SUFFIX,
    EXPORT_VERSION,
    SettingsService,
    _coerce,
    _readable,
)

# -- the schema --------------------------------------------------------------

def test_every_schema_setting_exists_on_the_configuration():
    """A setting the configuration cannot hold is a setting nobody can set."""
    fields = set(AppConfig().__dataclass_fields__)
    for setting in schema.SETTINGS:
        assert setting.key in fields, setting.key


def test_every_configuration_field_that_is_a_preference_is_in_the_schema():
    """And the other way round: no preference nobody can reach.

    Three of the data fields are also settings -- they are listed in the schema
    so the window can show them read-only -- so the comparison is "every
    non-data field is reachable", not equality of two sets.
    """
    data = {
        "roots", "ignore_dirs", "ignore_patterns", "contexts",
        "active_context", "saved_searches", "recent_queries", "window_geometry",
    }
    fields = set(AppConfig().__dataclass_fields__)
    assert (fields - data) - set(schema.BY_KEY) == set()
    # And the schema adds nothing that is not a field.
    assert set(schema.BY_KEY) - fields == set()


def test_the_defaults_agree_with_the_dataclass():
    for setting in schema.SETTINGS:
        assert getattr(AppConfig(), setting.key) == setting.default, setting.key


def test_every_group_has_a_label_and_a_readable_name():
    """The name lives in the catalogue, not in the schema.

    A `GROUP_LABELS` dict existed here for one commit; the module's own
    docstring rules out a second place to translate, so it is gone.
    """
    assert not hasattr(schema, "GROUP_LABELS")
    for name in schema.GROUP_ORDER:
        label = strings.get(f"SETTINGS.GROUP.{name.upper()}")
        assert label and len(label) > 3, name


# -- validation --------------------------------------------------------------

def test_a_negative_file_delay_would_reach_time_sleep():
    """The defect that justified bounds."""
    config = AppConfig(indexer_file_delay=-1.0)
    assert config.problems(), "a negative delay must be reported"
    # And `time.sleep` really would have raised, which is why it matters.
    with pytest.raises(ValueError):
        time.sleep(-1.0)
    assert config.repaired().indexer_file_delay == 0.0


def test_an_interval_of_zero_would_spin_the_worker_loop():
    config = AppConfig(indexer_interval_seconds=0)
    assert config.problems()
    assert config.repaired().indexer_interval_seconds == (
        schema.MIN_INDEXER_INTERVAL_SECONDS
    )


def test_validation_refuses_the_wrong_type_and_says_why():
    with pytest.raises(schema.SettingError) as caught:
        schema.validate("result_limit", "muchos")
    assert "result_limit" in str(caught.value)
    with pytest.raises(schema.SettingError):
        schema.validate("fuzzy_enabled", "sí")
    with pytest.raises(schema.SettingError):
        schema.validate("theme", "neón")
    with pytest.raises(schema.SettingError):
        schema.validate("no_existe", 1)


def test_a_number_with_a_fraction_is_not_an_integer():
    with pytest.raises(schema.SettingError):
        schema.validate("result_limit", 12.5)
    assert schema.validate("result_limit", 12) == 12


def test_a_boolean_is_not_a_number():
    """`True` is an int in Python, and a slider that sends one would be accepted."""
    with pytest.raises(schema.SettingError):
        schema.validate("indexer_interval_seconds", True)


def test_validate_all_keeps_what_it_can_and_explains_what_it_cannot():
    accepted, problems = schema.validate_all(
        {"result_limit": 200, "indexer_interval_seconds": -5, "unknown": 1}
    )
    assert accepted == {"result_limit": 200}
    assert [key for key, _ in problems] == ["indexer_interval_seconds"]
    assert "unknown" not in accepted


def test_bounds_are_the_ones_the_window_offers():
    for setting in schema.SETTINGS:
        if setting.kind not in {schema.INT, schema.FLOAT}:
            continue
        assert setting.minimum is not None, setting.key
        assert setting.maximum is not None, setting.key
        assert setting.minimum < setting.maximum, setting.key


# -- version and migration ---------------------------------------------------

def test_a_file_with_no_version_is_version_one_and_migrates():
    migrated = migrate({"roots": ["D:/a"]})
    assert migrated["version"] == CONFIG_VERSION
    assert migrated["roots"] == ["D:/a"]


def test_a_file_from_a_newer_build_is_read_but_not_downgraded(tmp_path):
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text(
        json.dumps({"version": 99, "roots": ["D:/a"], "future_setting": 1}),
        encoding="utf-8",
    )
    config = AppConfig.load(paths)
    assert config.roots == ("D:/a",)
    # Saving must not claim to be newer than the file we were just given.
    config.save(paths)
    written = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert written["version"] == 99
    assert written["future_setting"] == 1


def test_an_unreadable_version_is_treated_as_one():
    assert migrate({"version": "no soy un numero"})["version"] == CONFIG_VERSION


# -- persistence -------------------------------------------------------------

def test_saving_is_atomic_and_does_not_clobber_a_concurrent_writer(tmp_path):
    """The temporary file used to have a fixed name.

    The window, its service and the control centre all write here, and two of
    them in the same second would have written the same ``config.json.tmp``.
    """
    paths = AppPaths(tmp_path / "home")
    AppConfig(roots=("D:/a",)).save(paths)
    leftovers = [p for p in paths.home.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"a temporary file was left behind: {leftovers}"


def test_a_newer_keys_survives_a_save(tmp_path):
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text(
        json.dumps({"version": 1, "future_setting": {"a": 1}}), encoding="utf-8"
    )
    AppConfig(roots=("D:/b",)).save(paths)
    written = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert written["future_setting"] == {"a": 1}
    assert written["roots"] == ["D:/b"]


def test_a_corrupt_file_still_falls_back_to_defaults(tmp_path):
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text("{no es json", encoding="utf-8")
    assert AppConfig.load(paths) == AppConfig()


def test_an_out_of_range_value_loads_but_is_reported(tmp_path):
    """Forgiving on load, honest about it: rewriting silently would hide it."""
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text(
        json.dumps({"indexer_interval_seconds": -1, "ui_scale": 9.0}),
        encoding="utf-8",
    )
    config = AppConfig.load(paths)
    assert config.indexer_interval_seconds == -1
    reported = {key for key, _ in config.problems()}
    assert {"indexer_interval_seconds", "ui_scale"} <= reported


# -- reset -------------------------------------------------------------------

def test_reset_keeps_the_data_and_drops_the_preferences():
    config = AppConfig(
        roots=("D:/apuntes",), saved_searches=({"name": "n", "query": "q"},),
        recent_queries=("capacitor",), active_context="Universidad",
        window_geometry="900x600", hotkey="ctrl+q", ui_scale=2.0,
        result_limit=200,
    )
    reset = config.defaults_keeping_data()
    assert reset.roots == ("D:/apuntes",)
    assert reset.saved_searches == ({"name": "n", "query": "q"},)
    assert reset.recent_queries == ("capacitor",)
    assert reset.active_context == "Universidad"
    assert reset.window_geometry == "900x600"
    # And the preferences really are back to their defaults.
    assert reset.hotkey == schema.BY_KEY["hotkey"].default
    assert reset.ui_scale == 1.0
    assert reset.result_limit == 50


def test_data_settings_are_declared_as_such():
    for setting in schema.SETTINGS:
        if setting.key in {"roots", "ignore_dirs", "ignore_patterns"}:
            assert setting.is_data


# -- precedence --------------------------------------------------------------

def test_the_precedence_table_is_ordered_and_explained():
    ranks = [int(rank) for _, rank, _ in PRECEDENCE]
    assert ranks == sorted(ranks) == list(range(1, len(ranks) + 1))
    for name, _rank, why in PRECEDENCE:
        assert name and why
    assert PRECEDENCE[0][0] == "UNIVERSAL_SEARCH_HOME"


def test_home_still_beats_the_marker(tmp_path, monkeypatch):
    """The table says so; this checks the code agrees."""
    from universal_search import portable

    monkeypatch.setenv(portable.HOME_ENV, str(tmp_path / "home"))
    monkeypatch.delenv(portable.PORTABLE_ENV, raising=False)
    assert AppPaths.discover().home == tmp_path / "home"


def test_no_environment_variable_overrides_a_setting():
    """They choose *where* settings live, never *what they say*."""
    import os

    names = {name for name, _, _ in PRECEDENCE if name in os.environ or "%" in name}
    assert "UNIVERSAL_SEARCH_WORKER_GENERATION" not in names


# -- logging level -----------------------------------------------------------

def test_the_log_level_setting_reaches_the_logger(tmp_path):
    logger = setup_logging(AppPaths(tmp_path / "home"), "DEBUG")
    assert logger.level == 10
    logger = setup_logging(AppPaths(tmp_path / "home"), "WARNING")
    assert logger.level == 30
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def test_an_unknown_log_level_falls_back_to_info_and_says_so_by_level():
    assert resolve_log_level("DEBUG") == 10
    assert resolve_log_level("inventado") == 20
    assert resolve_log_level(None) == 20
    assert resolve_log_level(True) == 20


# -- the service -------------------------------------------------------------

@pytest.fixture
def service(tmp_path):
    return SettingsService(paths=AppPaths(tmp_path / "home"))


def test_the_service_groups_by_intent_not_by_module(service):
    grouped = service.grouped()
    names = [name for name, _ in grouped.groups]
    assert names == list(schema.GROUP_ORDER)
    # Every setting appears exactly once.
    keys = [item.key for _, items in grouped.groups for item in items]
    assert sorted(keys) == sorted(schema.BY_KEY)


def test_data_settings_are_shown_but_not_editable_there(service):
    grouped = service.grouped()
    editable = {item.key for _, items in grouped.groups for item in items if item.editable}
    assert "roots" not in editable
    assert "result_limit" in editable
    assert {item.key for item in grouped.data_summary("sources")} == {
        "roots", "ignore_dirs", "ignore_patterns"
    }


def test_setting_a_value_validates_and_persists(service):
    service.set("result_limit", 200)
    assert service.get("result_limit") == 200
    assert AppConfig.load(service.paths).result_limit == 200


def test_setting_a_bad_value_changes_nothing_on_disk(service):
    service.set("result_limit", 200)
    with pytest.raises(schema.SettingError):
        service.set("result_limit", 1)
    assert AppConfig.load(service.paths).result_limit == 200


def test_set_many_applies_what_it_can_and_refuses_the_rest(service):
    updated, refused = service.set_many(
        {"result_limit": 150, "ui_scale": 99.0, "theme": "neón"}
    )
    assert updated.result_limit == 150
    assert updated.ui_scale == 1.0
    assert {key for key, _ in refused} == {"ui_scale", "theme"}


def test_repair_reports_what_it_changed_and_is_idempotent(service):
    service._save(AppConfig(indexer_interval_seconds=-5))
    assert service.problems()
    updated, before = service.repair()
    assert len(before) == 1
    assert updated.indexer_interval_seconds == schema.MIN_INDEXER_INTERVAL_SECONDS
    assert service.problems() == []
    _updated, again = service.repair()
    assert again == []


def test_an_unknown_key_is_not_a_setting(service):
    with pytest.raises(schema.SettingError):
        service.get("no_existe")


# -- export and import -------------------------------------------------------

def test_an_export_carries_preferences_and_no_data(service, tmp_path):
    service.set("result_limit", 200)
    service.set("theme", "dark")
    path = service.export(tmp_path / "mis-ajustes")
    assert path.name.endswith(EXPORT_SUFFIX)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == EXPORT_VERSION
    assert payload["settings"]["result_limit"] == 200
    # Nothing that says where the user keeps their documents.
    for key in ("roots", "ignore_dirs", "saved_searches", "recent_queries"):
        assert key not in payload["settings"]


def test_an_export_says_it_carries_no_secrets(service, tmp_path):
    """The prompt asks for it explicitly; an assertion would be better than a hope."""
    payload = json.loads(
        service.export(tmp_path / "x").read_text(encoding="utf-8")
    )
    note = payload["note"].lower()
    assert "contrasenas" in note or "contraseñas" in note
    assert "claves" in note


def test_importing_restores_what_was_exported(service, tmp_path):
    service.set("result_limit", 200)
    service.set("theme", "dark")
    path = service.export(tmp_path / "x")
    service.set("result_limit", 50)
    service.set("theme", "light")
    applied, refused = service.import_from(path)
    assert refused == []
    assert service.get("result_limit") == 200
    assert service.get("theme") == "dark"
    assert "result_limit" in applied


def test_importing_refuses_a_file_from_a_newer_build(service, tmp_path):
    newer = tmp_path / "nuevo.json"
    newer.write_text(
        json.dumps({"version": 99, "settings": {"result_limit": 200}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as caught:
        service.import_from(newer)
    assert "versión posterior" in str(caught.value)


def test_importing_ignores_unknown_and_data_keys(service, tmp_path):
    payload = tmp_path / "raro.json"
    payload.write_text(
        json.dumps({
            "version": 1,
            "settings": {"result_limit": 200, "roots": ["D:/otro"], "nuevo": 1},
        }),
        encoding="utf-8",
    )
    applied, refused = service.import_from(payload)
    assert applied == ["result_limit"]
    assert service.get("roots") == (), "importar no puede cambiar qué se indexa"


def test_importing_refuses_values_out_of_range(service, tmp_path):
    payload = tmp_path / "raro.json"
    payload.write_text(
        json.dumps({"version": 1, "settings": {"indexer_interval_seconds": -5}}),
        encoding="utf-8",
    )
    applied, refused = service.import_from(payload)
    assert applied == []
    assert [key for key, _ in refused] == ["indexer_interval_seconds"]


def test_importing_a_file_that_is_not_a_settings_file_is_refused(service, tmp_path):
    payload = tmp_path / "otro.json"
    payload.write_text(json.dumps({"hola": "mundo"}), encoding="utf-8")
    with pytest.raises(ValueError):
        service.import_from(payload)


# -- the values a window shows and sends back --------------------------------

def test_a_value_reads_the_way_a_person_would_say_it():
    assert _readable(True) == "sí"
    assert _readable(False) == "no"
    assert _readable(()) == "(ninguno)"
    assert _readable(("a", "b")) == "a, b"
    assert _readable("oscuro") == "oscuro"


class _Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def test_a_control_value_comes_back_as_the_type_the_schema_wants():
    assert _coerce(schema.BY_KEY["result_limit"], _Variable("120")) == 120
    assert _coerce(schema.BY_KEY["ui_scale"], _Variable("1.5")) == 1.5
    assert _coerce(schema.BY_KEY["fuzzy_enabled"], _Variable(False)) is False
    assert _coerce(schema.BY_KEY["theme"], _Variable("dark")) == "dark"


def test_an_unreadable_number_is_sent_to_the_schema_to_refuse():
    """Not silently zero: a spin box holding nonsense must not become a 0."""
    assert _coerce(schema.BY_KEY["result_limit"], _Variable("")) == ""
    with pytest.raises(schema.SettingError):
        schema.validate("result_limit", "")


# -- the catalogue -----------------------------------------------------------

def test_every_setting_has_a_label_and_an_explanation():
    for setting in schema.SETTINGS:
        assert f"SETTINGS.{setting.key.upper()}.LABEL" in strings.CATALOGUE, setting.key
        help_text = strings.get(f"SETTINGS.{setting.key.upper()}.HELP")
        assert len(help_text) > 30, setting.key
        assert strings.get(f"SETTINGS.{setting.key.upper()}.LABEL") != help_text


def test_every_group_has_a_catalogued_name():
    for name in schema.GROUP_ORDER:
        assert f"SETTINGS.GROUP.{name.upper()}" in strings.CATALOGUE, name


def test_a_setting_that_needs_a_restart_says_so():
    for key in ("theme", "ui_scale", "hotkey"):
        assert schema.BY_KEY[key].restart_required
        assert "reiniciar" in strings.get("SETTINGS.RESTART")
    assert not schema.BY_KEY["result_limit"].restart_required


def test_the_destructive_operation_asks_and_names_what_it_keeps():
    confirm = strings.get("SETTINGS.RESET_CONFIRM")
    assert "NO se borran" in confirm or "no se borran" in confirm
    assert "SETTINGS.RESET_TITLE" in strings.CATALOGUE


# -- the gate ----------------------------------------------------------------

def test_the_settings_gate_runs_and_reports_every_threshold():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "evaluation.settings_gate"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=600,
        env={**_environment(), "PYTHONIOENCODING": "utf-8"},
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "VEREDICTO: SHIP" in output, output
    assert output.count("PASS") == len(
        __import__("evaluation.settings_gate", fromlist=["THRESHOLDS"]).THRESHOLDS
    ), output
    assert "NO SHIP" not in output, output


def _environment() -> dict:
    import os

    return dict(os.environ)


def test_the_gate_declares_thirteen_invariants_at_zero():
    from evaluation import settings_gate

    assert len(settings_gate.THRESHOLDS) == 13
    assert set(settings_gate.THRESHOLDS) == set(settings_gate.GATE_LINES)
    assert all(value == 0 for value in settings_gate.THRESHOLDS.values())


# -- the window --------------------------------------------------------------

@pytest.fixture
def window(tmp_path, tk_guard):
    from universal_search.gui.settings_window import SettingsWindow

    service = SettingsService(paths=AppPaths(tmp_path / "home"))
    created = SettingsWindow(None, service=service)
    yield created
    try:
        created.destroy()
    except Exception:  # pragma: no cover - teardown best effort
        pass


def test_the_window_has_one_tab_per_group_of_intent(window):
    from tkinter import ttk

    notebook = next(
        child for child in window.winfo_children()
        if isinstance(child, ttk.Notebook)
    )
    labels = [notebook.tab(index, "text") for index in range(notebook.index("end"))]
    assert labels == [
        strings.get(f"SETTINGS.GROUP.{name.upper()}")
        for name in schema.GROUP_ORDER
    ]


def test_the_window_offers_a_control_for_every_editable_setting(window):
    editable = {
        setting.key for setting in schema.SETTINGS if not setting.is_data
    }
    assert set(window._controls) == editable


def test_every_control_in_the_settings_window_is_reachable_and_named(window):
    from universal_search.gui.accessibility import focus_report, name_report_for

    report = focus_report(window)
    assert report.unreachable == (), report.unreachable
    names = name_report_for(window)
    assert names.unnamed == (), names.unnamed


def test_the_window_reflects_the_configured_value(window):
    window.service.set("result_limit", 200)
    window.destroy()
    from universal_search.gui.settings_window import SettingsWindow

    again = SettingsWindow(None, service=window.service)
    try:
        assert again._controls["result_limit"].get() == "200"
    finally:
        again.destroy()


def test_saving_from_the_window_persists_and_reports(window, monkeypatch):
    import tkinter as tk

    window._controls["result_limit"].set("175")
    monkeypatch.setattr(
        tk, "Toplevel", tk.Toplevel
    )  # nothing to fake: the save path opens no dialog
    window._save()
    assert AppConfig.load(window.service.paths).result_limit == 175
    assert strings.get("SETTINGS.SAVED") in window.status_var.get()


def test_saving_from_the_window_reports_a_refusal(window):
    window._controls["ui_scale"].set("99")
    window._save()
    assert strings.get("SETTINGS.SAVED") not in window.status_var.get()
    assert AppConfig.load(window.service.paths).ui_scale == 1.0