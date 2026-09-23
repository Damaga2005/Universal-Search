"""Query language: lexer, parser, validation, translation (spec 012).

Asserts the documented semantics end to end at the pure-function level:
precedence, quoting, escaping, Unicode, field formats, empty and
malformed input, and the injection-safety invariant of translation.
"""

import re

import pytest

from universal_search.query import (
    And,
    FieldRef,
    Filter,
    Not,
    Or,
    Phrase,
    QueryError,
    Term,
    parse_query,
    translate,
)


# -- plain terms and operators ------------------------------------------------

def test_single_term():
    plan = translate(parse_query("bjt"))
    assert plan.fts == '"bjt"'
    assert plan.terms == ("bjt",)
    assert plan.sql == ()
    assert plan.negations == ""


def test_plain_terms_are_implicit_and():
    expr = parse_query("bjt mux")
    assert isinstance(expr, And)
    assert expr.items == (Term("bjt"), Term("mux"))
    plan = translate(expr)
    assert plan.fts == '("bjt" AND "mux")'
    assert plan.terms == ("bjt", "mux")


def test_or_binds_looser_than_and():
    expr = parse_query("bjt OR cmos mux")
    assert isinstance(expr, Or)
    assert expr.items[0] == Term("bjt")
    assert expr.items[1] == And((Term("cmos"), Term("mux")))
    assert translate(expr).fts == '("bjt" OR ("cmos" AND "mux"))'


def test_implicit_and_before_or():
    expr = parse_query("bjt cmos OR mux")
    assert isinstance(expr, Or)
    assert expr.items[0] == And((Term("bjt"), Term("cmos")))
    assert expr.items[1] == Term("mux")


def test_keywords_are_case_insensitive():
    expr = parse_query("BJT or mux")
    assert isinstance(expr, Or)
    assert expr.items[0] == Term("BJT")


def test_parentheses_override_precedence():
    expr = parse_query("(bjt OR cmos) mux")
    assert isinstance(expr, And)
    assert isinstance(expr.items[0], Or)
    plan = translate(expr)
    assert plan.fts == '(("bjt" OR "cmos") AND "mux")'


def test_explicit_and():
    expr = parse_query("bjt AND mux")
    assert isinstance(expr, And)
    assert expr.items == (Term("bjt"), Term("mux"))


# -- negation -----------------------------------------------------------------

def test_top_level_negation_splits_out():
    expr = parse_query("bjt -cmos")
    assert isinstance(expr, And)
    assert expr.items[1] == Not(Term("cmos"))
    plan = translate(expr)
    assert plan.fts == '"bjt"'
    assert plan.negations == '("cmos")'
    assert plan.terms == ("bjt",)  # excluded words never rank


def test_multiple_negations_are_ored_together():
    plan = translate(parse_query("bjt -cmos -mux"))
    assert plan.negations == '("cmos" OR "mux")'
    assert plan.terms == ("bjt",)


def test_negation_of_group():
    plan = translate(parse_query("bjt -(cmos OR mux)"))
    assert plan.negations == '(("cmos" OR "mux"))'


def test_negative_only_has_no_positive_terms():
    plan = translate(parse_query("-cmos"))
    assert plan.terms == ()
    assert plan.fts == ""


# -- quoting and escaping -----------------------------------------------------

def test_quoted_phrase_is_one_fts_phrase():
    expr = parse_query('"ebers moll"')
    assert expr == Phrase("ebers moll")
    plan = translate(expr)
    assert plan.fts == '"ebers moll"'
    assert plan.terms == ("ebers", "moll")


def test_escapes_inside_quotes():
    expr = parse_query(r'"say \"hi\" now"')
    assert expr == Phrase('say "hi" now')
    plan = translate(expr)
    assert plan.fts == '"say hi now"'  # quotes can never reach FTS syntax


def test_unterminated_quote_recovers_literally():
    # Phase-004 compatibility: '"notes' still searches for notes.
    plan = translate(parse_query('"notes'))
    assert plan.fts == '"notes"'
    assert plan.terms == ("notes",)


def test_unicode_terms_preserved():
    plan = translate(parse_query("calibración"))
    assert plan.fts == '"calibración"'
    assert plan.terms == ("calibración",)


def test_repeated_terms_de_duplicate_but_keep_first_casing():
    plan = translate(parse_query("bjt BJT bjt"))
    assert plan.terms == ("bjt",)


def test_symbols_outside_quotes_are_ignored():
    plan = translate(parse_query("C++; nota [2026] #final"))
    assert plan.terms == ("c", "nota", "2026", "final")


# -- empty and degenerate input ----------------------------------------------

@pytest.mark.parametrize("raw", ["", "   ", "()", '"', "*", "';", "==="])
def test_degenerate_queries_produce_empty_plans(raw):
    plan = translate(parse_query(raw))
    assert plan.fts == ""
    assert plan.terms == ()
    assert plan.sql == ()
    assert plan.negations == ""


def test_empty_group_inside_query_is_a_no_op():
    plan = translate(parse_query("bjt ()"))
    assert plan.fts == '"bjt"'
    assert plan.terms == ("bjt",)


# -- text fields (FTS columns) ------------------------------------------------

def test_path_field_translates_to_column_filter():
    expr = parse_query("path:universidad")
    assert expr == FieldRef("path", "universidad")
    plan = translate(expr)
    assert plan.fts == 'path : "universidad"'
    assert plan.terms == ("universidad",)
    assert plan.sql == ()


def test_name_field_accepts_quoted_values_with_spaces():
    plan = translate(parse_query('name:"informe final"'))
    assert plan.fts == 'name : "informe final"'
    assert plan.terms == ("informe", "final")


