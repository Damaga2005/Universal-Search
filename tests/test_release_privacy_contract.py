"""The release audit's privacy contract, asserted as a test.

Phase 030 pre-release audit: the product declares in three places that logs
and diagnostic artefacts carry no query text. This test exists so that claim
cannot rot silently in a log call nobody reads again.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "universal_search"


def _logged_calls(path: Path) -> list[ast.Call]:
    """Every ``log.<level>(...)`` call in ``path``, as AST nodes."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_log_call(node)]


def _is_log_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "log"
        and node.func.attr in {"debug", "info", "warning", "error", "exception"}
    )


def _formats(call: ast.Call) -> list[str]:
    return [
        arg.value
        for arg in call.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]


def _direct_names(call: ast.Call) -> set[str]:
    """Names passed *as they are* to the logger.

    A call like ``len(query)`` is a measurement, not a disclosure, so the
    names inside a nested call are deliberately not counted. What matters is
    whether the value itself can reach the record.
    """
    names: set[str] = set()
    for arg in list(call.args) + [keyword.value for keyword in call.keywords]:
        if isinstance(arg, ast.Name):
            names.add(arg.id)
        elif isinstance(arg, ast.Attribute):
            names.add(arg.attr)
    return names


# Anything whose name suggests it holds something the user typed or owns.
SENSITIVE_VALUE = re.compile(
    r"(?i)\b(query|queries|text|content|snippet|words|headings|body|terms)\b"
)


def test_no_log_call_in_the_package_passes_a_sensitive_value() -> None:
    """A query is the user's own words: it belongs in the UI, not the log."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for call in _logged_calls(path):
            templates = _formats(call)
            if not any("%" in template or "{" in template for template in templates):
                continue  # nothing is interpolated: nothing can leak
            leaked = sorted(
                name for name in _direct_names(call) if SENSITIVE_VALUE.search(name)
            )
            if leaked:
                offenders.append(
                    f"{path.relative_to(ROOT)}: {templates[0]!r} <- {leaked}"
                )
    assert not offenders, f"logs may carry user text: {offenders}"


def test_no_log_call_writes_a_raw_document_field() -> None:
    """Extracted text, snippets and term vectors never reach a log record."""
    banned = re.compile(
        r"(?i)^(document_text|extracted_text|content|snippet|words|headings|"
        r"terms|pairs|raw_text)$"
    )
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for call in _logged_calls(path):
            if not any(
                "%" in template or "{" in template for template in _formats(call)
            ):
                continue
            for name in _direct_names(call):
                if banned.match(name):
                    offenders.append(
                        f"{path.relative_to(ROOT)}: passes {name!r}"
                    )
    assert not offenders, f"logs may carry document text: {offenders}"


def test_the_gui_failure_log_measures_the_query_without_writing_it() -> None:
    """The specific line the audit changed, pinned so it cannot come back."""
    source = (SRC / "gui" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    messages = [
        arg.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_log_call(node)
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]
    assert not any("for %r" in message and "query" in message for message in messages)
    assert any("characters" in message for message in messages)


def test_log_messages_are_bounded() -> None:
    """A log record is capped, so one call cannot spill a whole document."""
    from universal_search.appconfig import MAX_LOG_MESSAGE

    assert MAX_LOG_MESSAGE <= 1000


def test_the_declared_contract_matches_the_implementation() -> None:
    """What the docs promise about logs is what the code does."""
    privacy = (ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    log_row = next(
        line for line in privacy.splitlines() if "universal-search.log" in line
    )
    assert "nunca texto del documento" in log_row

    events_row = next(
        line for line in privacy.splitlines() if "events.jsonl" in line
    )
    assert "nunca texto" in events_row

    from universal_search.observability import REDACTED, _sanitize_value

    assert _sanitize_value("query", "palabra secreta") == REDACTED
    assert _sanitize_value("access_token", "abc") == REDACTED
    assert _sanitize_value("detail", "ok") == "ok"


@pytest.mark.parametrize(
    "field",
    ["query", "user_query", "content", "document_text", "snippet", "password",
     "token", "secret", "credential", "search_query"],
)
def test_sensitive_field_names_are_redacted(field: str) -> None:
    from universal_search.observability import REDACTED, _sanitize_value

    assert _sanitize_value(field, "anything") == REDACTED
