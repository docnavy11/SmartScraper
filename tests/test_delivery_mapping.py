"""mapping.py: record fields -> outbound tool arguments."""

from __future__ import annotations

import pytest

from smartscraper.delivery.mapping import (
    MapContext,
    MappingError,
    expand_paths,
    map_record,
    map_records,
    map_value,
    resolve_ref,
)

RUN = {
    "run_id": 7,
    "scraper": "widgets",
    "extracted_at": "2026-09-17T09:00:00+00:00",
    "status": "passed",
    "validator": {"row_count": 2},
}
ROW = {"name": "Widget", "price": 9.5, "url": "https://x/1", "tags": ["a", "b"], "nested": {"k": "v"}}


def test_row_reference_keeps_the_original_type():
    assert map_value("$row.price", MapContext(row=ROW, run=RUN)) == 9.5
    assert isinstance(map_value("$row.price", MapContext(row=ROW, run=RUN)), float)


def test_run_extracted_at():
    assert map_value("$run.extracted_at", MapContext(row=ROW, run=RUN)) == RUN["extracted_at"]
    assert resolve_ref("$run.extracted_at", MapContext(run=RUN)) == RUN["extracted_at"]


def test_dotted_paths_reach_into_nested_values():
    ctx = MapContext(row=ROW, run=RUN)
    assert map_value("$row.nested.k", ctx) == "v"
    assert map_value("$row.tags.0", ctx) == "a"
    assert map_value("$run.validator.row_count", ctx) == 2


def test_whole_namespace_references():
    ctx = MapContext(row=ROW, run=RUN)
    assert map_value("$row", ctx) == ROW
    assert map_value("$row.*", ctx) == ROW
    assert map_value("$run.*", ctx)["scraper"] == "widgets"


def test_index_reference_counts_from_zero():
    out = map_records({"i": "$index", "n": "$row.name"}, [{"name": "a"}, {"name": "b"}], RUN)
    assert [o["i"] for o in out] == [0, 1]


def test_missing_reference_is_none_unless_strict():
    ctx = MapContext(row=ROW, run=RUN)
    assert map_value("$row.absent", ctx) is None
    with pytest.raises(MappingError):
        map_value("$row.absent", ctx, strict=True)


def test_default_is_used_only_when_the_reference_is_missing():
    ctx = MapContext(row=ROW, run=RUN)
    assert map_value({"$ref": "$row.absent", "default": 0}, ctx) == 0
    assert map_value({"$ref": "$row.price", "default": 0}, ctx) == 9.5


def test_interpolation_produces_a_string():
    ctx = MapContext(row=ROW, run=RUN)
    assert map_value("{$row.name} costs {$row.price}", ctx) == "Widget costs 9.5"
    assert map_value("run {$run.run_id} of {$run.scraper}", ctx) == "run 7 of widgets"


def test_literals_and_escapes_pass_through():
    ctx = MapContext(row=ROW, run=RUN)
    assert map_value("plain text", ctx) == "plain text"
    assert map_value("\\$row.name", ctx) == "$row.name"
    assert map_value(42, ctx) == 42
    assert map_value(None, ctx) is None


def test_nested_structures_are_mapped_recursively():
    spec = {"outer": {"inner": "$row.name"}, "list": ["$row.price", "literal"]}
    assert map_record(spec, ROW, RUN) == {"outer": {"inner": "Widget"}, "list": [9.5, "literal"]}


def test_bracket_keys_build_a_values_array():
    """The Google-Sheets-append shape the MCP console screen produces."""
    mapping = {
        "spreadsheet": "sheet-id",
        "values[0]": "$run.extracted_at",
        "values[1]": "$row.name",
        "values[2]": "$row.price",
    }
    assert map_record(mapping, ROW, RUN) == {
        "spreadsheet": "sheet-id",
        "values": ["2026-09-17T09:00:00+00:00", "Widget", 9.5],
    }


def test_sparse_indices_keep_their_positions():
    assert expand_paths({"v[0]": "a", "v[2]": "c"}) == {"v": ["a", None, "c"]}


def test_dotted_keys_build_nested_objects():
    assert expand_paths({"a.b.c": 1, "a.b.d": 2}) == {"a": {"b": {"c": 1, "d": 2}}}


def test_plain_keys_are_untouched():
    assert expand_paths({"a": 1, "b": {"c": 2}}) == {"a": 1, "b": {"c": 2}}


def test_map_records_preserves_order_and_length():
    rows = [{"name": n} for n in ("a", "b", "c")]
    out = map_records({"n": "$row.name"}, rows, RUN)
    assert [o["n"] for o in out] == ["a", "b", "c"]


def test_empty_mapping_yields_empty_payloads():
    assert map_record({}, ROW, RUN) == {}
    assert map_records({}, [ROW], RUN) == [{}]
