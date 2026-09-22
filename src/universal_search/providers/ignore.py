"""Explicit, testable ignore rules applied during filesystem discovery."""

import fnmatch
from dataclasses import dataclass


DEFAULT_IGNORED_DIRECTORIES = frozenset({
    # version control
    ".git", ".hg", ".svn",
    # dependency / build caches
    "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".cache", ".gradle",
    # Windows noise that is irrelevant for search
    "AppData", "$RECYCLE.BIN", "System Volume Information",
})

DEFAULT_IGNORED_PATTERNS = (
    "*.tmp",
    "*.bak",
    "~$*",          # Microsoft Office lock files
    "Thumbs.db",
    "desktop.ini",
)


@dataclass(frozen=True, slots=True)
class IgnoreRules:
    """Directory names (case-insensitive) and file glob patterns to skip."""

    directories: frozenset[str]
    patterns: tuple[str, ...]

    @classmethod
    def defaults(
        cls,
        *,
        directories: tuple[str, ...] | frozenset[str] = (),
        patterns: tuple[str, ...] = (),
    ) -> "IgnoreRules":
        """Built-in rules plus user-configured extras."""
        return cls(
            directories=frozenset(d.lower() for d in DEFAULT_IGNORED_DIRECTORIES)
            | frozenset(d.lower() for d in directories),
            patterns=tuple(DEFAULT_IGNORED_PATTERNS) + tuple(patterns),
        )

    @classmethod
    def only(
        cls,
        *,
        directories: tuple[str, ...] | frozenset[str] = (),
        patterns: tuple[str, ...] = (),
    ) -> "IgnoreRules":
        """No built-in rules; only the supplied extras."""
        return cls(
            directories=frozenset(d.lower() for d in directories),
            patterns=tuple(patterns),
        )

    def ignores_directory(self, name: str) -> bool:
        return name.lower() in self.directories

    def ignores_file(self, name: str) -> bool:
        # fnmatch normalises case on Windows, matching platform expectations.
        return any(fnmatch.fnmatch(name, pattern) for pattern in self.patterns)
