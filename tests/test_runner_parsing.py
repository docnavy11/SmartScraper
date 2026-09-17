"""Value parsers. A wrong number is worse than a null, so nulls are the default."""

from __future__ import annotations

import pytest

from smartscraper.dsl.models import Parse
from smartscraper.runner.parsing import (
    absolutise,
    apply_regex,
    clean_text,
    coerce,
    parse_bool,
    parse_date,
    parse_datetime,
    parse_money,
    parse_number,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  hello   world \n", "hello world"),
        (" spaced ", "spaced"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234", 1234.0),
        ("1234.56", 1234.56),
        ("1,234.56", 1234.56),          # US grouping
        ("1.234,56", 1234.56),          # EU grouping
        ("1 234,56", 1234.56),          # space grouping
        ("1'234.56", 1234.56),          # Swiss grouping
        ("1.234.567", 1234567.0),       # dots as grouping, no decimals
        ("1,234,567", 1234567.0),
        ("-42", -42.0),
        ("0.5", 0.5),
        ("7", 7.0),
    ],
)
def test_parse_number_separators(raw, expected):
    assert parse_number(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("€1.299,00", 1299.0),
        ("$1,299.00", 1299.0),
        ("Price: 49.99 USD", 49.99),
        ("£ 12", 12.0),
        ("from €9,95 per month", 9.95),
    ],
)
def test_parse_money(raw, expected):
    assert parse_money(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "  ", "n/a", "Sold out", "TBC", "---"])
def test_unparseable_numbers_are_none_not_zero(raw):
    # Zero would be silently wrong data, which is the failure mode this system
    # exists to catch. None is measurable by max_null_rate.
    assert parse_number(raw) is None
    assert parse_money(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("yes", True), ("In stock", True), ("1", True), ("no", False),
     ("Out of stock", False), ("0", False), ("maybe", None)],
)
def test_parse_bool(raw, expected):
    assert parse_bool(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-17", "2026-09-17"),
        ("17/09/2026", "2026-09-17"),
        ("17 September 2026", "2026-09-17"),
        ("Sep 17, 2026", "2026-09-17"),
        ("20260917", "2026-09-17"),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_parse_date_rejects_nonsense():
    assert parse_date("next tuesday") is None


def test_parse_datetime_handles_z_suffix():
    assert parse_datetime("2026-09-17T08:30:00Z").startswith("2026-09-17T08:30:00")


def test_parse_datetime_falls_back_to_midnight_on_a_bare_date():
    assert parse_datetime("2026-09-17") == "2026-09-17T00:00:00"


# ------------------------------------------------------------------- coerce
@pytest.mark.parametrize(
    ("parse", "raw", "expected"),
    [
        (Parse.TEXT, "  a b ", "a b"),
        (Parse.MONEY, "€1.299,00", 1299.0),
        (Parse.INT, "42 items", 42),
        (Parse.INT, "4.6", 4),
        (Parse.FLOAT, "4.6", 4.6),
        (Parse.BOOL, "In stock", True),
        (Parse.DATE, "2026-09-17", "2026-09-17"),
        (Parse.JSON, '{"a": 1}', {"a": 1}),
    ],
)
def test_coerce(parse, raw, expected):
    assert coerce(raw, parse) == expected


def test_coerce_none_stays_none_for_every_mode():
    assert all(coerce(None, mode) is None for mode in Parse)


def test_coerce_accepts_a_plain_string_mode():
    assert coerce("1,5", "money") == pytest.approx(1.5)


def test_bad_json_is_none_not_a_raise():
    assert coerce("{not json", Parse.JSON) is None


# -------------------------------------------------------------------- regex
def test_apply_regex_returns_first_group_when_present():
    assert apply_regex("SKU-0042 (blue)", r"SKU-(\d+)") == "0042"


def test_apply_regex_returns_whole_match_without_groups():
    assert apply_regex("SKU-0042", r"SKU-\d+") == "SKU-0042"


def test_apply_regex_miss_is_none():
    assert apply_regex("nothing here", r"SKU-(\d+)") is None


def test_invalid_regex_is_none_not_a_raise():
    assert apply_regex("x", r"(unclosed") is None


# ----------------------------------------------------------------- absolute
def test_absolutise_resolves_against_the_page_url():
    assert absolutise("/p/1", "https://x.test/a/b") == "https://x.test/p/1"
    assert absolutise("2.html", "https://x.test/a/b.html") == "https://x.test/a/2.html"


def test_absolutise_leaves_absolute_urls_alone():
    assert absolutise("https://y.test/p", "https://x.test/") == "https://y.test/p"


def test_absolutise_without_a_base_is_a_passthrough():
    assert absolutise("/p/1", "") == "/p/1"
