"""Metrics, drift and the validator verdict.

The case this file exists for: a run that raised nothing and returned rows still
fails, because the rows are wrong.
"""

from __future__ import annotations

import pytest

from smartscraper.dsl.models import RowCountBand, ScrapeScript
from smartscraper.validate import compute_metrics, infer_schema, validate
from smartscraper.validate.drift import baseline_for, row_count_band_rule
from smartscraper.validate.metrics import distinct_count, null_rate
from smartscraper.validate.schema import schema_errors


def script(**validation) -> ScrapeScript:
    """A two-page-ish product script with the validation block under test."""
    return ScrapeScript.model_validate(
        {
            "steps": [
                {"op": "goto", "url": "https://example.com/p"},
                {
                    "op": "extract_list",
                    "selector": "css=.product-card",
                    "as": "products",
                    "fields": {
                        "name": {"selector": "h3"},
                        "price": {"selector": ".price", "parse": "money"},
                        "url": {"selector": "a", "attr": "href", "absolute": True},
                    },
                },
                {"op": "emit", "from": "products"},
            ],
            "validation": validation,
        }
    )


def rows(n: int, *, null_price_from: int | None = None, url_start: int = 0) -> list[dict]:
    out = []
    for i in range(n):
        price = None if (null_price_from is not None and i >= null_price_from) else 9.99 + i
        out.append({"name": f"Item {i}", "price": price, "url": f"https://example.com/p/{i + url_start}"})
    return out


def by_rule(report, name: str):
    return next(r for r in report.rules if r.rule == name)


# --------------------------------------------------------------------------- metrics
def test_null_rate_worked_example_28_of_42():
    """PLAN's worked example: price is missing on a third of a 42-row page.

    28 of 42 rows carry a price, so 14 are null and the rate is 14/42 = 0.3333.
    PLAN.md's prose rounds that to 0.34; the computed value rounds to 0.33. The
    number that matters is that it is six times the 0.05 threshold.
    """
    data = rows(42, null_price_from=28)
    rate = null_rate(data, "price")
    assert rate == pytest.approx(14 / 42)
    assert round(rate, 2) == 0.33

    report = validate(data, script(min_rows=1, max_null_rate={"price": 0.05}))
    assert report.passed is False
    rule = by_rule(report, "max_null_rate[price]")
    assert rule.passed is False
    assert rule.measured == "0.33 (14 of 42 rows null)"
    assert rule.expected == "<= 0.05"


def test_null_rate_within_threshold_passes():
    data = rows(42, null_price_from=41)  # 1 of 42 null = 0.024
    report = validate(data, script(min_rows=1, max_null_rate={"price": 0.05}))
    assert report.passed is True


def test_metrics_cover_declared_fields_that_never_appeared():
    data = [{"name": "a"}, {"name": "b"}]
    metrics = {m.field: m for m in compute_metrics(data, fields=["name", "price", "url"])}
    assert metrics["name"].null_rate == 0.0
    assert metrics["price"].null_rate == 1.0
    assert metrics["url"].null_rate == 1.0
    assert metrics["name"].distinct_count == 2
    assert metrics["name"].sample == "a"
    assert metrics["price"].sample is None


@pytest.mark.parametrize("value", [None, "", "   ", [], {}])
def test_empty_values_count_as_null(value):
    assert null_rate([{"f": value}], "f") == 1.0


def test_distinct_count_handles_unhashable_values():
    data = [{"tags": ["a"]}, {"tags": ["a"]}, {"tags": ["b"]}]
    assert distinct_count(data, "tags") == 2


def test_report_carries_row_count_and_metrics():
    report = validate(rows(3), script(min_rows=1))
    assert report.row_count == 3
    assert {m.field for m in report.metrics} == {"name", "price", "url"}


# --------------------------------------------------------------------------- drift
def test_row_count_halving_is_caught_by_the_band():
    band = RowCountBand(relative_to="last_5_runs", tolerance=0.5)
    history = [120, 118, 122, 119, 121]  # baseline 120.0
    result = row_count_band_rule(60, band, history)
    assert result.passed is False
    assert result.measured == "60 rows, baseline 120.0 (-50%)"
    assert result.expected == "60.0 to 180.0 rows, edges excluded (last_5_runs +/- 0.50)"


