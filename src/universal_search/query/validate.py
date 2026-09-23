"""Validation and normalization (spec 012, stage 3).

Two passes over the immutable AST:

1. **Field values** are checked and normalized into their final bound
   form — ``type:PDF`` becomes ``.pdf``, ``size:>10mb`` becomes
   ``(size, >, 10485760)``, ``after:2026-02-30`` raises with a readable
   message. Anything malformed is feedback, never a traceback.
2. **Structural policy**: SQL filters are only allowed as top-level
   conjuncts (never inside ``OR`` — the language cannot express
   text-OR-filter and silently rewriting it would lie about meaning),
   and ``-`` negation only applies to text. Both rejections explain
   themselves.

Validation never touches the database or the filesystem.
"""

import re
from datetime import datetime

from .errors import QueryError
from .lexer import WORD_RE
from .nodes import (
    SOURCE_KINDS,
    And,
    Expr,
    FieldRef,
    Filter,
    Not,
    Or,
)

# `type:` accepts a bare extension with or without the leading dot.
_TYPE_RE = re.compile(r"\.?([A-Za-z0-9]{1,16})\Z")

# `size:` an optional operator, a number and an optional unit.
# Units are binary: 1 KB = 1024 bytes (matches OS tooling on Windows).
_SIZE_RE = re.compile(
    r"(?P<op>>=|<=|>|<|=)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z]*)\Z"
)
_SIZE_UNITS = {"": 1, "b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}


def _normalize_filter(node: Filter) -> Filter:
    """Check one filter value and return its normalized node."""
    field = node.field
    raw = str(node.value).strip()
    if field == "type":
        match = _TYPE_RE.match(raw)
        if not match:
            raise QueryError(
                f"'type:' expects an extension like pdf or .pdf "
                f"(got '{raw}')"
            )
        return Filter("type", "=", f".{match.group(1).lower()}")
    if field == "source":
        lowered = raw.lower()
        if lowered not in SOURCE_KINDS:
            raise QueryError(
                f"'source:' must be one of {', '.join(SOURCE_KINDS)} "
                f"(got '{raw}')"
            )
        return Filter("source", "=", lowered)
    if field in ("after", "before"):
        try:
            datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            raise QueryError(
                f"'{field}:' expects a date like 2026-01-31 (got '{raw}')"
            ) from None
        # Date-only granularity: compared against the calendar date, so a
        # plain ISO date string is exactly the bound we need.
        return Filter(field, "=", raw)
    if field == "size":
        match = _SIZE_RE.match(raw)
        if not match:
            raise QueryError(
                f"'size:' expects e.g. size:>10MB, size:500KB or size:1000 "
                f"(got '{raw}')"
            )
        unit = match.group("unit").lower()
        if unit not in _SIZE_UNITS:
            raise QueryError(
                f"'size:' unknown unit '{match.group('unit')}' "
                "(use B, KB, MB or GB)"
            )
        operator = match.group("op") or ">="  # bare `size:10MB` = at least
        byte_count = float(match.group("num")) * _SIZE_UNITS[unit]
        return Filter("size", operator, int(round(byte_count)))
    raise QueryError(f"unknown field '{field}:'")  # pragma: no cover


def _normalize(node: Expr) -> Expr:
    if isinstance(node, Filter):
        return _normalize_filter(node)
    if isinstance(node, FieldRef):
        if not WORD_RE.search(node.value):
            raise QueryError(
                f"'{node.field}:' needs at least one word "
                f"(got '{node.value}')"
            )
        return node
    if isinstance(node, And):
        return And(tuple(_normalize(item) for item in node.items))
    if isinstance(node, Or):
        return Or(tuple(_normalize(item) for item in node.items))
    if isinstance(node, Not):
        return Not(_normalize(node.operand))
    return node  # Term, Phrase


def _check_placement(
    node: Expr, *, under_or: bool = False, under_not: bool = False
) -> None:
    if isinstance(node, Or):
        for item in node.items:
            _check_placement(item, under_or=True, under_not=under_not)
    elif isinstance(node, Not):
        if under_or:
            raise QueryError(
                "negation '-' cannot appear inside OR — exclusions apply "
                "to the whole query (put them at the top level)"
            )
        _check_placement(node.operand, under_or=under_or, under_not=True)
    elif isinstance(node, And):
        for item in node.items:
            _check_placement(item, under_or=under_or, under_not=under_not)
    elif isinstance(node, Filter):
        if under_or:
            raise QueryError(
                f"'{node.field}:' cannot appear inside OR — filters are "
                "always combined with AND (e.g. 'terms type:pdf')"
            )
        if under_not:
            raise QueryError(f"'{node.field}:' cannot be negated with '-'")
    # Term, Phrase, FieldRef: always allowed.


def validate(expr: Expr | None) -> Expr | None:
    """Check and normalize the AST (stage 3). Raises QueryError."""
    if expr is None:
        return None
    normalized = _normalize(expr)
    _check_placement(normalized)
    return normalized
