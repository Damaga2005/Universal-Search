"""AST to MATCH / ranking / SQL parts (spec 012, stage 4).

Safety invariant: the FTS5 ``MATCH`` string is assembled **exclusively**
from quoted ``\\w+`` runs, whitelisted column names and the operator
keywords ``AND``/``OR``/``NOT``/``(`/``)``/``:`` — user text can never
introduce an FTS operator, string delimiter or parenthesis, so operator
injection is structurally impossible (the bound SQL parameters in
``QueryPlan`` keep the database side equally parameterized).

Negation: FTS5 has only the *binary* ``NOT`` (probed empirically, spec
012), so every top-level ``-x`` is hoisted into one right-hand side:
``(positives) NOT (neg1 OR neg2)``. Validation has already guaranteed
negations and filters only occur where this rewrite preserves meaning.
"""

from .lexer import WORD_RE
from .nodes import (
    TEXT_FIELDS,
    And,
    Expr,
    FieldRef,
    Filter,
    Not,
    Or,
    Phrase,
    QueryPlan,
    Term,
)


def _join(node_type, items: list[Expr]) -> Expr | None:
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return node_type(tuple(items))


def _split(expr: Expr | None) -> tuple[Expr | None, list[Expr]]:
    """Positive part + hoisted negation operands (top-level NOT only)."""
    if expr is None:
        return None, []
    if isinstance(expr, Not):
        return None, [expr.operand]
    if isinstance(expr, And):
        positives: list[Expr] = []
        negations: list[Expr] = []
        for item in expr.items:
            positive, hoisted = _split(item)
            if positive is not None:
                positives.append(positive)
            negations.extend(hoisted)
        return _join(And, positives), negations
    return expr, []


def _fts(expr: Expr | None) -> str:
    """Positive MATCH fragment; ``""`` when the node contributes nothing."""
    if expr is None:
        return ""
    if isinstance(expr, Term):
        return f'"{expr.text}"'
    if isinstance(expr, Phrase):
        words = WORD_RE.findall(expr.text)
        return f'"{" ".join(words)}"' if words else ""
    if isinstance(expr, FieldRef):
        # Whitelist: only `name` and `path` reach here (parser routing).
        if expr.field not in TEXT_FIELDS:
            return ""
        words = WORD_RE.findall(expr.value)
        return f'{expr.field} : "{" ".join(words)}"' if words else ""
    if isinstance(expr, (Filter, Not)):
        return ""  # filters are SQL-side; negations have their own side
    parts = [part for part in (_fts(item) for item in expr.items) if part]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    joiner = " AND " if isinstance(expr, And) else " OR "
    return "(" + joiner.join(parts) + ")"


def _negations(negation_exprs: list[Expr]) -> str:
    parts = [part for part in (_fts(expr) for expr in negation_exprs) if part]
    if not parts:
        return ""
    if len(parts) == 1:
        return f"({parts[0]})"
    return "(" + " OR ".join(parts) + ")"


def _terms(expr: Expr | None) -> tuple[str, ...]:
    """Positive ranking terms: case-folded, first occurrence wins."""
    if expr is None or isinstance(expr, Filter):
        return ()
    if isinstance(expr, Not):
        return ()  # excluded words must never influence scoring
    if isinstance(expr, Term):
        return (expr.text.casefold(),)
    if isinstance(expr, Phrase):
        return tuple(word.casefold() for word in WORD_RE.findall(expr.text))
    if isinstance(expr, FieldRef):
        return tuple(word.casefold() for word in WORD_RE.findall(expr.value))
    # And / Or: merge children, de-duplicating across the whole query.
    seen: set[str] = set()
    merged: list[str] = []
    for item in expr.items:
        for word in _terms(item):
            if word not in seen:
                seen.add(word)
                merged.append(word)
    return tuple(merged)


# SQL templates for the validated filters. `d.` is the documents alias
# both in the pool subquery and in the filter-only query, so the same
# templates work in either context. Every user value is a bound ?.
_FILTER_SQL = {
    "type": "d.extension = ?",
    "source": "d.source = ?",
    "after": "substr(d.modified_at, 1, 10) > ?",  # strictly later date
    "before": "substr(d.modified_at, 1, 10) < ?",  # strictly earlier date
}


def _sql(expr: Expr | None) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []

    def walk(node: Expr | None) -> None:
        if isinstance(node, Filter):
            if node.field == "size":
                # `op` comes from the validated regex whitelist (one of
                # = > >= < <=), never from raw user text.
                clauses.append(f"d.size {node.op} ?")
            else:
                clauses.append(_FILTER_SQL[node.field])
            params.append(node.value)
        elif isinstance(node, (And, Or)):
            for item in node.items:
                walk(item)
        elif isinstance(node, Not):
            # Validation rejects filters under negation; walking anyway
            # keeps this function safe if policy ever changes.
            walk(node.operand)

    walk(expr)
    return clauses, params


def translate(expr: Expr | None) -> QueryPlan:
    """Stages' output: MATCH parts, ranking terms and SQL filters."""
    positives, negation_exprs = _split(expr)
    clauses, params = _sql(expr)
    return QueryPlan(
        fts=_fts(positives),
        negations=_negations(negation_exprs),
        terms=_terms(positives),
        sql=tuple(clauses),
        sql_params=tuple(params),
    )
