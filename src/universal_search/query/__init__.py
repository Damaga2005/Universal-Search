"""Search query language (spec 012).

Four separate stages, one entry point each:

1. :func:`universal_search.query.lexer.tokenize` — lexical parsing
2. :func:`universal_search.query.parser.parse` — tokens to AST
3. :func:`universal_search.query.validate.validate` — validation and
   normalization (with human-readable :class:`QueryError` feedback)
4. :func:`universal_search.query.translate.translate` — AST to the
   :class:`QueryPlan` the engine binds

``parse_query`` chains stages 1–3. The language:

===================  ==================================================
Plain terms          ``bjt mux`` — implicit AND (unchanged behavior)
Quoted phrases       ``"ebers moll"`` — words must be adjacent
``AND`` / ``OR``     case-insensitive; ``-`` negation; ``( )`` grouping
                     precedence: ``-`` > ``AND`` > ``OR``
``name:`` ``path:``  words within the file name / path (FTS column)
``type:`` ``source:``extension / provider (SQL filter)
``after:`` ``before:``calendar date ``YYYY-MM-DD`` (strict: the named
                     day itself is excluded by both)
``size:``            ``size:>10MB`` — operators ``> >= < <= =``, binary
                     units (1 KB = 1024 B), bare number = bytes, no
                     operator = ``>=``
===================  ==================================================

Documented decisions: unknown ``foo:bar`` degrades to ``foo AND bar``
(free text with colons keeps working); queries with no positive terms
return no results (FTS5 has no unary NOT — negative-only cannot anchor);
repeated terms de-duplicate; an unterminated quote takes the remainder
literally; symbols and punctuation outside quotes are ignored exactly
like the pre-012 ``\\w+`` extraction; empty groups ``()`` contribute
nothing.
"""

from .errors import QueryError
from .lexer import Token, tokenize
from .nodes import (
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
from .parser import parse
from .translate import translate
from .validate import validate

__all__ = [
    "And",
    "Expr",
    "FieldRef",
    "Filter",
    "Not",
    "Or",
    "Phrase",
    "QueryError",
    "QueryPlan",
    "Term",
    "Token",
    "parse",
    "parse_query",
    "tokenize",
    "translate",
    "validate",
]


def parse_query(text: str) -> Expr | None:
    """Lex, parse and validate ``text`` (stages 1–3 in order)."""
    return validate(parse(tokenize(text)))
