"""The browser tool surface, driven directly. No gateway involved."""

from __future__ import annotations

import json

import pytest

from smartscraper.agents.tools_browser import ALLOWED_TOOLS, BrowserToolbox, TrialRun, qualified
from smartscraper.contracts import ProbeResult
from tests.test_agents_support import (
    ROWS,
    FakeEngine,
    HttpEngine,
    good_probes,
    script_dict,
    settings_for,
)


def toolbox_for(tmp_path, **kwargs) -> BrowserToolbox:
    engine = kwargs.pop("engine", None) or FakeEngine(probes=good_probes())
    return BrowserToolbox(engine, settings=settings_for(tmp_path), **kwargs)


def by_name(box: BrowserToolbox) -> dict:
    return {t.name: t for t in box.tools()}


def text_of(result: dict) -> str:
    return "\n".join(b["text"] for b in result["content"])


def test_the_tool_set_is_exactly_what_the_agent_is_allowed_to_call(tmp_path):
    box = toolbox_for(tmp_path)
    names = sorted(t.name for t in box.tools())
    assert names == sorted(
        [
            "navigate",
            "snapshot",
            "click",
            "fill",
            "scroll",
            "extract_probe",
            "propose_script",
            "finish",
        ]
    )
    assert set(ALLOWED_TOOLS) == {qualified(n) for n in names}
    assert qualified("snapshot") == "mcp__browser__snapshot"


async def test_navigate_reports_the_status(tmp_path):
    engine = FakeEngine(status=404)
    box = toolbox_for(tmp_path, engine=engine)
    out = await by_name(box)["navigate"].handler({"url": "https://example.com/x"})
    assert "HTTP 404" in text_of(out)
    assert engine.gotos == ["https://example.com/x"]


async def test_snapshot_is_capped_at_the_configured_budget(tmp_path):
    engine = FakeEngine(html="<div>" + ("x" * 5_000) + "</div>")
    box = BrowserToolbox(engine, settings=settings_for(tmp_path, snapshot_budget_bytes=500))
    out = await by_name(box)["snapshot"].handler({})
    body = text_of(out)
    assert len(body) < 2_000
    assert "truncated" in body
    assert box.last_snapshot is not None


async def test_extract_probe_records_what_it_measured(tmp_path):
    box = toolbox_for(tmp_path)
    out = await by_name(box)["extract_probe"].handler({"selector": "h3"})
    payload = json.loads(text_of(out))
    assert payload["matched"] == 3
    assert payload["samples"][0] == "Alpha"
    assert box.probes_for("h3")[0].non_empty == 3


async def test_a_selector_that_matches_nothing_is_still_recorded(tmp_path):
    box = toolbox_for(tmp_path)
    out = await by_name(box)["extract_probe"].handler({"selector": ".nope"})
    assert json.loads(text_of(out))["matched"] == 0
    assert len(box.probes) == 1


async def test_measured_null_rates_come_only_from_probes_that_matched(tmp_path):
    engine = FakeEngine(
        probes={
            "h3": ProbeResult("h3", matched=4, non_empty=3),
            ".sku": ProbeResult(".sku", matched=0, non_empty=0),
        }
    )
    box = toolbox_for(tmp_path, engine=engine)
    probe = by_name(box)["extract_probe"].handler
    await probe({"selector": "h3"})
    await probe({"selector": ".sku"})
    assert box.measured_null_rates() == {"h3": 0.25}


async def test_propose_script_returns_pydantic_errors_verbatim(tmp_path):
    box = toolbox_for(tmp_path)
    bad = script_dict()
    bad["steps"][2]["selector"] = 123
    out = await by_name(box)["propose_script"].handler({"script": bad})
    body = text_of(out)
    assert out["isError"] is True
    assert "validation error" in body.lower()
    assert "selector" in body
    assert box.accepted is None
    assert box.proposals[-1].errors is not None


async def test_a_script_with_no_emit_step_is_rejected_by_the_dsl(tmp_path):
    box = toolbox_for(tmp_path)
    bad = script_dict()
    bad["steps"] = [s for s in bad["steps"] if s["op"] != "emit"]
    out = await by_name(box)["propose_script"].handler({"script": bad})
    assert out["isError"] is True
    assert "emit" in text_of(out)


async def test_propose_script_test_runs_and_reports_the_rows(tmp_path):
    async def test_run(script):
        return TrialRun(ok=True, row_count=len(ROWS), rows=ROWS, summary="3 of 3 rules passed")

    box = toolbox_for(tmp_path, test_run=test_run)
    out = await by_name(box)["propose_script"].handler({"script": script_dict()})
    body = text_of(out)
    assert "3 rows" in body
    assert "Alpha" in body
    assert box.accepted is not None