def test_band_edges_are_exclusive_and_an_exact_match_always_passes():
    band = RowCountBand(relative_to="last_5_runs", tolerance=0.5)
    assert row_count_band_rule(61, band, [120] * 5).passed is True
    assert row_count_band_rule(60, band, [120] * 5).passed is False  # exactly halved
    assert row_count_band_rule(180, band, [120] * 5).passed is False
    assert row_count_band_rule(179, band, [120] * 5).passed is True
    assert row_count_band_rule(120, RowCountBand(tolerance=0.0), [120] * 5).passed is True
    assert row_count_band_rule(121, RowCountBand(tolerance=0.0), [120] * 5).passed is False


def test_band_windows():
    assert baseline_for([10, 20, 30, 40, 50, 60], RowCountBand(relative_to="last_run")) == 10
    assert baseline_for([10, 20, 30, 40, 50, 60], RowCountBand(relative_to="last_5_runs")) == 30
    assert baseline_for([10] * 9 + [100], RowCountBand(relative_to="last_10_runs")) == 19


def test_band_with_no_history_is_skipped_not_failed():
    result = row_count_band_rule(5, RowCountBand(), [])
    assert result.passed is True
    assert "no passing run in history" in result.measured


def test_band_with_zero_baseline_is_skipped():
    result = row_count_band_rule(5, RowCountBand(), [0, 0])
    assert result.passed is True
    assert "baseline 0" in result.measured


def test_band_flows_through_validate():
    s = script(min_rows=1, row_count_band={"relative_to": "last_5_runs", "tolerance": 0.5})
    report = validate(rows(60), s, history=[120, 118, 122, 119, 121])
    assert report.passed is False
    assert by_rule(report, "row_count_band").passed is False
    assert "row_count_band" in report.summary()


# --------------------------------------------------------------------------- rules
def test_no_exception_no_error_and_still_a_failure():
    """The whole point. The runner raised nothing, rows came back, and this is
    still a failed run: the price selector broke and every price is null."""
    data = [{"name": f"Item {i}", "price": None, "url": f"https://x/{i}"} for i in range(40)]
    s = script(min_rows=10, max_null_rate={"price": 0.05}, unique=["url"])
    report = validate(data, s)

    assert report.passed is False
    assert by_rule(report, "min_rows").passed is True  # nothing crashed, rows arrived
    assert [r.rule for r in report.failures] == ["max_null_rate[price]"]
    assert report.summary() == "1 of 4 rules failed: max_null_rate[price]"


def test_min_rows_and_max_rows():
    report = validate(rows(3), script(min_rows=10))
    assert by_rule(report, "min_rows").measured == "3 rows"
    assert by_rule(report, "min_rows").expected == ">= 10 rows"
    assert report.passed is False

    report = validate(rows(30), script(min_rows=1, max_rows=20))
    assert by_rule(report, "max_rows").passed is False
    assert validate(rows(5), script(min_rows=1)).passed is True


def test_required_fields():
    data = rows(4)
    data[2]["url"] = None
    report = validate(data, script(min_rows=1, required_fields=["name", "url"]))
    rule = by_rule(report, "required_fields")
    assert rule.passed is False
    assert rule.measured == "url null in 1/4"


def test_unique_catches_a_duplicated_page():
    data = rows(5) + rows(5)  # pagination that served page 1 twice
    report = validate(data, script(min_rows=1, unique=["url"]))
    rule = by_rule(report, "unique[url]")
    assert rule.passed is False
    assert rule.measured.startswith("5 duplicated value(s), 5 extra row(s):")


def test_unique_passes_on_distinct_values():
    report = validate(rows(5) + rows(5, url_start=100), script(min_rows=1, unique=["url"]))
    assert by_rule(report, "unique[url]").passed is True


def test_rule_order_is_fixed():
    s = script(
        min_rows=1,
        max_rows=100,
        required_fields=["name"],
        max_null_rate={"price": 0.5},
        unique=["url"],
        row_count_band={"relative_to": "last_run", "tolerance": 0.5},
    )
    report = validate(rows(5), s, history=[5])
    assert [r.rule for r in report.rules] == [
        "min_rows",
        "max_rows",
        "required_fields",
        "max_null_rate[price]",
        "unique[url]",
        "row_count_band",
        "field_set",
    ]


def test_empty_rows_fail_min_rows_without_blowing_up():
    report = validate([], script(min_rows=1, max_null_rate={"price": 0.05}, unique=["url"]))
    assert report.passed is False
    assert report.row_count == 0


