"""Central theme and scaling definitions (spec 017).

Every colour, font size and spacing constant the window uses lives here.
Scattering `#666` through widget code is how a dark mode ends up with
unreadable text in three places; a single frozen record with one place to
change is the only way this stays maintainable.

Pure data and arithmetic: nothing in this module touches Tk, so the theme
contract is testable without a display.
"""

from dataclasses import dataclass, replace

THEME_CHOICES = ("system", "light", "dark")

# Base UI font family. Segoe UI ships with Windows; Tk falls back cleanly.
UI_FONT = "Segoe UI"
MONO_FONT = "Consolas"

# Entry font is larger than the rest on purpose: it is the one control the
# user looks at while typing.
ENTRY_FONT_SIZE = 14
BODY_FONT_SIZE = 10

# Row text budget: enough to recognise a document, not enough to wrap a
# whole sentence into a listbox line.
SNIPPET_CHARS = 100
PATH_PARTS = 2


@dataclass(frozen=True, slots=True)
class Theme:
    """One complete palette plus its type scale."""

    name: str
    dark: bool
    background: str
    surface: str
    foreground: str
    muted: str
    accent: str
    selection_background: str
    selection_foreground: str
    busy: str
    danger: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "dark": self.dark,
            "background": self.background,
            "foreground": self.foreground,
            "muted": self.muted,
            "accent": self.accent,
        }


LIGHT = Theme(
    name="light",
    dark=False,
    background="#ffffff",
    surface="#f4f5f7",
    foreground="#1b1b1b",
    muted="#5d6470",
    accent="#1a5fb4",
    selection_background="#cfe3ff",
    selection_foreground="#0b1b33",
    busy="#8a6d00",
    danger="#a51d2d",
)

DARK = Theme(
    name="dark",
    dark=True,
    background="#1e1f22",
    surface="#2b2d30",
    foreground="#e8e8e8",
    muted="#a6adb8",
    accent="#7aa7e8",
    selection_background="#2f4f7f",
    selection_foreground="#ffffff",
    busy="#d8b44a",
    danger="#f28b82",
)

THEMES = {"light": LIGHT, "dark": DARK}


def system_prefers_dark() -> bool:
    """Best-effort read of the Windows dark preference.

    Registry first (it is what Explorer uses), then the ttk "vista" theme
    as a hint. Returns False when nothing can be determined, because light
    is the safer default for a search box read in daylight.
    """
    import sys

    if sys.platform != "win32":
        return False
    try:  # pragma: no cover - Windows only
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            0,
            winreg.KEY_READ,
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


def resolve(name: str, *, prefer_dark: bool | None = None) -> Theme:
    """The theme for a configuration value, never None and never invalid."""
    if name == "light":
        return LIGHT
    if name == "dark":
        return DARK
    dark = system_prefers_dark() if prefer_dark is None else prefer_dark
    return DARK if dark else LIGHT


def clamp_scale(value: float) -> float:
    """Keep the UI scale inside what a desktop window can honour."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.75, min(number, 2.5))


def scaled_size(size: int, scale: float) -> int:
    """Font size in points for a given UI scale (never below 8pt)."""
    return max(8, int(round(size * clamp_scale(scale))))


def fonts(scale: float = 1.0) -> dict[str, tuple[str, int]]:
    """The type scale for this configuration."""
    factor = clamp_scale(scale)
    return {
        "entry": (UI_FONT, scaled_size(ENTRY_FONT_SIZE, factor)),
        "body": (UI_FONT, scaled_size(BODY_FONT_SIZE, factor)),
        "mono": (MONO_FONT, scaled_size(BODY_FONT_SIZE, factor)),
    }


def with_accent(theme: Theme, accent: str) -> Theme:
    """Derived palette (used by tests and by future accent choices)."""
    return replace(theme, accent=accent)
