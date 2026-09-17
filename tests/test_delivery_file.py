"""File sink: jsonl and csv round-trips, and what happens without pyarrow."""

from __future__ import annotations

import csv
import io
import json

import pytest

from smartscraper.config import get_settings
from smartscraper.delivery.file import FileSink, render_csv, render_jsonl, resolve_path

ROWS = [
    {"name": "Widget", "price": 9.5, "url": "https://x/1"},
    {"name": "Gadget", "price": 12.0, "url": "https://x/2"},
]
META = {"scraper": "widgets", "run_id": 3, "status": "passed", "extracted_at": "2026-09-17T09:00:00+00:00"}


@pytest.fixture
def exports(tmp_path, monkeypatch):
    monkeypatch.setenv("SS_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield tmp_path / "data" / "exports"
    get_settings.cache_clear()


async def test_jsonl_round_trip(exports):
    result = await FileSink().send(ROWS, config={"fmt": "jsonl"}, run_id=3, scraper="widgets", meta=META)
    assert result.ok
    assert result.rows_sent == 2

    files = list(exports.glob("widgets-3-*.jsonl"))
    assert len(files) == 1
    parsed = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert parsed == ROWS


async def test_csv_round_trip(exports):
    result = await FileSink().send(ROWS, config={"fmt": "csv"}, run_id=3, scraper="widgets", meta=META)
    assert result.ok

    text = next(iter(exports.glob("*.csv"))).read_text()
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [r["name"] for r in rows] == ["Widget", "Gadget"]
    assert [float(r["price"]) for r in rows] == [9.5, 12.0]


async def test_explicit_path_wins_over_the_template(exports, tmp_path):
    target = tmp_path / "out" / "fixed.jsonl"
    result = await FileSink().send(
        ROWS, config={"fmt": "jsonl", "path": str(target)}, run_id=3, scraper="widgets", meta=META
    )
    assert result.ok
    assert target.exists()


async def test_append_mode_accumulates(exports, tmp_path):
    cfg = {"fmt": "jsonl", "path": str(tmp_path / "acc.jsonl"), "mode": "append"}
    await FileSink().send(ROWS, config=cfg, run_id=1, scraper="w", meta=META)
    await FileSink().send(ROWS, config=cfg, run_id=2, scraper="w", meta=META)
    assert len((tmp_path / "acc.jsonl").read_text().strip().splitlines()) == 4


async def test_unknown_format_is_terminal_not_retryable(exports):
    result = await FileSink().send(ROWS, config={"fmt": "xlsx"}, run_id=3, scraper="w", meta=META)
    assert not result.ok
    assert result.retryable is False
    assert "xlsx" in (result.error or "")


async def test_parquet_reports_a_clear_error_when_pyarrow_is_missing(exports):
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        result = await FileSink().send(ROWS, config={"fmt": "parquet"}, run_id=3, scraper="w", meta=META)
        assert not result.ok
        assert result.retryable is False
        assert "pyarrow" in (result.error or "")
        return
    result = await FileSink().send(ROWS, config={"fmt": "parquet"}, run_id=3, scraper="w", meta=META)
    assert result.ok
    import pyarrow.parquet as pq

    table = pq.read_table(next(iter(exports.glob("*.parquet"))))
    assert table.column("name").to_pylist() == ["Widget", "Gadget"]


def test_render_jsonl_is_one_object_per_line():
    assert render_jsonl(ROWS).count("\n") == 2


def test_render_csv_serialises_nested_values():
    text = render_csv([{"a": {"k": 1}}])
    assert json.loads(list(csv.DictReader(io.StringIO(text)))[0]["a"]) == {"k": 1}


def test_columns_union_covers_ragged_rows():
    text = render_csv([{"a": 1}, {"b": 2}])
    assert text.splitlines()[0] == "a,b"


def test_resolve_path_uses_the_exports_dir_by_default(exports):
    path = resolve_path({}, run_id=9, scraper="s", fmt="jsonl")
    assert path.parent == exports
    assert path.name.startswith("s-9-")