async def test_a_failing_test_run_means_the_proposal_is_not_accepted(tmp_path):
    async def test_run(script):
        return TrialRun(ok=False, row_count=0, error="0 rows, min_rows is 1")

    box = toolbox_for(tmp_path, test_run=test_run)
    out = await by_name(box)["propose_script"].handler({"script": script_dict()})
    assert out["isError"] is True
    assert "min_rows" in text_of(out)
    assert box.accepted is None


async def test_an_exception_in_the_runner_reaches_the_model_instead_of_the_caller(tmp_path):
    async def test_run(script):
        raise TimeoutError("browser hung")

    box = toolbox_for(tmp_path, test_run=test_run)
    out = await by_name(box)["propose_script"].handler({"script": script_dict()})
    assert out["isError"] is True
    assert "TimeoutError" in text_of(out)


async def test_finish_refuses_until_something_has_been_accepted(tmp_path):
    box = toolbox_for(tmp_path)
    out = await by_name(box)["finish"].handler({"note": "done"})
    assert out["isError"] is True
    assert box.finished is False


async def test_finish_succeeds_after_an_accepted_proposal(tmp_path):
    box = toolbox_for(tmp_path)
    tools = by_name(box)
    await tools["propose_script"].handler({"script": script_dict()})
    out = await tools["finish"].handler({"note": "3 products"})
    assert out.get("isError") is None
    assert box.finished is True
    assert box.finish_note == "3 products"


async def test_click_fill_and_scroll_reach_the_engine(tmp_path):
    engine = FakeEngine(probes=good_probes())
    box = toolbox_for(tmp_path, engine=engine)
    tools = by_name(box)
    await tools["click"].handler({"selector": "#accept"})
    await tools["fill"].handler({"selector": "#q", "value": "widget"})
    await tools["scroll"].handler({"to": "bottom", "times": 2})
    assert engine.actions == [
        ("click", ("#accept",)),
        ("fill", ("#q", "widget")),
        ("scroll", ("bottom", 2)),
    ]


async def test_an_engine_that_cannot_interact_signals_escalation(tmp_path):
    box = toolbox_for(tmp_path, engine=HttpEngine())
    out = await by_name(box)["click"].handler({"selector": "#x"})
    body = text_of(out)
    assert out["isError"] is True
    assert "cannot click" in body
    assert "Do not retry" in body
    assert box.capability_gaps == ["click"]
    assert box.needs_escalation


async def test_every_interaction_on_a_non_interactive_engine_is_recorded(tmp_path):
    box = toolbox_for(tmp_path, engine=HttpEngine())
    tools = by_name(box)
    await tools["click"].handler({"selector": "#x"})
    await tools["fill"].handler({"selector": "#q", "value": "v"})
    await tools["scroll"].handler({"to": "bottom"})
    assert box.capability_gaps == ["click", "fill", "scroll"]


async def test_an_interactive_engine_records_no_gap(tmp_path):
    box = toolbox_for(tmp_path)
    await by_name(box)["click"].handler({"selector": "#accept"})
    assert box.capability_gaps == []
    assert not box.needs_escalation


async def test_a_click_that_matched_nothing_is_an_error_not_a_gap(tmp_path):
    box = toolbox_for(tmp_path, engine=FakeEngine(hits=False))
    out = await by_name(box)["click"].handler({"selector": "#missing"})
    assert out["isError"] is True
    assert "nothing matched" in text_of(out)
    assert box.capability_gaps == []


async def test_a_scroll_that_did_not_move_the_page_is_reported_as_fully_loaded(tmp_path):
    box = toolbox_for(tmp_path, engine=FakeEngine(hits=False))
    out = await by_name(box)["scroll"].handler({"to": "bottom", "times": 2})
    assert out.get("isError") is None
    assert "fully loaded" in text_of(out)


async def test_scroll_to_a_selector_needs_one(tmp_path):
    box = toolbox_for(tmp_path)
    out = await by_name(box)["scroll"].handler({"to": "selector"})
    assert out["isError"] is True
    assert "needs a selector" in text_of(out)


@pytest.mark.parametrize("payload", [["not", "an", "object"], "a string", 7])
async def test_propose_script_rejects_a_non_object_script(tmp_path, payload):
    box = toolbox_for(tmp_path)
    out = await by_name(box)["propose_script"].handler({"script": payload})
    assert out["isError"] is True
