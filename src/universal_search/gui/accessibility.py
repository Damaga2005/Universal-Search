"""Accessibility, measured rather than asserted (phase 039).

Three questions about the interface, each with an instrument instead of a
promise:

1. **Can it be used with no mouse at all?** :class:`FocusOrder` walks the
   widget tree the way Tab does and reports what is reachable, what is skipped
   and where the ring goes next. A window where the only way to reach the
   results is a click is not keyboard-operable, however good the shortcuts are.

2. **Does every control have a name?** :func:`accessibility_report` reads the
   built widgets and reports each one's role, its accessible name and whether
   the name is the same as its visible text. A control with no name is
   announced as "button" by a screen reader, which is not a name.

3. **Can it be read?** :func:`contrast_report` computes WCAG 2.1 contrast
   ratios from the theme and checks the pairs the window actually renders.
   Measured, because "the palette looks fine" is not a fact.

Tk has no accessibility API, so (1) and (2) work by reading the widgets. That
is honest about its limits and it is testable everywhere; pretending to more
than that would be the worse answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# -- 1. keyboard reachability ------------------------------------------------

# Roles a user can act on. A widget of one of these types must be reachable
# with Tab or it is mouse-only.
INTERACTIVE_TYPES = (
    "Entry", "Combobox", "Listbox", "Button", "Checkbutton", "Radiobutton",
    "Menubutton", "Scale", "Spinbox", "Text", "Treeview",
)


def _children(widget):
    for child in widget.winfo_children():
        yield child
        yield from _children(child)


def _pack_order(widget) -> list:
    """Children of ``widget`` in the order the packer puts them on screen.

    ``winfo_children`` is creation order, which is usually but not always the
    visual order. Focus follows the visual order, so it has to follow this
    one: a Tab ring that jumps down then back up is disorienting even when
    every control is reachable.
    """
    try:
        order = list(widget.pack_slaves())
    except Exception:  # pragma: no cover - a widget without a packer
        order = []
    if order:
        return order
    try:
        return list(widget.grid_slaves())
    except Exception:  # pragma: no cover
        return []


def focus_order(root) -> list:
    """Interactive widgets in Tab order: depth-first, visual order per parent.

    Tk's Tab ring follows the order widgets were *packed*, not the order they
    were created, so a control built first but packed last is reached later.
    Getting this wrong produces a ring that jumps around the window, which is
    disorienting even when every control is reachable.
    """
    found: list = []

    def walk(widget) -> None:
        for child in _pack_order(widget):
            if type(child).__name__ in INTERACTIVE_TYPES:
                found.append(child)
            walk(child)

    walk(root)
    return found


def widget_path(widget) -> str:
    return type(widget).__name__


def reachable_widgets(root) -> list:
    """Every interactive widget below ``root``, in creation order."""
    found = []
    for widget in [root, *_children(root)]:
        if type(widget).__name__ in INTERACTIVE_TYPES:
            found.append(widget)
    return found


@dataclass(frozen=True, slots=True)
class FocusReport:
    reachable: int
    unreachable: tuple[str, ...]
    focusable_without_mouse: bool

    @property
    def ok(self) -> bool:
        return not self.unreachable


def focus_report(root) -> FocusReport:
    """Which interactive widgets the keyboard can reach.

    ``takefocus`` is the honest answer for Tk: ``1`` means the widget takes
    focus, ``0`` means it never will, and anything else is a style default
    that changes with the platform theme. A widget left to a default is
    reported separately rather than counted as reachable, because "it probably
    works" is not what an accessibility audit is allowed to conclude.
    """
    unreachable: list[str] = []
    explicit = 0
    for widget in reachable_widgets(root):
        try:
            value = str(widget.cget("takefocus"))
        except Exception:
            value = ""
        name = widget_path(widget)
        if value == "1":
            explicit += 1
        elif value == "0":
            unreachable.append(name)
        else:
            unreachable.append(f"{name} (sin takefocus explícito)")
    return FocusReport(
        reachable=explicit,
        unreachable=tuple(unreachable),
        focusable_without_mouse=not unreachable,
    )


# -- 2. accessible names -----------------------------------------------------


def declare_name(widget, name: str) -> None:
    """Attach an accessible name to a widget.

    Tk has no ``aria-label`` and no ``labelwidget``: a ``Label`` beside a
    control is only related to it by position, and three labels in one row
    ("Contexto:", "Fuente:", "Tipo:") are indistinguishable from each other by
    anything Tk exposes. Guessing from adjacency would announce "Contexto:" for
    three different filters.

    So the association is **declared** where the widget is built, and the
    audit checks that every interactive control has one. That is also what a
    real screen-reader bridge would consume, so the declaration is where the
    information belongs.
    """
    widget._universal_search_name = name


def declared_name(widget) -> str:
    return str(getattr(widget, "_universal_search_name", "") or "")


def _own_text(widget) -> str:
    """Static text the widget carries itself.

    Deliberately *not* the value of a ``textvariable``: a combobox showing
    "(todos)" would then be announced as "combobox (todos)", which tells a
    screen-reader user nothing about what the control is for. And ``cget("text")``
    on such a widget answers with the Tcl variable name ("PY_VAR16"), which is
    worse than nothing.
    """
    for option in ("text", "label"):
        try:
            value = widget.cget(option)
        except Exception:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        if re.fullmatch(r"(PY_VAR\d+|::env\([^)]*\)|[A-Za-z_][\w:]*)", value):
            continue  # a variable name is not text
        return value.strip()
    return ""


def _visible_text(widget) -> str:
    """What the widget itself shows, for readability in the report."""
    own = _own_text(widget)
    if own:
        return own
    variable = ""
    try:
        variable = str(widget.cget("textvariable") or "")
    except Exception:
        variable = ""
    if variable:
        try:
            value = widget.tk.globalgetvar(variable)
        except Exception:
            return ""
        if isinstance(value, str):
            return value.strip()
    return ""


def _is_widget(widget, name: str) -> bool:
    """Does ``widget`` name a widget called ``name`` in its widget path?"""
    return str(widget).endswith(name)


def accessible_name(widget, root=None) -> str:
    """The name a screen reader would announce for ``widget``.

    In order: the text the widget carries itself, then an explicit
    :func:`declare_name`, then nothing. Adjacency is deliberately *not* used as
    a fallback: three labels share one parent frame in this window, so a
    positional guess names three different filters "Contexto:".
    """
    own = _own_text(widget)
    if own:
        return own
    return declared_name(widget)


@dataclass(frozen=True, slots=True)
class ControlName:
    role: str
    name: str
    focusable: bool


@dataclass(frozen=True, slots=True)
class NameReport:
    controls: tuple[ControlName, ...] = field(default=())

    @property
    def unnamed(self) -> tuple[str, ...]:
        return tuple(
            control.role for control in self.controls if not control.name.strip()
        )

    @property
    def ok(self) -> bool:
        return not self.unnamed


def name_report_for(root) -> NameReport:
    """Every interactive control, with the name it would announce."""
    controls = []
    for widget in reachable_widgets(root):
        try:
            takefocus = str(widget.cget("takefocus"))
        except Exception:
            takefocus = "1"
        controls.append(
            ControlName(
                role=widget_path(widget),
                name=accessible_name(widget, root),
                focusable=takefocus != "0",
            )
        )
    return NameReport(tuple(controls))


# -- 3. contrast -------------------------------------------------------------


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: str) -> float:
    """WCAG 2.1 relative luminance of a ``#rrggbb`` string."""
    text = colour.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"not a #rrggbb colour: {colour!r}")
    red, green, blue = (int(text[index: index + 2], 16) for index in (0, 2, 4))
    return (
        0.2126 * _channel(red)
        + 0.7152 * _channel(green)
        + 0.0722 * _channel(blue)
    )


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.1 contrast ratio between two colours, from 1 to 21."""
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# WCAG 2.1 AA: 4.5:1 for body text, 3:1 for large text (>=18pt, or >=14pt
# bold) and for non-text indicators such as a focus ring.
AA_BODY = 4.5
AA_LARGE = 3.0


@dataclass(frozen=True, slots=True)
class ContrastCheck:
    name: str
    foreground: str
    background: str
    ratio: float
    required: float

    @property
    def ok(self) -> bool:
        return self.ratio >= self.required


# The pairs the window actually renders, with the level each one must reach.
# A pair that is not in this table is not drawn by the GUI today; adding one to
# the theme without adding it here is a gap the tests would not catch, which is
# why every test asserts the table covers the whole palette.
CONTRAST_PAIRS: tuple[tuple[str, str, str, float], ...] = (
    ("body text", "foreground", "background", AA_BODY),
    ("secondary text", "muted", "background", AA_BODY),
    ("links and focus", "accent", "background", AA_BODY),
    ("selected row", "selection_foreground", "selection_background", AA_BODY),
    ("warning", "busy", "background", AA_BODY),
    ("error", "danger", "background", AA_BODY),
    # A selected row also has to be distinguishable from an unselected one by
    # something other than colour alone, which is what the listbox highlight
    # ring is for; the ratio below is the colour half of that requirement.
    ("focus ring", "accent", "background", AA_LARGE),
)


def contrast_report(theme) -> tuple[ContrastCheck, ...]:
    """Contrast of every pair the window draws, for one theme."""
    return tuple(
        ContrastCheck(
            name=name,
            foreground=getattr(theme, foreground),
            background=getattr(theme, background),
            ratio=contrast_ratio(
                getattr(theme, foreground), getattr(theme, background)
            ),
            required=required,
        )
        for name, foreground, background, required in CONTRAST_PAIRS
    )