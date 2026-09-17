"""The builder loop, end to end, with a scripted gateway and a fake browser."""

from __future__ import annotations

import json

import pytest

from smartscraper.agents.builder import (
    BuildRequest,
    build,
    derive_validation,
    infer_output_schema,
    measured_null_rates,
    slug,
)
from smartscraper.agents.fake import FakeGateway, ToolCall, Turn
from smartscraper.agents.tools_browser import TrialRun
from smartscraper.contracts import Usage
from smartscraper.dsl.models import ScrapeScript
from tests.test_agents_support import (
    ROWS,
    FakeEngine,
    HttpEngine,
    good_probes,
    script_dict,
    settings_for,
)


def a_good_run(rows=ROWS):
    async def test_run(script: ScrapeScript) -> TrialRun:
        return TrialRun(ok=True, row_count=len(rows), rows=list(rows), summary="all rules passed")

    return test_run


def explore_then_propose(script=None) -> Turn:
    return Turn(
        calls=[
            ToolCall("navigate", {"url": "https://example.com/products"}),
            ToolCall("snapshot", {}),
            ToolCall("extract_probe", {"selector": "css=.product"}),
            ToolCall("extract_probe", {"selector": "h3"}),
            ToolCall("extract_probe", {"selector": ".price"}),
            ToolCall("propose_script", {"script": script or script_dict()}),
            ToolCall("finish", {"note": "3 products on the page"}),
        ],
        text="Built a script over .product; probed 3 matches for each field.",
        usage=Usage(model="claude-opus-5", input_tokens=40_000, output_tokens=3_000, turns=7),
    )


async def test_the_builder_returns_a_valid_script_that_passed_a_run(tmp_path):
    gateway = FakeGateway([explore_then_propose()])
    engine = FakeEngine(probes=good_probes())
    result = await build(
        BuildRequest(url="https://example.com/products", goal="every product", name="widgets"),
        gateway=gateway,
        engine=engine,
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )

    assert result.ok
    assert isinstance(result.script, ScrapeScript)
    assert result.row_count == 3
    assert result.script.field_names() == ["name", "price"]
    assert engine.gotos == ["https://example.com/products"]


async def test_the_builder_asks_the_configured_model_with_the_configured_turn_limit(tmp_path):
    gateway = FakeGateway([explore_then_propose()])
    settings = settings_for(tmp_path, builder_model="claude-opus-5", max_builder_turns=40)
    await build(
        BuildRequest(url="https://example.com/products", goal="products"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings,
        test_run=a_good_run(),
    )
    run = gateway.runs[0]
    assert run["model"] == "claude-opus-5"
    assert run["max_turns"] == 40
    assert "propose_script" in run["tools"]
    assert "Bash" not in run["tools"]


async def test_the_prompt_carries_the_dsl_schema_and_the_goal(tmp_path):
    gateway = FakeGateway([explore_then_propose()])
    await build(
        BuildRequest(url="https://example.com/p", goal="all the widgets", target_schema={"type": "array"}),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )
    prompt = gateway.runs[0]["prompt"]
    assert "all the widgets" in prompt
    assert "extract_list" in prompt  # the DSL schema is in there
    assert gateway.runs[0]["system"].startswith("You build scrape scripts")


async def test_validation_is_derived_from_the_rows_that_were_measured(tmp_path):
    rows = [
        {"name": "Alpha", "price": "$10.00"},
        {"name": "Beta", "price": None},
        {"name": "Gamma", "price": "$8.25"},
        {"name": "Delta", "price": "$1.00"},
    ]
    gateway = FakeGateway([explore_then_propose()])
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(rows),
    )
    validation = result.script.validation
    assert validation.min_rows == 2
    assert validation.max_rows == 12
    assert validation.max_null_rate["name"] == pytest.approx(0.05)
    assert validation.max_null_rate["price"] == pytest.approx(0.30)
    assert validation.required_fields == ["name"]


async def test_thresholds_the_agent_set_itself_are_left_alone(tmp_path):
    proposed = script_dict(validation={"min_rows": 25, "max_rows": 100})
    gateway = FakeGateway([explore_then_propose(proposed)])
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )
    assert result.script.validation.min_rows == 25
    assert result.script.validation.max_rows == 100


async def test_the_output_schema_is_inferred_from_the_observed_rows(tmp_path):
    gateway = FakeGateway([explore_then_propose()])
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )
    schema = result.script.output_schema
    assert schema["type"] == "array"
    assert schema["items"]["properties"]["name"] == {"type": "string"}
    assert schema["items"]["required"] == ["name", "price"]


async def test_a_target_schema_from_the_user_wins_over_inference(tmp_path):
    target = {"type": "array", "items": {"type": "object", "properties": {"sku": {"type": "string"}}}}
    gateway = FakeGateway([explore_then_propose()])
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products", target_schema=target),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )
    assert result.script.output_schema == target


