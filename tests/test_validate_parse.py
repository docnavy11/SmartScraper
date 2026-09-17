"""Table-driven coercion tests. Every case here is an input we expect from a page."""

from __future__ import annotations

import pytest

from smartscraper.dsl.models import Parse
from smartscraper.validate import parse_money, parse_value
from smartscraper.validate.parse import (
    parse_bool,
    parse_date,
    parse_datetime,
    parse_float,
    parse_int,
    parse_json,
    parse_text,
)

MONEY_CASES = [
    # raw, amount, currency
    ("€8,95", 8.95, "EUR"),
    ("8,95 €", 8.95, "EUR"),
    ("$1,234.56", 1234.56, "USD"),
    ("1.234,56 €", 1234.56, "EUR"),
    ("1 234,56 €", 1234.56, "EUR"),
    ("1 234,56 €", 1234.56, "EUR"),
    ("£19.99", 19.99, "GBP"),
    ("EUR 12.50", 12.50, "EUR"),
    ("12.50 USD", 12.50, "USD"),
    ("R$ 1.499,90", 1499.90, "BRL"),
    ("  \n  $ 0.99 \t", 0.99, "USD"),
    ("-$5.00", -5.0, "USD"),
    ("1.234.567,89", 1234567.89, None),
    ("1,234,567.89", 1234567.89, None),
    ("99", 99.0, None),
    ("€1,234", 1234.0, "EUR"),  # single comma, three digits: grouping
    ("Price: $49.00 only", 49.0, "USD"),
]


@pytest.mark.parametrize(("raw", "amount", "currency"), MONEY_CASES)
def test_money(raw, amount, currency):
    parsed = parse_money(raw)
    assert parsed.ok is True
    assert parsed.amount == pytest.approx(amount)
    assert parsed.currency == currency
    # parse_value returns the amount for the same input.
    value, ok = parse_value(raw, Parse.MONEY)
    assert ok is True
    assert value == pytest.approx(amount)


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_money_blank_is_null_not_failure(raw):
    parsed = parse_money(raw)
    assert (parsed.amount, parsed.ok) == (None, True)


@pytest.mark.parametrize("raw", ["free", "ask for price", "€", "--"])
def test_money_unparseable(raw):
    parsed = parse_money(raw)
    assert parsed.ok is False
    assert parsed.amount is None


INT_CASES = [
    ("42", 42),
    (" 42 ", 42),
    ("1,234", 1234),
    ("1.234", 1234),  # European grouping
    ("1 234", 1234),
    ("-7", -7),
    ("42 reviews", 42),
    ("0", 0),
    (17, 17),
]


@pytest.mark.parametrize(("raw", "expected"), INT_CASES)
def test_int(raw, expected):
    assert parse_int(raw) == (expected, True)


@pytest.mark.parametrize("raw", ["3.7", "abc", "1,5"])
def test_int_rejects_non_integers(raw):
    value, ok = parse_int(raw)
    assert (value, ok) == (None, False)


FLOAT_CASES = [
    ("3.7", 3.7),
    ("3,7", 3.7),
    ("1.234,5", 1234.5),
    ("1,234.5", 1234.5),
    ("-0,5", -0.5),
    ("4.5 stars", 4.5),
    ("1 234,56", 1234.56),
    (2, 2.0),
]


@pytest.mark.parametrize(("raw", "expected"), FLOAT_CASES)
def test_float(raw, expected):
    value, ok = parse_float(raw)
    assert ok is True
    assert value == pytest.approx(expected)


DATE_CASES = [
    ("2026-09-17", "2026-09-17"),
    ("17/09/2026", "2026-09-17"),
    ("17.09.2026", "2026-09-17"),
    ("17 September 2026", "2026-09-17"),
    ("September 17, 2026", "2026-09-17"),
    ("2026-09-17T10:30:00Z", "2026-09-17"),
    ("  2026-09-17 ", "2026-09-17"),
]


@pytest.mark.parametrize(("raw", "expected"), DATE_CASES)
def test_date(raw, expected):
    assert parse_date(raw) == (expected, True)


DATETIME_CASES = [
    ("2026-09-17T10:30:00Z", "2026-09-17T10:30:00+00:00"),
    ("2026-09-17 10:30:00", "2026-09-17T10:30:00"),
    ("2026-09-17 10:30", "2026-09-17T10:30:00"),
    ("17/09/2026 10:30", "2026-09-17T10:30:00"),
    ("2026-09-17", "2026-09-17T00:00:00"),
]


@pytest.mark.parametrize(("raw", "expected"), DATETIME_CASES)
def test_datetime(raw, expected):
    assert parse_datetime(raw) == (expected, True)


@pytest.mark.parametrize("raw", ["yesterday", "soon", "2026-13-45"])
def test_date_unparseable(raw):
    assert parse_date(raw) == (None, False)


BOOL_CASES = [
    ("true", True), ("Yes", True), ("1", True), ("In stock", True),
    ("false", False), ("NO", False), ("0", False), ("out of stock", False),
]


@pytest.mark.parametrize(("raw", "expected"), BOOL_CASES)
def test_bool(raw, expected):
    assert parse_bool(raw) == (expected, True)


def test_bool_unparseable():
    assert parse_bool("maybe") == (None, False)


def test_json():
    assert parse_json('{"a": 1}') == ({"a": 1}, True)
    assert parse_json("[1, 2]") == ([1, 2], True)
    value, ok = parse_json("{oops")
    assert (value, ok) == (None, False)


def test_text_collapses_whitespace():
    assert parse_text("  Big\n\n   Sale \t now   ") == ("Big Sale now", True)


ALL_KINDS = [
    Parse.TEXT, Parse.MONEY, Parse.INT, Parse.FLOAT,
    Parse.DATE, Parse.DATETIME, Parse.BOOL, Parse.JSON,
]


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_blank_is_null_for_every_kind(kind):
    assert parse_value("", kind) == (None, True)
    assert parse_value(None, kind) == (None, True)


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("raw", ["  ", "<<<>>>", object()])
def test_parse_value_never_raises(kind, raw):
    value, ok = parse_value(raw, kind)
    assert isinstance(ok, bool)
    if not ok:
        assert value is None


def test_parse_value_accepts_string_kind_and_unknown_kind():
    assert parse_value("8,95 €", "money")[0] == pytest.approx(8.95)
    assert parse_value("  hi  ", "not-a-kind") == ("hi", True)