def test_unknown_field_degrades_to_plain_words():
    # Compatibility: foo:bar searches both words (phase-004 behavior),
    # so free text with colons never errors.
    expr = parse_query("foo:bar")
    assert expr == And((Term("foo"), Term("bar")))
    plan = translate(expr)
    assert plan.fts == '("foo" AND "bar")'


def test_number_colon_text_keeps_working():
    plan = translate(parse_query("3:15"))
    assert plan.terms == ("3", "15")


# -- SQL filters --------------------------------------------------------------

def test_known_filter_fields_build_filter_nodes():
    # A known filter field routes to a SQL `Filter` node, normalized in
    # place by stage 3; a text field routes to an FTS column reference.
    assert parse_query("type:PDF") == Filter("type", "=", ".pdf")
    assert parse_query("path:universidad") == FieldRef("path", "universidad")


def test_type_filter_is_normalized():
    plan = translate(parse_query("type:PDF"))
    assert plan.sql == ("d.extension = ?",)
    assert plan.sql_params == (".pdf",)
    assert plan.fts == ""
    assert plan.terms == ()


def test_type_filter_accepts_leading_dot():
    assert translate(parse_query("type:.Md")).sql_params == (".md",)


def test_source_filter_validates_against_known_kinds():
    plan = translate(parse_query("source:ONEDRIVE"))
    assert plan.sql == ("d.source = ?",)
    assert plan.sql_params == ("onedrive",)
    with pytest.raises(QueryError, match="source:"):
        parse_query("source:twitter")


def test_date_filters_use_calendar_date_bounds():
    after = translate(parse_query("after:2026-01-31"))
    assert after.sql == ("substr(d.modified_at, 1, 10) > ?",)
    assert after.sql_params == ("2026-01-31",)
    before = translate(parse_query("before:2020-12-31"))
    assert before.sql == ("substr(d.modified_at, 1, 10) < ?",)


@pytest.mark.parametrize("raw", ["after:2026-13-01", "before:2026-02-30",
                                 "after:31-01-2026", "after:2026"])
def test_malformed_dates_give_feedback(raw):
    with pytest.raises(QueryError, match="expects a date"):
        parse_query(raw)


def test_size_filter_operators_and_binary_units():
    greater = translate(parse_query("size:>10MB"))
    assert greater.sql == ("d.size > ?",)
    assert greater.sql_params == (10 * 1024 * 1024,)
    at_least = translate(parse_query("size:500KB"))
    assert at_least.sql == ("d.size >= ?",)
    assert at_least.sql_params == (500 * 1024,)
    bare = translate(parse_query("size:1000"))
    assert bare.sql_params == (1000,)  # no unit = bytes, no op = >=
    exact = translate(parse_query("size:<=1.5GB"))
    assert exact.sql == ("d.size <= ?",)
    assert exact.sql_params == (int(1.5 * 1024**3),)


@pytest.mark.parametrize("raw", ["size:abc", "size:>abcMB", "size:5TB",
                                 "size:"])
def test_malformed_sizes_give_feedback(raw):
    with pytest.raises(QueryError, match="size:"):
        parse_query(raw)


def test_filters_combine_with_text_at_top_level():
    plan = translate(parse_query("bjt type:pdf"))
    assert plan.fts == '"bjt"'
    assert plan.terms == ("bjt",)
    assert plan.sql == ("d.extension = ?",)
    assert plan.sql_params == (".pdf",)


# -- structural feedback ------------------------------------------------------

@pytest.mark.parametrize(
    "raw,match",
    [
        ("bjt AND", "operator 'AND'"),
        ("bjt OR", "operator 'OR'"),
        ("AND bjt", "unexpected operator 'AND'"),
        ("bjt AND OR cmos", "operator 'AND'"),
        ("bjt OR OR cmos", "operator 'OR'"),
        ("(bjt", r"unclosed '\('"),
        ("bjt)", r"unexpected '\)'"),
        (")", r"unexpected '\)'"),
        ("bjt -", "negation"),
        ("bjt AND -", "negation"),
        ("type:", "needs a value"),
        ("name:", "needs a value"),
        ("name:!!!", "at least one word"),
        ("type:pdf OR bjt", "inside OR"),
        ("bjt OR (cmos AND type:pdf)", "inside OR"),
        ("bjt -cmos OR mux", "inside OR"),
        ("-type:pdf", "negated"),
        ("bjt OR -mux", "inside OR"),
    ],
)
def test_malformed_queries_raise_readable_errors(raw, match):
    with pytest.raises(QueryError, match=match):
        parse_query(raw)


# -- injection safety ---------------------------------------------------------

NASTY = [
    "'; DROP TABLE documents; --",
    'bjt" OR "1"="1',
    '1=1) OR (documents_fts MATCH "x',
    "bjt NEAR cmos x",
    "(((((((",
    ")))))))",
    "size:>1MB AND type:'pdf",
    'name:"a" OR "b',
    "%00%01 OR _",
    "bjt OR NOT cmos",
]


@pytest.mark.parametrize("raw", NASTY)
def test_translation_only_emits_fts_safe_characters(raw):
    """Either the query is rejected, or its MATCH string is built only
    from word runs, quotes, parentheses, spaces and ':' — no user
    character can ever reach an FTS5 operator or string delimiter."""
    try:
        expr = parse_query(raw)
    except QueryError:
        return  # feedback is a valid, safe outcome
    plan = translate(expr)
    assert re.fullmatch(r'[\w\s"():]+', plan.fts), repr(plan.fts)
    assert all(isinstance(p, (str, int)) for p in plan.sql_params)