async def test_a_fixture_of_the_captured_page_is_saved_for_offline_repair(tmp_path):
    settings = settings_for(tmp_path)
    gateway = FakeGateway([explore_then_propose()])
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products", name="Widget Shop"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings,
        test_run=a_good_run(),
    )
    assert result.fixture_path is not None
    assert result.fixture_path.parent == settings.fixtures_dir / "widget-shop"
    assert "<div class=\"product\">" in result.fixture_path.read_text()

    meta = json.loads((result.fixture_path.parent / "meta.json").read_text())
    assert meta["row_count"] == 3
    assert {p["selector"] for p in meta["probes"]} == {"css=.product", "h3", ".price"}


async def test_usage_comes_back_priced(tmp_path):
    gateway = FakeGateway([explore_then_propose()])
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )
    # 40k input at $5/MTok plus 3k output at $25/MTok.
    assert result.usage.cost_usd == pytest.approx(0.275)


async def test_a_run_with_no_accepted_proposal_fails_with_the_validation_errors(tmp_path):
    broken = script_dict()
    broken["steps"][2]["fields"] = "not a mapping"
    gateway = FakeGateway(
        [
            Turn(
                calls=[ToolCall("propose_script", {"script": broken})],
                text="I could not get it to validate.",
                usage=Usage(model="claude-opus-5", input_tokens=1_000),
            )
        ]
    )
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )
    assert not result.ok
    assert result.script is None
    assert "did not validate" in result.error


async def test_a_proposal_whose_test_run_failed_is_not_returned(tmp_path):
    async def failing(script):
        return TrialRun(ok=False, row_count=0, error="0 rows")

    gateway = FakeGateway([explore_then_propose()])
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=failing,
    )
    assert not result.ok
    assert result.script is None


async def test_a_custom_python_proposal_is_refused_and_the_build_fails(tmp_path):
    sneaky = script_dict()
    sneaky["steps"].insert(1, {"op": "custom_python", "code": "import os"})
    gateway = FakeGateway(
        [
            Turn(
                calls=[ToolCall("propose_script", {"script": sneaky})],
                text="tried custom python",
                usage=Usage(model="claude-opus-5", input_tokens=1_000),
            )
        ]
    )
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )
    assert not result.ok
    assert gateway.denials == ["propose_script"]


async def test_the_same_proposal_is_allowed_when_custom_python_is_explicitly_enabled(tmp_path):
    allowed = script_dict()
    allowed["steps"].insert(1, {"op": "custom_python", "code": "return 1", "as": "x"})
    gateway = FakeGateway(
        [
            Turn(
                calls=[ToolCall("propose_script", {"script": allowed})],
                text="used custom python",
                usage=Usage(model="claude-opus-5", input_tokens=1_000),
            )
        ],
        allow_custom_python=True,
    )
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )
    assert result.ok
    assert result.script.has_custom_python
    assert gateway.denials == []


# --------------------------------------------------------------------------- pure helpers
def test_infer_output_schema_reports_only_what_the_sample_supports():
    rows = [{"a": "x", "b": 1}, {"a": "y", "b": None}]
    schema = infer_output_schema(rows)
    assert schema["items"]["required"] == ["a"]
    assert schema["items"]["properties"]["b"] == {"type": "integer"}


def test_infer_output_schema_on_no_rows_returns_nothing():
    assert infer_output_schema([]) is None


def test_measured_null_rates_counts_empty_strings_as_missing():
    rows = [{"a": "x"}, {"a": ""}, {"a": None}, {"a": "z"}]
    assert measured_null_rates(rows) == {"a": 0.5}


def test_derive_validation_leaves_everything_alone_when_the_trial_failed():
    script = ScrapeScript.model_validate(script_dict())
    assert derive_validation(script, TrialRun(ok=False)) is script.validation
    assert derive_validation(script, None) is script.validation


def test_slug_is_filesystem_safe():
    assert slug("https://Example.com/products?page=2") == "https-example-com-products-page-2"
    assert slug("???") == "scraper"


async def test_an_engine_that_cannot_interact_surfaces_on_the_result(tmp_path):
    turn = explore_then_propose()
    turn.calls.insert(1, ToolCall("click", {"selector": "#accept-cookies"}))
    gateway = FakeGateway([turn])
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products"),
        gateway=gateway,
        engine=HttpEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )
    # The script still built; the gap is what tells the caller to escalate.
    assert result.ok
    assert result.capability_gaps == ["click"]


async def test_a_browser_engine_leaves_the_gap_list_empty(tmp_path):
    gateway = FakeGateway([explore_then_propose()])
    result = await build(
        BuildRequest(url="https://example.com/p", goal="products"),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_good_run(),
    )
    assert result.capability_gaps == []