# --------------------------------------------------------------------------- schema
def test_field_set_rule_catches_a_renamed_field():
    data = [{"name": "a", "cost": 1.0, "url": "https://x/1"}]
    report = validate(data, script(min_rows=1))
    rule = by_rule(report, "field_set")
    assert rule.passed is False
    assert "price" in rule.measured


def test_output_schema_rule_runs_instead_of_field_set():
    s = script(min_rows=1)
    s.output_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["name", "price", "url"],
            "properties": {
                "name": {"type": "string"},
                "price": {"type": "number"},
                "url": {"type": "string"},
            },
        },
    }
    assert validate(rows(3), s).passed is True

    bad = rows(3)
    bad[1]["price"] = "9,99"  # unparsed string reached the record
    report = validate(bad, s)
    rule = by_rule(report, "output_schema")
    assert rule.passed is False
    assert "expected number, got string" in rule.measured


def test_schema_errors_reports_missing_required_and_bad_types():
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}, "qty": {"type": "integer"}},
        },
    }
    errors = schema_errors([{"qty": "many"}], schema)
    assert any("required field missing or null" in e for e in errors)
    assert any("expected integer, got string" in e for e in errors)


def test_broken_schema_does_not_raise():
    assert schema_errors([{"a": 1}], {"type": "object", "properties": "not-a-dict"})


def test_infer_schema_round_trips_through_the_checker():
    data = rows(5)
    data[4]["price"] = None  # price is not required: null in one row
    schema = infer_schema(data)
    assert schema["type"] == "array"
    assert schema["items"]["required"] == ["name", "url"]
    assert schema["items"]["properties"]["name"]["type"] == "string"
    assert schema["items"]["properties"]["price"]["type"] == ["number", "null"]
    assert schema["items"]["properties"]["url"]["format"] == "uri"
    assert schema_errors(data, schema) == []


def test_infer_schema_on_no_rows():
    schema = infer_schema([])
    assert schema["items"]["required"] == []
    assert schema["items"]["properties"] == {}


def test_schema_subset_covers_bounds_enum_and_extra_keys():
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["new", "used"], "minLength": 2},
            "qty": {"type": "integer", "minimum": 1, "maximum": 10},
            "score": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
            "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
            "kind": {"const": "product"},
        },
        "additionalProperties": False,
    }
    row = {"status": "x", "qty": 99, "score": 1.0, "tags": ["a", "b", "c"], "kind": "other", "junk": 1}
    errors = schema_errors([row], schema)
    joined = " | ".join(errors)
    for fragment in (
        "not in enum",
        "minLength",
        "> maximum 10",
        ">= exclusiveMaximum 1",
        "maxItems 2",
        "!= const",
        "additionalProperties:false",
    ):
        assert fragment in joined, f"{fragment} not reported: {joined}"


def test_integer_satisfies_number_but_string_does_not():
    schema = {"type": "object", "properties": {"price": {"type": "number"}}}
    assert schema_errors([{"price": 3}], schema) == []
    assert schema_errors([{"price": "3"}], schema)


def test_unique_handles_unhashable_values():
    data = [{"tags": ["a"]}, {"tags": ["a"]}]
    report = validate(data, script(min_rows=1, unique=["tags"]))
    assert by_rule(report, "unique[tags]").passed is False


def test_unique_lists_at_most_three_duplicates():
    data = [{"name": "n", "price": 1.0, "url": f"https://x/{i % 4}"} for i in range(8)]
    rule = by_rule(validate(data, script(min_rows=1, unique=["url"])), "unique[url]")
    assert "+1 more" in rule.measured


def test_output_schema_violations_are_truncated_in_the_measured_string():
    s = script(min_rows=1)
    s.output_schema = {"type": "array", "items": {"type": "object", "required": ["missing"]}}
    rule = by_rule(validate(rows(9), s), "output_schema")
    assert "+4 more" in rule.measured


def test_field_set_rule_is_skipped_when_a_script_declares_no_fields():
    s = ScrapeScript.model_validate(
        {"steps": [{"op": "goto", "url": "https://x"}, {"op": "emit", "from": "x"}], "validation": {}}
    )
    rule = by_rule(validate([{"a": 1}], s), "field_set")
    assert rule.passed is True
    assert "no fields declared" in rule.measured
