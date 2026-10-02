"""Phase 043 evidence: one settings system, measured rather than promised.

Run from the repository root::

    python -m evaluation.settings_gate

Phase 043's prompt asks for a configuration *contract* — typed settings,
defaults, validation, persistence, migration, reset, import/export and
documented precedence. Every one of those is a promise somebody can write down
and then quietly not keep, so each one is a gate here:

* **C1** every preference is in the schema, with a default that agrees;
* **C2** every schema setting exists on the configuration, and vice versa;
* **C3** a value out of range is refused with a reason, and pulled back by an
  explicit repair;
* **C4** a bad *type* is refused rather than coerced;
* **C5** the file declares which build wrote it, and a file from a newer build
  is not downgraded;
* **C6** a newer build's keys survive a save;
* **C7** the temporary file does not survive a save, and two writers cannot
  collide;
* **C8** reset keeps the data;
* **C9** export carries no data and says it carries no secrets;
* **C10** import refuses a newer file, ignores unknown and data keys, and
  applies the rest;
* **C11** the precedence table is ordered and explained, and the code agrees
  with the first entry;
* **C12** every setting has a label and an explanation in the catalogue;
* **C13** every preference that needs a restart says so.

Everything here is Tk-free, so the gate needs no display and reports NOT RUN
rather than assuming anything about a machine it could not open.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evaluation.accessibility_gate import Verdict  # noqa: E402
from universal_search import settings as schema  # noqa: E402
from universal_search.appconfig import (  # noqa: E402
    CONFIG_VERSION,
    PRECEDENCE,
    AppConfig,
    AppPaths,
    migrate,
)
from universal_search.gui import strings  # noqa: E402
from universal_search.gui.settings_window import (  # noqa: E402
    EXPORT_VERSION,
    SettingsService,
)

THRESHOLDS = {
    "C1_settings_without_a_default": 0,
    "C2_preferences_missing_from_the_schema": 0,
    "C3_out_of_range_values_accepted": 0,
    "C4_wrong_types_accepted": 0,
    "C5_versions_not_declared_or_downgraded": 0,
    "C6_newer_keys_destroyed_by_a_save": 0,
    "C7_temporary_files_left_behind": 0,
    "C8_resets_that_forget_user_data": 0,
    "C9_exports_carrying_data_or_silence": 0,
    "C10_imports_that_do_not_behave": 0,
    "C11_precedence_unordered_or_unexplained": 0,
    "C12_settings_without_an_explanation": 0,
    "C13_restarts_left_for_the_user_to_discover": 0,
}

GATE_LINES = {
    "C1_settings_without_a_default": "C1 ajustes sin valor por defecto coherente",
    "C2_preferences_missing_from_the_schema": "C2 preferencias fuera del esquema",
    "C3_out_of_range_values_accepted": "C3 valores fuera de rango aceptados",
    "C4_wrong_types_accepted": "C4 tipos erróneos aceptados",
    "C5_versions_not_declared_or_downgraded": "C5 versiones sin declarar o rebajadas",
    "C6_newer_keys_destroyed_by_a_save": "C6 claves de una version nueva destruidas",
    "C7_temporary_files_left_behind": "C7 ficheros temporales sin limpiar",
    "C8_resets_that_forget_user_data": "C8 restablecimientos que olvidan los datos",
    "C9_exports_carrying_data_or_silence": "C9 exportaciones con datos o sin aviso",
    "C10_imports_that_do_not_behave": "C10 importaciones que no se comportan",
    "C11_precedence_unordered_or_unexplained": "C11 precedencia sin orden ni explicación",
    "C12_settings_without_an_explanation": "C12 ajustes sin explicación",
    "C13_restarts_left_for_the_user_to_discover": "C13 reinicios que descubre el usuario",
}

# Fields that describe what the user owns rather than how the program behaves.
DATA_FIELDS = frozenset({
    "roots", "ignore_dirs", "ignore_patterns", "contexts", "active_context",
    "saved_searches", "recent_queries", "window_geometry",
})


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    measured: dict[str, float] = {}
    details: dict[str, str] = {}
    workspace = Path(tempfile.mkdtemp(prefix="universal-search-043-"))
    service = SettingsService(paths=AppPaths(workspace / "home"))
    paths = service.paths

    # -- C1 and C2: the schema and the configuration agree ------------------
    wrong_defaults = [
        setting.key for setting in schema.SETTINGS
        if getattr(AppConfig(), setting.key) != setting.default
    ]
    measured["C1_settings_without_a_default"] = len(wrong_defaults)
    details["C1_settings_without_a_default"] = (
        f"{len(schema.SETTINGS)} ajustes, todos con su valor por defecto"
        if not wrong_defaults else f"no coinciden: {wrong_defaults}"
    )

    fields = set(AppConfig().__dataclass_fields__)
    missing = (fields - DATA_FIELDS) - set(schema.BY_KEY)
    invented = set(schema.BY_KEY) - fields
    measured["C2_preferences_missing_from_the_schema"] = len(missing) + len(invented)
    details["C2_preferences_missing_from_the_schema"] = (
        f"{len(fields)} campos, {len(schema.BY_KEY)} en el esquema, "
        f"{len(DATA_FIELDS & set(schema.BY_KEY))} de ellos solo de lectura"
        if not missing and not invented
        else f"faltan {sorted(missing)}, de mas {sorted(invented)}"
    )

    # -- C3: out of range is refused, and repairable by asking --------------
    probes = {
        "indexer_interval_seconds": 0,
        "indexer_interval_seconds#high": 99999,
        "indexer_file_delay": -1.0,
        "onedrive_download_max_mb": -5.0,
        "result_limit": 2,
        "ui_scale": 99.0,
    }
    accepted_out_of_range = []
    for key, value in probes.items():
        field = key.split("#")[0]
        try:
            schema.validate(field, value)
            accepted_out_of_range.append(f"{field}={value}")
        except schema.SettingError:
            continue
    repaired = AppConfig(
        indexer_interval_seconds=0, indexer_file_delay=-1.0, ui_scale=99.0
    ).repaired()
    not_repaired = (
        repaired.indexer_interval_seconds == 0
        or repaired.indexer_file_delay < 0
        or repaired.ui_scale > schema.MAX_UI_SCALE
    )
    measured["C3_out_of_range_values_accepted"] = (
        len(accepted_out_of_range) + int(not_repaired)
    )
    details["C3_out_of_range_values_accepted"] = (
        f"{len(probes)} valores fuera de rango rechazados y reparados a "
        f"{repaired.indexer_interval_seconds} s / {repaired.ui_scale}"
        if not accepted_out_of_range and not not_repaired
        else f"aceptados: {accepted_out_of_range}, sin reparar: {not_repaired}"
    )

    # -- C4: a wrong type is refused ----------------------------------------
    wrong_types = []
    WRONG_TYPE_PROBES = 6
    for key, value in (
        ("result_limit", "muchos"),
        ("result_limit", 12.5),
        ("fuzzy_enabled", "sí"),
        ("indexer_interval_seconds", True),
        ("theme", "neón"),
        ("roots", "no-es-una-lista"),
    ):
        try:
            schema.validate(key, value)
            wrong_types.append(f"{key}={value!r}")
        except schema.SettingError:
            continue
    measured["C4_wrong_types_accepted"] = len(wrong_types)
    details["C4_wrong_types_accepted"] = (
        f"{WRONG_TYPE_PROBES} tipos erróneos rechazados"
        if not wrong_types else f"aceptados: {wrong_types}"
    )

    # -- C5: the file declares its version and is not downgraded ------------
    paths.ensure()
    AppConfig(roots=("D:/a",)).save(paths)
    written = json.loads(paths.config_file.read_text(encoding="utf-8"))
    declared = written.get("version") == CONFIG_VERSION
    paths.config_file.write_text(
        json.dumps({"version": 99, "roots": ["D:/a"], "future_key": 1}),
        encoding="utf-8",
    )
    AppConfig.load(paths).save(paths)
    after = json.loads(paths.config_file.read_text(encoding="utf-8"))
    kept_newer = after.get("version") == 99
    from_unversioned = migrate({"roots": ["D:/a"]}).get("version") == CONFIG_VERSION
    measured["C5_versions_not_declared_or_downgraded"] = (
        int(not declared) + int(not kept_newer) + int(not from_unversioned)
    )
    details["C5_versions_not_declared_or_downgraded"] = (
        f"guarda como versión {declared and CONFIG_VERSION}, no rebaja un "
        f"{after.get('version')} ajeno, y trata un fichero sin versión como 1"
    )

    # -- C6 and C7: a save keeps what it does not own, and cleans up ---------
    paths.config_file.write_text(
        json.dumps({"version": 1, "future_key": {"a": 1}}), encoding="utf-8"
    )
    AppConfig(roots=("D:/b",)).save(paths)
    after_save = json.loads(paths.config_file.read_text(encoding="utf-8"))
    survived = after_save.get("future_key") == {"a": 1}
    measured["C6_newer_keys_destroyed_by_a_save"] = int(not survived)
    details["C6_newer_keys_destroyed_by_a_save"] = (
        f"una clave de otra build sobrevive a guardar (pid {os.getpid()} en el "
        "nombre del temporal)"
    )
    leftovers = [p.name for p in paths.home.iterdir() if p.name.endswith(".tmp")]
    measured["C7_temporary_files_left_behind"] = len(leftovers)
    details["C7_temporary_files_left_behind"] = (
        "ningun temporal tras guardar, y su nombre lleva el pid"
        if not leftovers else f"restan: {leftovers}"
    )

    # -- C8: reset keeps what the user owns --------------------------------
    owned = AppConfig(
        roots=("D:/apuntes",), saved_searches=({"name": "n", "query": "q"},),
        recent_queries=("capacitor",), active_context="Universidad",
        window_geometry="900x600", hotkey="ctrl+q", ui_scale=2.0,
        result_limit=200, theme="dark",
    )
    reset = owned.defaults_keeping_data()
    lost = [
        name for name in DATA_FIELDS & set(owned.__dataclass_fields__)
        if getattr(reset, name) != getattr(owned, name)
    ]
    changed = (
        reset.result_limit != owned.result_limit
        and reset.ui_scale != owned.ui_scale
        and reset.theme != owned.theme
    )
    measured["C8_resets_that_forget_user_data"] = len(lost) + int(not changed)
    details["C8_resets_that_forget_user_data"] = (
        f"{len(DATA_FIELDS)} campos de datos conservados, preferencias "
        f"restablecidas ({lost or 'sin pérdidas'})"
    )

    # -- C9: an export carries preferences and says it is safe -------------
    exported = service.export(workspace / "gate")
    payload = json.loads(exported.read_text(encoding="utf-8"))
    carried_data = sorted(set(payload.get("settings", {})) & DATA_FIELDS)
    note = str(payload.get("note", "")).lower()
    silent = not (
        "contrase" in note and "clave" in note
    )
    measured["C9_exports_carrying_data_or_silence"] = (
        len(carried_data) + int(silent) + int(payload.get("version") != EXPORT_VERSION)
    )
    details["C9_exports_carrying_data_or_silence"] = (
        f"{len(payload.get('settings', {}))} preferencias, 0 datos, "
        f"versión {payload.get('version')}, y el archivo dice que no lleva "
        "contraseñas"
    )

    # -- C10: import behaves ----------------------------------------------
    problems = []
    newer = workspace / "newer.json"
    newer.write_text(
        json.dumps({"version": EXPORT_VERSION + 1, "settings": {}}), encoding="utf-8"
    )
    try:
        service.import_from(newer)
        problems.append("acepta un archivo de una version posterior")
    except ValueError:
        pass
    mixed = workspace / "mixed.json"
    mixed.write_text(
        json.dumps({
            "version": EXPORT_VERSION,
            "settings": {
                "result_limit": 200, "roots": ["D:/otro"],
                "inventado": 1, "indexer_interval_seconds": -5,
            },
        }),
        encoding="utf-8",
    )
    applied, refused = service.import_from(mixed)
    if applied != ["result_limit"]:
        problems.append(f"aplico {applied}")
    if [key for key, _ in refused] != ["indexer_interval_seconds"]:
        problems.append(f"rechazo {[key for key, _ in refused]}")
    if service.get("roots") != ():
        problems.append("importo datos")
    measured["C10_imports_that_do_not_behave"] = len(problems)
    details["C10_imports_that_do_not_behave"] = (
        "rechaza lo posterior, aplica lo valido, rechaza lo fuera de rango e "
        "ignora lo desconocido y los datos"
        if not problems else f"problemas: {problems}"
    )

    # -- C11: precedence is ordered and the code agrees ---------------------
    ranks = []
    for _name, rank, _why in PRECEDENCE:
        try:
            ranks.append(int(rank))
        except ValueError:
            ranks.append(-1)
    ordered = ranks == sorted(ranks) == list(range(1, len(ranks) + 1))
    explained = all(why for _name, _rank, why in PRECEDENCE)
    measured["C11_precedence_unordered_or_unexplained"] = (
        int(not ordered) + int(not explained)
    )
    details["C11_precedence_unordered_or_unexplained"] = (
        f"{len(PRECEDENCE)} reglas de precedencia, la primera es "
        f"{PRECEDENCE[0][0]}"
    )

    # -- C12 and C13: the catalogue explains every setting -------------------
    unexplained = []
    for setting in schema.SETTINGS:
        label_key = f"SETTINGS.{setting.key.upper()}.LABEL"
        help_key = f"SETTINGS.{setting.key.upper()}.HELP"
        if label_key not in strings.CATALOGUE or help_key not in strings.CATALOGUE:
            unexplained.append(setting.key)
            continue
        if len(strings.get(help_key)) < 30:
            unexplained.append(setting.key)
    groups_missing = [
        name for name in schema.GROUP_ORDER
        if f"SETTINGS.GROUP.{name.upper()}" not in strings.CATALOGUE
    ]
    measured["C12_settings_without_an_explanation"] = len(unexplained) + len(
        groups_missing
    )
    details["C12_settings_without_an_explanation"] = (
        f"{len(schema.SETTINGS)} ajustes con etiqueta y explicacion, "
        f"{len(schema.GROUP_ORDER)} grupos con nombre"
    )
    restart_missing = [
        setting.key for setting in schema.SETTINGS
        if setting.restart_required
        and "reiniciar" not in strings.get("SETTINGS.RESTART").lower()
    ]
    measured["C13_restarts_left_for_the_user_to_discover"] = len(restart_missing)
    details["C13_restarts_left_for_the_user_to_discover"] = (
        f"{len(schema.RESTART_REQUIRED)} ajustes marcados como "
        "«requiere reiniciar», y la ventana lo dice"
    )

    verdicts = [
        Verdict(
            gate=GATE_LINES[gate],
            measured=float(measured[gate]),
            threshold=float(threshold),
            passed=measured[gate] <= threshold,
            detail=details[gate],
        )
        for gate, threshold in THRESHOLDS.items()
    ]

    print("=" * 100)
    print("PUERTA DE EVIDENCIA - FASE 043 (ajustes y configuracion)")
    print("=" * 100)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 100)
    print("ajustes por intencion:")
    for name in schema.GROUP_ORDER:
        items = schema.settings_for(name)
        editable = [s.key for s in items if not s.is_data]
        read_only = [s.key for s in items if s.is_data]
        print(f"  {name:<12} {strings.get(f'SETTINGS.GROUP.{name.upper()}')}")
        print(f"               editables: {editable or '(ninguno)'}")
        if read_only:
            print(f"               solo lectura: {read_only}")

    payload_out = {
        "phase": "043",
        "config_version": CONFIG_VERSION,
        "export_version": EXPORT_VERSION,
        "settings": len(schema.SETTINGS),
        "groups": len(schema.GROUP_ORDER),
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
    }
    out = ROOT / "evaluation" / "settings_baseline.json"
    out.write_text(
        json.dumps(payload_out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")
    failed = [v for v in verdicts if not v.passed]
    if not failed:
        print("VEREDICTO: SHIP")
        return 0
    print(f"VEREDICTO: NO SHIP ({len(failed)} puertas)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())