"""Result of attempting to read a document's text content.

The domain model distinguishes three states:

- ``text`` present, ``error`` absent: extraction succeeded.
- ``text`` absent, ``error`` absent: the format carries no extractable text.
- ``text`` absent, ``error`` present: extraction was attempted and failed.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    text: str | None = None
    error: str | None = None
