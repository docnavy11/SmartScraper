"""The run directory, and the probe the builder agent leans on."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smartscraper.contracts import RunOutcome
from smartscraper.runner.artifacts import (
    RunArtifacts,
    outcome_from_dict,
    outcome_to_dict,
    prune,
)
from smartscraper.runner.engines.http import HttpEngine
from smartscraper.runner.probe import (
    describe,
    probe_elements,
    probe_engine,
    probe_with_fallbacks,
)

PAGE = """<html><body>
  <ul class="list">
    <li class="item"><span class="name">Alpha</span><span class="price">10</span></li>
    <li class="item"><span class="name">Beta</span><span class="price"></span></li>
    <li class="item"><span class="name">Gamma</span><span class="price">30</span></li>
    <li class="item"><span class="name"></span><span class="price">40</span></li>
  </ul>
  <div class="new-list"><div class="tile">Delta</div></div>
</body></html>"""


def engine() -> HttpEngine:
    e = HttpEngine()
    e.set_content(PAGE, url="https://fixture.test/list")
    return e


# ------------------------------------------------------------------- artifacts
def test_the_run_directory_is_created_on_construction(tmp_path):
    art = RunArtifacts(1, tmp_path / "r1")
    assert art.dir.is_dir()


def test_paths_follow_the_documented_layout(tmp_path):
    art = RunArtifacts(1, tmp_path / "r1")
    assert art.log_path.name == "log.txt"
    assert art.html_path.name == "page.html"
    assert art.screenshot_path.name == "screen.png"
    assert art.records_path.name == "records.jsonl"
    assert art.trace_path.name == "trace.zip"


def test_records_are_appended_one_json_object_per_line(tmp_path):
    with RunArtifacts(1, tmp_path / "r1") as art:
        art.append_records([{"a": 1}, {"a": 2}])
        art.append_record({"a": 3})
    lines = (tmp_path / "r1" / "records.jsonl").read_text().splitlines()
    assert [json.loads(x)["a"] for x in lines] == [1, 2, 3]


def test_records_are_flushed_as_they_arrive(tmp_path):
    # A run killed by the wall-clock timeout must still leave what it found.
    art = RunArtifacts(1, tmp_path / "r1")
    art.append_record({"a": 1})
    assert art.records_path.read_text().strip()      # readable before close()
    art.close()


def test_read_records_round_trips(tmp_path):
    with RunArtifacts(1, tmp_path / "r1") as art:
        art.append_records([{"a": 1}, {"b": "two"}])
        assert art.read_records() == [{"a": 1}, {"b": "two"}]


def test_record_count_tracks_appends(tmp_path):
    with RunArtifacts(1, tmp_path / "r1") as art:
        art.append_records([{"a": i} for i in range(5)])
        assert art.record_count == 5


def test_read_records_of_an_empty_run_is_empty(tmp_path):
    assert RunArtifacts(1, tmp_path / "r1").read_records() == []


def test_save_html_writes_the_page(tmp_path):
    art = RunArtifacts(1, tmp_path / "r1")
    art.save_html("<html>hi</html>")
    assert art.html_path.read_text() == "<html>hi</html>"


def test_save_html_under_another_name(tmp_path):
    art = RunArtifacts(1, tmp_path / "r1")
    art.save_html("<html/>", name="blocked.html")
    assert (tmp_path / "r1" / "blocked.html").exists()


def test_adopt_moves_a_file_into_the_run_directory(tmp_path):
    src = tmp_path / "elsewhere" / "trace.zip"
    src.parent.mkdir()
    src.write_bytes(b"PK\x03\x04")
    art = RunArtifacts(1, tmp_path / "r1")
    assert art.adopt(src, "trace.zip") == art.trace_path
    assert art.trace_path.exists() and not src.exists()


def test_adopt_of_a_missing_file_is_none(tmp_path):
    assert RunArtifacts(1, tmp_path / "r1").adopt(tmp_path / "ghost", "trace.zip") is None


def test_existing_lists_only_files(tmp_path):
    art = RunArtifacts(1, tmp_path / "r1")
    art.save_html("<html/>")
    (art.dir / "subdir").mkdir()
    assert set(art.existing()) == {"page.html"}


# --------------------------------------------------------------- serialisation
def test_outcome_to_dict_stringifies_paths_and_counts_rows(tmp_path):
    outcome = RunOutcome(rows=[{"a": 1}, {"a": 2}], engine_used="http", escalation_level=1,
                         artifacts={"page.html": tmp_path / "page.html"})
    data = outcome_to_dict(outcome)
    assert data["row_count"] == 2
    assert isinstance(data["artifacts"]["page.html"], str)
    json.dumps(data)      # must be JSON-serialisable for the stdout stream


def test_outcome_round_trips_through_the_stream_shape(tmp_path):
    original = RunOutcome(rows=[{"a": 1}], engine_used="patchright", escalation_level=2,
                          proxy_used="http://p:1", blocked=True, block_reason="cloudflare",
                          error="blocked", artifacts={"trace.zip": tmp_path / "t.zip"},
                          steps=[{"op": "goto", "ok": True}])
    back = outcome_from_dict(outcome_to_dict(original))
    assert back.rows == original.rows
    assert back.engine_used == "patchright"
    assert back.escalation_level == 2
    assert back.blocked and back.block_reason == "cloudflare"
    assert back.artifacts["trace.zip"] == Path(tmp_path / "t.zip")


def test_save_outcome_writes_readable_json(tmp_path):
    art = RunArtifacts(1, tmp_path / "r1")
    art.save_outcome(RunOutcome(rows=[{"a": 1}], engine_used="http", escalation_level=0))
    assert json.loads(art.outcome_path.read_text())["row_count"] == 1


# -------------------------------------------------------------------- pruning
def age(path: Path, days: int) -> None:
    import os

    when = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (when, when))


def test_prune_deletes_run_directories_past_retention(tmp_path):
    old = RunArtifacts(1, tmp_path / "1").dir
    new = RunArtifacts(2, tmp_path / "2").dir
    age(old, 40)
    removed = prune(tmp_path, keep_days=30, traces_days=7)
    assert old in removed and not old.exists()
    assert new.exists()


def test_prune_drops_traces_on_a_shorter_clock(tmp_path):
    art = RunArtifacts(1, tmp_path / "1")
    art.trace_path.write_bytes(b"PK")
    art.har_path.write_text("{}")
    art.append_record({"a": 1})
    art.close()
    age(art.dir, 10)
    prune(tmp_path, keep_days=30, traces_days=7)
    assert not art.trace_path.exists() and not art.har_path.exists()
    assert art.records_path.exists()      # the evidence survives the trace


def test_prune_on_a_missing_root_is_a_no_op(tmp_path):
    assert prune(tmp_path / "absent", keep_days=1, traces_days=1) == []


# ---------------------------------------------------------------------- probe
async def test_probe_counts_matches_and_non_empty_values():
    result = await probe_engine(engine(), "css=.item .name")
    assert result.matched == 4
    assert result.non_empty == 3          # one .name is empty
    assert result.samples[:2] == ["Alpha", "Beta"]
    assert result.error is None


async def test_probe_of_a_selector_that_matches_nothing():
    result = await probe_engine(engine(), "css=.nope")
    assert result.matched == 0 and result.non_empty == 0 and result.samples == []
    assert result.error is None           # "no match" is an answer, not an error


async def test_probe_of_an_invalid_selector_reports_the_error():
    result = await probe_engine(engine(), "css=.a >>>> .b")
    assert result.error is not None and result.matched == 0


async def test_probe_of_an_empty_selector_reports_the_error():
    assert (await probe_engine(engine(), "")).error is not None


async def test_probe_can_read_an_attribute_instead_of_text():
    e = HttpEngine()
    e.set_content('<a href="/a">A</a><a href="/b">B</a>', url="https://x.test/")
    result = await probe_engine(e, "css=a", attr="href")
    assert result.non_empty == 2
    assert result.samples == ["https://x.test/a", "https://x.test/b"]


async def test_probe_limits_the_number_of_samples():
    result = await probe_engine(engine(), "css=.item", limit=2)
    assert result.matched == 4 and len(result.samples) == 2


async def test_probe_elements_summarises_a_resolved_list():
    e = engine()
    from smartscraper.runner.locators import parse_selector

    elements = await e.query(parse_selector("css=.item .price"))
    result = await probe_elements(elements, "css=.item .price")
    assert result.matched == 4 and result.non_empty == 3


async def test_probe_with_fallbacks_reports_which_candidate_answered():
    result, resolution = await probe_with_fallbacks(engine(), "css=.item", ["css=.tile"])
    assert resolution is not None and not resolution.is_fallback
    assert result.matched == 4


async def test_probe_with_fallbacks_uses_the_fallback_when_the_primary_is_dead():
    result, resolution = await probe_with_fallbacks(engine(), "css=.gone", ["css=.tile"])
    assert resolution is not None and resolution.is_fallback
    assert resolution.used == "css=.tile"
    assert result.matched == 1


async def test_probe_with_fallbacks_when_everything_is_dead():
    result, resolution = await probe_with_fallbacks(engine(), "css=.gone", ["css=.also-gone"])
    assert resolution is None
    assert result.error is not None and "css=.gone" in result.error


@pytest.mark.parametrize(
    ("selector", "contains"),
    [("css=.item .name", "4 matched, 3 non-empty"), ("css=.nope", "no match")],
)
async def test_describe_gives_one_readable_line(selector, contains):
    assert contains in describe(await probe_engine(engine(), selector))


async def test_describe_reports_an_error_selector():
    assert "error" in describe(await probe_engine(engine(), "css=.a >>>> .b"))


# ------------------------------------------------------------------- snapshot
async def test_snapshot_carries_url_title_html_and_an_outline():
    e = HttpEngine()
    e.set_content("<html><head><title>T</title></head><body><h1>Head</h1>"
                  "<a href='/x'>Link</a></body></html>", url="https://x.test/p")
    snap = await e.snapshot(budget_bytes=10_000)
    assert snap.url == "https://x.test/p"
    assert snap.title == "T"
    assert "<h1>" in snap.html
    assert "h1: Head" in snap.accessibility_tree
    assert snap.truncated is False


async def test_snapshot_respects_the_byte_budget():
    e = HttpEngine()
    e.set_content("<html><body>" + "<p>x</p>" * 5000 + "</body></html>", url="https://x.test/")
    snap = await e.snapshot(budget_bytes=500)
    assert len(snap.html) == 500 and snap.truncated is True
