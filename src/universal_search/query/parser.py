"""Tokens to AST (spec 012, stage 2).

Precedence, lowest to highest: ``OR`` < implicit/explicit ``AND`` <
``-`` negation < primary (word, quoted phrase, field, parenthesised
group). Implicit AND is plain adjacency (``bjt mux``), exactly the
semantics searches had before the language existed.

Structural problems — dangling operators, unbalanced parentheses —
raise :class:`~universal_search.query.errors.QueryError` with a message
a person can act on. Field *values* are not checked here: building the
right node (``Filter`` vs ``FieldRef``, plain words for unknown
``foo:bar``) is this stage's only job; format and placement policy live
in validation.
"""

from .errors import QueryError
from .lexer import WORD_RE, Token
from .nodes import (
    FILTER_FIELDS,
    KNOWN_FIELDS,
    TEXT_FIELDS,
    And,
    Expr,
    FieldRef,
    Filter,
    Not,
    Or,
    Phrase,
    Term,
)

# The empty group ``()``: a node that contributes nothing and is dropped
# whenever its enclosing chain is combined.
_EMPTY = And(())


def _field_node(token: Token) -> Expr:
    """Route one field token to its node type.

    Known fields become ``FieldRef`` (FTS column) or ``Filter`` (SQL).
    Unknown ``foo:bar`` degrades to ``foo AND bar`` — phase-004
    compatibility: free text containing a colon keeps searching both
    words, so ``meeting 3:15`` or ``http://x`` never error.
    """
    name = token.text
    value = token.value
    if name not in KNOWN_FIELDS:
        words = WORD_RE.findall(value)
        if not words:
            return Term(name)  # `foo:` alone: just the word
        return And((Term(name), *(Term(word) for word in words)))
    if not value:
        raise QueryError(f"'{name}:' needs a value (example: {name}:...)")
    if name in TEXT_FIELDS:
        return FieldRef(name, value)
    if name in FILTER_FIELDS:
        # Raw value; validation (stage 3) checks and normalizes it.
        return Filter(name, "", value)
    raise QueryError(f"unknown field '{name}:'")  # pragma: no cover


def _drop_empty(items: list[Expr]) -> list[Expr]:
    return [
        item
        for item in items
        if not (isinstance(item, And) and len(item.items) == 0)
    ]


def _combine(node_type, items: list[Expr]) -> Expr:
    kept = _drop_empty(items)
    if not kept:
        return _EMPTY
    if len(kept) == 1:
        return kept[0]
    return node_type(tuple(kept))


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def peek(self) -> Token | None:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def parse(self) -> Expr:
        expr = self._or_chain()
        leftover = self.peek()
        if leftover is not None:
            if leftover.kind == "rparen":
                raise QueryError("unexpected ')' — unbalanced parentheses")
            if leftover.kind in ("and", "or"):
                raise QueryError(
                    f"unexpected operator '{leftover.text.upper()}'"
                )
            raise QueryError("unexpected input")  # pragma: no cover
        return expr

    def _or_chain(self) -> Expr:
        items = [self._and_chain()]
        while True:
            token = self.peek()
            if token is None or token.kind != "or":
                break
            self._pos += 1
            nxt = self.peek()
            if nxt is None or nxt.kind in ("rparen", "and", "or"):
                raise QueryError("operator 'OR' needs a term after it")
            items.append(self._and_chain())
        return _combine(Or, items)

    def _and_chain(self) -> Expr:
        items = [self._unary()]
        while True:
            token = self.peek()
            if token is None:
                break
            if token.kind == "and":
                self._pos += 1
                nxt = self.peek()
                if nxt is None or nxt.kind in ("rparen", "and", "or"):
                    raise QueryError("operator 'AND' needs a term after it")
                items.append(self._unary())
                continue
            if token.kind in ("or", "rparen"):
                break
            # Implicit AND: term, phrase, field, '(' or '-' follow.
            items.append(self._unary())
        return _combine(And, items)

    def _unary(self) -> Expr:
        token = self.peek()
        if token is not None and token.kind == "minus":
            self._pos += 1
            nxt = self.peek()
            if nxt is None or nxt.kind in ("rparen", "and", "or"):
                raise QueryError("negation '-' needs a term after it")
            return Not(self._unary())
        return self._primary()

    def _primary(self) -> Expr:
        token = self.peek()
        if token is None:
            raise QueryError("unexpected end of query")
        if token.kind == "lparen":
            self._pos += 1
            nxt = self.peek()
            if nxt is not None and nxt.kind == "rparen":
                self._pos += 1
                return _EMPTY  # empty group `()` contributes nothing
            inner = self._or_chain()
            closing = self.peek()
            if closing is None or closing.kind != "rparen":
                raise QueryError("unclosed '(' — add a matching ')'")
            self._pos += 1
            return inner
        self._pos += 1
        if token.kind == "term":
            return Term(token.text)
        if token.kind == "phrase":
            return Phrase(token.text)
        if token.kind == "field":
            return _field_node(token)
        if token.kind == "rparen":
            raise QueryError("unexpected ')' — unbalanced parentheses")
        if token.kind in ("and", "or"):
            raise QueryError(
                f"unexpected operator '{token.text.upper()}' at the start "
                "of the query"
            )
        raise QueryError("unexpected input")  # pragma: no cover


def parse(tokens: list[Token]) -> Expr | None:
    """Build the AST from tokens (``None`` for an empty token stream)."""
    if not tokens:
        return None
    return _Parser(tokens).parse()
