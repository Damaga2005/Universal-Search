"""Query AST and translation plan (spec 012, stages 2 and 4 output).

Frozen dataclasses: a query is parsed once, validated by an immutable
walk, translated to a plan and discarded. ``QueryPlan`` carries the four
things the engine needs — what to MATCH, how to exclude, what to rank
with and what to filter in SQL — so no layer ever rebuilds SQL from raw
user text (every value is a bound parameter).
"""

from dataclasses import dataclass

# Field routing (spec 012): `name`/`path` query FTS columns; everything
# else is a SQL-level filter. `source` values are the provider kinds.
TEXT_FIELDS = frozenset({"name", "path"})
FILTER_FIELDS = frozenset({"type", "source", "after", "before", "size"})
KNOWN_FIELDS = TEXT_FIELDS | FILTER_FIELDS
SOURCE_KINDS = ("local", "onedrive", "other")


@dataclass(frozen=True, slots=True)
class Term:
    """One bare word (already split on non-word characters by the lexer)."""

    text: str


@dataclass(frozen=True, slots=True)
class Phrase:
    """Quoted run; ``text`` is the raw inner string (escapes resolved)."""

    text: str


@dataclass(frozen=True, slots=True)
class FieldRef:
    """Text field query restricted to an FTS column (``name`` / ``path``)."""

    field: str
    value: str


@dataclass(frozen=True, slots=True)
class Filter:
    """SQL-level filter (``type``, ``source``, ``after``, ``before``, ``size``).

    ``op`` is one of ``=``, ``>``, ``>=``, ``<``, ``<=``; ``value`` is the
    normalized form produced by validation (extension with dot, lowercase
    source, ``YYYY-MM-DD`` date, or integer bytes for sizes).
    """

    field: str
    op: str
    value: str | int


@dataclass(frozen=True, slots=True)
class Not:
    """Exclusion; only allowed at the top level (validation enforces it)."""

    operand: "Expr"


@dataclass(frozen=True, slots=True)
class And:
    """Conjunction (implicit adjacency and explicit ``AND`` produce this)."""

    items: tuple["Expr", ...]


@dataclass(frozen=True, slots=True)
class Or:
    """Disjunction (explicit ``OR``)."""

    items: tuple["Expr", ...]


# Discriminated union of every node an expression can contain.
Expr = Term | Phrase | FieldRef | Filter | Not | And | Or


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Result of translation: ready-to-bind MATCH, ranking and SQL parts.

    ``fts`` is the positive part of the MATCH string (built exclusively
    from quoted ``\\w+`` runs and whitelisted column names, so FTS5
    operator injection is structurally impossible). ``negations`` is the
    parenthesized right side of the binary ``NOT`` operator, empty when
    the query excludes nothing. ``terms`` are the positive ranking terms,
    case-folded and de-duplicated. ``sql`` / ``sql_params`` are matching
    WHERE templates and their bound values, in order.
    """

    fts: str
    negations: str
    terms: tuple[str, ...]
    sql: tuple[str, ...]
    sql_params: tuple[object, ...]
