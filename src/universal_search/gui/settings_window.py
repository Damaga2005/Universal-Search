"""Phase 043: one settings service, one settings window.

The service half is Tk-free and is what every test in this phase uses. It owns
the four operations phase 043 asks for and that had no home before:

* **read** a setting through the schema, so the window never touches a
  dataclass field directly and never invents a control for a value it does not
  know about;
* **set** one, validating it and saying why it refused;
* **reset** preferences to defaults without touching the data;
* **repair** a value that is out of range, which loading silently accepts.

And the two that the phase asked about and marked "where justified": **export**
and **import**, because moving a set of settings to a second machine is a real
thing people do, and because a feature you cannot leave is a feature you cannot
check. Both go through the same validation, and neither can carry a secret,
because there is no setting whose value is a secret -- and the exporter says so
in the file it writes.

The window half is a ``Toplevel`` defined only when Tk is importable, exactly as
``gui/control_center.py`` does, so this module can be imported and tested on a
machine with no display.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from universal_search import settings as schema
from universal_search.appconfig import AppConfig, AppPaths

log = logging.getLogger("universal_search.settings")

# Bumped when the exported file's shape changes. An export from a newer build
# is refused rather than half-read, for the same reason the config file is.
EXPORT_VERSION = 1
EXPORT_SUFFIX = ".universal-search-settings.json"

# Keys an exported file may contain. Anything else is a key this build does not
# recognise and is kept aside rather than imported, so importing a file from a
# newer build cannot silently apply settings the user did not choose.
EXPORTABLE = tuple(
    setting.key for setting in schema.SETTINGS if not setting.is_data
)


@dataclass(frozen=True, slots=True)
class SettingValue:
    """One setting as the window sees it: the schema plus what is set."""

    setting: schema.Setting
    value: object

    @property
    def key(self) -> str:
        return self.setting.key

    @property
    def editable(self) -> bool:
        """Data settings are shown, not edited here.

        The folders you index and the searches you saved belong to the control
        centre and the search window. A settings window that also let you edit
        them would be a second place to change them, which is the thing this
        phase exists to remove.
        """
        return not self.setting.is_data


@dataclass(frozen=True, slots=True)
class GroupedSettings:
    """The settings a window renders: groups in order, settings in order."""

    groups: tuple[tuple[str, tuple[SettingValue, ...]], ...]

    def editable(self, group: str) -> tuple[SettingValue, ...]:
        for name, items in self.groups:
            if name == group:
                return tuple(item for item in items if item.editable)
        return ()

    def data_summary(self, group: str) -> tuple[SettingValue, ...]:
        for name, items in self.groups:
            if name == group:
                return tuple(item for item in items if not item.editable)
        return ()


class SettingsService:
    """Read and change the user's settings, through the schema."""

    def __init__(self, paths: AppPaths | None = None, config: AppConfig | None = None) -> None:
        self.paths = paths or AppPaths.discover()
        self.config = config if config is not None else AppConfig.load(self.paths)

    # -- read ---------------------------------------------------------------

    def grouped(self) -> GroupedSettings:
        """Every setting, grouped the way a person would look for it."""
        groups: list[tuple[str, tuple[SettingValue, ...]]] = []
        for name in schema.GROUP_ORDER:
            items = tuple(
                SettingValue(setting, getattr(self.config, setting.key))
                for setting in schema.settings_for(name)
            )
            if items:
                groups.append((name, items))
        return GroupedSettings(tuple(groups))

    def get(self, key: str) -> object:
        if key not in schema.BY_KEY:
            raise schema.SettingError(f"ajuste desconocido: {key!r}")
        return getattr(self.config, key)

    def problems(self) -> list[tuple[str, str]]:
        """Values that loaded but the schema would refuse."""
        return self.config.problems()

    # -- write --------------------------------------------------------------

    def set(self, key: str, value) -> AppConfig:
        """Validate and store one setting. Raises :class:`SettingError`."""
        checked = schema.validate(key, value)
        updated = type(self.config)(**{**self._as_dict(), key: checked})
        self._save(updated)
        return updated

    def set_many(self, changes: dict) -> tuple[AppConfig, list[tuple[str, str]]]:
        """Apply a group of changes, refusing only the invalid ones.

        A settings window saves every control at once, and refusing the whole
        save because one spin box holds a number out of range would make the
        window feel broken. The accepted values go in; the refused ones come
        back with a reason.
        """
        accepted, refused = schema.validate_all(changes)
        if accepted:
            self._save(type(self.config)(**{**self._as_dict(), **accepted}))
        return self.config, refused

    def reset(self) -> AppConfig:
        """Every preference back to its default, keeping the user's data."""
        updated = self.config.defaults_keeping_data()
        self._save(updated)
        return updated

    def repair(self) -> tuple[AppConfig, list[tuple[str, str]]]:
        """Pull every out-of-range value back inside its bounds.

        This one is a real decision, so it is a method the user calls and not
        something the loader does behind their back.
        """
        before = self.problems()
        updated = self.config.repaired()
        if updated != self.config:
            self._save(updated)
        return updated, before

    # -- import and export --------------------------------------------------

    def export(self, destination: Path) -> Path:
        """Write the preferences to a file a person can copy to another machine."""
        destination = Path(destination)
        if destination.suffix != ".json":
            destination = destination.with_name(destination.name + EXPORT_SUFFIX)
        payload = {
            "version": EXPORT_VERSION,
            "note": (
                "Preferencias de Universal Search. Sin contrasenas, sin "
                "claves y sin nada que se haya transmission: el archivo no "
                "contiene ningun dato de los documentos indexados."
            ),
            "settings": {key: getattr(self.config, key) for key in EXPORTABLE},
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return destination

    def import_from(self, source: Path) -> tuple[list[str], list[tuple[str, str]]]:
        """Apply the preferences in an exported file.

        Returns the keys applied and the ones refused. Data settings are not in
        an export and are not applied, so importing can never replace the
        folders a user indexes.
        """
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("el archivo no es un conjunto de ajustes")
        version = payload.get("version")
        if isinstance(version, int) and version > EXPORT_VERSION:
            raise ValueError(
                f"el archivo es de una versión posterior ({version}) y este "
                f"programa entiende hasta la {EXPORT_VERSION}"
            )
        raw = payload.get("settings")
        if not isinstance(raw, dict):
            raise ValueError("el archivo no contiene ajustes")
        wanted = {key: raw[key] for key in EXPORTABLE if key in raw}
        applied, refused = schema.validate_all(wanted)
        if applied:
            self._save(type(self.config)(**{**self._as_dict(), **applied}))
        return sorted(applied), refused

    # -- internals ----------------------------------------------------------

    def _as_dict(self) -> dict:
        return {
            name: getattr(self.config, name)
            for name in self.config.__dataclass_fields__
        }

    def _save(self, config: AppConfig) -> None:
        try:
            config.save(self.paths)
        except Exception:
            log.exception("could not persist settings")
            raise
        self.config = config


try:  # pragma: no cover - Tk is present on the supported platform
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    # Tk-free either way: the accessibility helpers have no display of their
    # own, and the window half needs them to declare every control's name.
    from universal_search.gui import accessibility
except ImportError:  # pragma: no cover - a build without Tk
    tk = None  # type: ignore[assignment]

    ttk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    accessibility = None  # type: ignore[assignment]


def _label_key(key: str) -> str:
    # Upper case because the phase 039 catalogue rule is that a key is an
    # identifier, and `SETTINGS.roots.LABEL` is not one.
    return f"SETTINGS.{key.upper()}.LABEL"


def _help_key(key: str) -> str:
    return f"SETTINGS.{key.upper()}.HELP"


def _group_key(group: str) -> str:
    return f"SETTINGS.GROUP.{group.upper()}"


if tk is not None:

    class SettingsWindow(tk.Toplevel):
        """The one place a user changes a preference.

        Grouped by intent and not by module, because the module layout is an
        implementation detail and "where do I turn this off" is a question a
        person asks about their own intent. Every advanced control carries its
        explanation underneath, and every control that needs a restart says so
        rather than letting the user find out.
        """

        def __init__(self, parent=None, *, service: SettingsService) -> None:
            # `parent` first and `service` keyword-only, the same shape as
            # `ControlCenterWindow`: Tk takes the master positionally and a
            # caller that passes a service positionally would be handed to Tk
            # as a widget.
            super().__init__(parent)
            self.service = service
            self._controls: dict[str, tk.Variable] = {}
            from universal_search.gui import strings
            from universal_search.gui import theme as theme_module

            self.strings = strings
            self.theme = theme_module.resolve(
                getattr(service.config, "theme", "system")
            )
            scale = theme_module.clamp_scale(
                getattr(service.config, "ui_scale", 1.0)
            )
            self.spacing = theme_module.spacing(scale)
            self.fonts = theme_module.fonts(scale)
            self.title(strings.get("SETTINGS.TITLE"))
            self.geometry("720x640")
            self.configure(background=self.theme.background)

            notebook = ttk.Notebook(self)
            notebook.pack(
                fill="both", expand=True,
                padx=self.spacing.pad, pady=self.spacing.pad,
            )
            for group, _ in service.grouped().groups:
                frame = ttk.Frame(notebook, padding=self.spacing.gap)
                notebook.add(frame, text=strings.get(_group_key(group)))
                self._build_group(frame, group)
            self._build_footer()

            self.protocol("WM_DELETE_WINDOW", self.destroy)

        # -- construction -------------------------------------------------

        def _build_group(self, frame, group: str) -> None:
            grouped = self.service.grouped()
            for item in grouped.editable(group):
                self._build_row(frame, item)
            for item in grouped.data_summary(group):
                self._build_readonly(frame, item)

        def _build_row(self, frame, item) -> None:
            setting = item.setting
            row = ttk.Frame(frame, padding=(0, self.spacing.tight))
            row.pack(fill="x")
            variable = self._variable_for(setting, item.value)
            self._controls[setting.key] = variable
            control = self._control_for(setting, variable)
            # Tk cannot say which Label belongs to which control -- three
            # labels share this frame -- so the association is declared, which
            # is what phase 039 established and what `gui.accessibility` fails
            # without. A spin box showing "175" on its own tells a screen
            # reader nothing.
            accessibility.declare_name(control, self.strings.get(_label_key(setting.key)))
            text = ttk.Label(
                row, text=self.strings.get(_label_key(setting.key)),
                font=self.fonts["body"],
            )
            text.pack(side="left")
            control.pack(side="right")
            help_text = self.strings.get(_help_key(setting.key))
            if setting.restart_required:
                help_text += " " + self.strings.get("SETTINGS.RESTART")
            ttk.Label(
                frame, text=help_text, foreground=self.theme.muted,
                font=self.fonts["body"], wraplength=620, justify="left",
            ).pack(fill="x", padx=(self.spacing.gap, 0))

        def _build_readonly(self, frame, item) -> None:
            ttk.Label(
                frame,
                text=f"{self.strings.get(_label_key(item.key))}: "
                     f"{_readable(item.value)}",
                foreground=self.theme.muted, font=self.fonts["body"],
                wraplength=620, justify="left",
            ).pack(fill="x", pady=self.spacing.tight)

        def _variable_for(self, setting: schema.Setting, value):
            if setting.kind == schema.BOOL:
                return tk.BooleanVar(value=bool(value))
            return tk.StringVar(value=_readable(value))

        def _control_for(self, setting: schema.Setting, variable):
            if setting.kind == schema.BOOL:
                return ttk.Checkbutton(variable=variable, takefocus=True)
            if setting.kind == schema.CHOICE:
                return ttk.Combobox(
                    state="readonly", textvariable=variable,
                    values=list(setting.choices), width=16, takefocus=True,
                )
            if setting.kind in {schema.INT, schema.FLOAT}:
                return ttk.Spinbox(
                    textvariable=variable, width=8, takefocus=True,
                    from_=setting.minimum if setting.minimum is not None else 0,
                    to=setting.maximum if setting.maximum is not None else 100,
                    increment=1 if setting.kind == schema.INT else 0.1,
                )
            return ttk.Entry(textvariable=variable, width=20, takefocus=True)

        def _build_footer(self) -> None:
            bar = ttk.Frame(self, padding=self.spacing.pad)
            bar.pack(fill="x", side="bottom")
            for label, command in (
                ("SETTINGS.SAVE", self._save),
                ("SETTINGS.RESET", self._reset),
                ("SETTINGS.REPAIR", self._repair),
                ("SETTINGS.EXPORT", self._export),
                ("SETTINGS.IMPORT", self._import),
                ("SETTINGS.CLOSE", self.destroy),
            ):
                button = ttk.Button(
                    bar, text=self.strings.get(label), command=command,
                    # Phase 039's rule, and it applies here too: never rely on
                    # a style default for whether a control is reachable.
                    takefocus=True,
                )
                accessibility.declare_name(button, self.strings.get(label))
                button.pack(side="left", padx=(0, self.spacing.tight))
            self.status_var = tk.StringVar(value="")
            ttk.Label(
                bar, textvariable=self.status_var,
                foreground=self.theme.muted, font=self.fonts["body"],
            ).pack(side="right")

        # -- actions ------------------------------------------------------

        def _changes(self) -> dict:
            changes: dict = {}
            for key, variable in self._controls.items():
                setting = schema.BY_KEY[key]
                changes[key] = _coerce(setting, variable)
            return changes

        def _save(self) -> None:
            _, refused = self.service.set_many(self._changes())
            if refused:
                for key, reason in refused:
                    log.warning("setting %s refused: %s", key, reason)
                self._say(self.strings.get("SETTINGS.REFUSED", count=len(refused)))
            else:
                self._say(self.strings.get("SETTINGS.SAVED"))

        def _reset(self) -> None:
            """Destructive, so it asks, and it says what it will not touch.

            The confirmation names the exception rather than hiding it: the
            folders being indexed and the saved searches survive a reset, and a
            user who assumed otherwise would be right to hesitate.
            """
            if not messagebox.askyesno(
                self.strings.get("SETTINGS.RESET_TITLE"),
                self.strings.get("SETTINGS.RESET_CONFIRM"),
            ):
                return
            self.service.reset()
            self.destroy()
            self._say(self.strings.get("SETTINGS.RESET_DONE"))
            messagebox.showinfo(
                self.strings.get("SETTINGS.TITLE"),
                self.strings.get("SETTINGS.RESET_APPLY"),
            )

        def _repair(self) -> None:
            """Not destructive -- it only pulls values back in range -- but it
            changes what the application does, so it reports what it changed."""
            _, before = self.service.repair()
            if before:
                self._say(self.strings.get("SETTINGS.REPAIRED", count=len(before)))
                self.destroy()
            else:
                self._say(self.strings.get("SETTINGS.NOTHING_TO_REPAIR"))

        def _export(self) -> None:
            chosen = filedialog.asksaveasfilename(
                defaultextension=EXPORT_SUFFIX, filetypes=[("JSON", "*.json")]
            )
            if not chosen:
                return
            path = self.service.export(Path(chosen))
            self._say(self.strings.get("SETTINGS.EXPORTED", path=str(path)))

        def _import(self) -> None:
            chosen = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
            if not chosen:
                return
            try:
                applied, refused = self.service.import_from(Path(chosen))
            except (OSError, ValueError) as exc:
                self._say(
                    self.strings.get("SETTINGS.IMPORT_FAILED", reason=str(exc))
                )
                return
            self._say(
                self.strings.get(
                    "SETTINGS.IMPORTED", count=len(applied), refused=len(refused)
                )
            )
            self.destroy()

        # -- helpers ------------------------------------------------------

        def _say(self, message: str) -> None:
            self.status_var.set(message)


def _readable(value) -> str:
    """A value as a person would read it, for a summary or a display field."""
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) if value else "(ninguno)"
    return "" if value is None else str(value)


def _coerce(setting: schema.Setting, variable) -> object:
    """What a Tk control holds, back into the type the schema wants.

    A Spinbox and a Checkbutton both hand back strings from some platforms and
    typed values from others, so the conversion is explicit and it tells the
    schema rather than guessing: an unparsable number arrives as the string
    itself and is refused with a reason the user can read.
    """
    value = variable.get()
    if setting.kind == schema.BOOL:
        return bool(value)
    if setting.kind in {schema.INT, schema.FLOAT}:
        text = str(value).strip()
        try:
            number = float(text)
        except ValueError:
            return text
        return int(number) if setting.kind == schema.INT else number
    return value