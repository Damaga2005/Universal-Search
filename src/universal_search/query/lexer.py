"""Lexical parsing: text to tokens (spec 012, stage 1).

The lexer is deliberately dumb — it never decides what a field *means*
(that is validation's job). It only recognises structure: parentheses,
quoted runs (with backslash escapes and unterminated-quote recovery),
leading ``-`` negation, ``AND``/``OR`` keywords and word/field shapes.
Everything else — colons inside plain text, symbols, punctuation — is
dropped exactly like the pre-012 ``re.findall(r"\\w+")`` fallback did, so
free text keeps behaving the same way (``C++`` searches ``c``,
``foo:bar`` searches both words, ``3:15`` still works).
"""

import re
from dataclasses import dataclass

# Word runs: Unicode letters/digits/underscore, the same shape FTS5's
# unicode61 tokenizer and ranking.tokens() treat as searchable words.
WORD_RE = re.compile(r"\w+", re.UNICODE)

# Standalone keywords (case-insensitive; ``name:and`` is a field value,
# not this — the colon branch wins first).
KEYWORDS = ("and", "or")


@dataclass(frozen=True, slots=True)
class Token:
    """kind: term | phrase | field | and | or | minus | lparen | rparen.

    For ``field``: ``text`` is the word before the colon and ``value`` is
    the raw run after it (may be empty, may be a quoted string with
    spaces). For ``phrase``: ``text`` is the inner string with escapes
    resolved.
    """

    kind: str
    text: str = ""
    value: str = ""


def _scan_quoted(text: str, start: int) -> tuple[str, int]:
    """Inner string of a quoted run whose opening quote sits at ``start``.

    A backslash escapes the next character. An unterminated quote
    recovers by taking the remainder of the input literally (compatibility
    rule kept from phase 004: ``\"notes`` still searches ``notes``).
    """
    index = start + 1
    chars: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            chars.append(text[index + 1])
            index += 2
            continue
        if char == '"':
            return "".join(chars), index + 1
        chars.append(char)
        index += 1
    return "".join(chars), len(text)  # unterminated: literal recovery


def tokenize(text: str) -> list[Token]:
    """Tokenize ``text``; never raises (spec: unknown input never crashes)."""
    tokens: list[Token] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "(":
            tokens.append(Token("lparen"))
            index += 1
            continue
        if char == ")":
            tokens.append(Token("rparen"))
            index += 1
            continue
        if char == '"':
            inner, index = _scan_quoted(text, index)
            tokens.append(Token("phrase", inner))
            continue
        if char == "-":
            # Negation only where an expression can start: beginning of
            # input, after whitespace, or right after '('. A hyphen glued
            # to a word (state-of-the-art) keeps splitting it like \w+ does.
            previous = text[index - 1] if index > 0 else ""
            if previous == "" or previous.isspace() or previous == "(":
                tokens.append(Token("minus"))
            index += 1
            continue
        match = WORD_RE.match(text, index)
        if match:
            word = match.group()
            after = index + len(word)
            if after < length and text[after] == ":":
                value_start = after + 1
                if value_start < length and text[value_start] == '"':
                    value, index = _scan_quoted(text, value_start)
                else:
                    end = value_start
                    while (
                        end < length
                        and not text[end].isspace()
                        and text[end] != ")"
                    ):
                        end += 1
                    value = text[value_start:end]
                    index = end
                tokens.append(Token("field", word, value))
                continue
            folded = word.casefold()
            if folded in KEYWORDS:
                tokens.append(Token(folded, word))
            else:
                tokens.append(Token("term", word))
            index = after
            continue
        index += 1  # every other character is ignored (compat with \w+)
    return tokens
