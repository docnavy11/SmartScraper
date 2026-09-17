"""The repair loop: candidate, diff, rationale and the minor/major verdict."""

from __future__ import annotations

import pytest

from smartscraper.agents.fake import FakeGateway, ToolCall, Turn
from smartscraper.agents.repair import RepairRequest, first_paragraph, repair, repair_prompt
from smartscraper.agents.tools_browser import TrialRun
from smartscraper.contracts import FieldMetric, PageSnapshot, RuleResult, Usage, ValidatorReport
from tests.test_agents_support import ROWS, FakeEngine, good_probes, script, script_dict, settings_for


def a_failed_report() -> ValidatorReport:
    return ValidatorReport(
        passed=False,
        rules=[
            RuleResult(rule="min_rows", passed=False, measured="0", expected=">= 10"),
            RuleResult(rule="max_null_rate[price]", passed=True, measured="0.0", expected="<= 0.05"),
        ],
        metrics=[FieldMetric(field="price", null_rate=1.0, distinct_count=0)],
        row_count=0,
    )


def a_snapshot() -> PageSnapshot:
    return PageSnapshot(
        url="https://example.com/products",
        title="Widgets",
        accessibility_tree="main",
        html="<div class='item'></div>",
    )


def a_request(**kwargs) -> RepairRequest:
    base = {
        "script": script(),
        "report": a_failed_report(),
        "snapshot": a_snapshot(),
        "last_good_sample": ROWS,
        "name": "widgets",
    }
    base.update(kwargs)
    return RepairRequest(**base)


def a_trial(rows=ROWS):
    async def test_run(_script) -> TrialRun:
        return TrialRun(ok=True, row_count=len(rows), rows=list(rows), summary="passed")

    return test_run


def repair_turn(new_script, text="The list container class changed.") -> Turn:
    return Turn(
        calls=[
            ToolCall("snapshot", {}),
            ToolCall("extract_probe", {"selector": "css=.item"}),
            ToolCall("propose_script", {"script": new_script}),
            ToolCall("finish", {}),
        ],
        text=text,
        usage=Usage(model="claude-opus-5", input_tokens=60_000, output_tokens=2_000, turns=4),
    )


async def run_repair(tmp_path, new_script, **kwargs):
    gateway = FakeGateway([repair_turn(new_script, **kwargs)])
    result = await repair(
        a_request(),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=a_trial(),
    )
    return gateway, result


# --------------------------------------------------------------------------- happy path
async def test_a_selector_only_repair_comes_back_minor(tmp_path):
    fixed = script_dict()
    fixed["steps"][2]["selector"] = "css=.item"
    fixed["steps"][1]["selector"] = "css=.item"
    _, result = await run_repair(tmp_path, fixed)

    assert result.ok
    assert result.is_minor
    assert result.candidate.version == 2
    assert result.reasons


async def test_the_diff_is_against_the_active_version(tmp_path):
    fixed = script_dict()
    fixed["steps"][2]["fields"]["price"]["selector"] = ".amount"
    _, result = await run_repair(tmp_path, fixed)

    assert "--- v1" in result.diff
    assert "+++ v2" in result.diff
    assert ".amount" in result.diff


async def test_the_rationale_is_one_paragraph_of_the_agents_answer(tmp_path):
    fixed = script_dict()
    fixed["steps"][2]["selector"] = "css=.item"
    _, result = await run_repair(
        tmp_path,
        fixed,
        text="The site renamed .product to .item.\n\nI also noticed unrelated things.",
    )
    assert result.rationale == "The site renamed .product to .item."


async def test_a_structural_repair_is_flagged_major(tmp_path):
    bigger = script_dict()
    bigger["steps"].insert(1, {"op": "scroll", "to": "bottom", "times": 3})
    _, result = await run_repair(tmp_path, bigger)

    assert result.ok
    assert not result.is_minor
    assert any("step count changed" in r for r in result.reasons)


async def test_a_repair_that_changes_a_field_set_is_major(tmp_path):
    changed = script_dict()
    changed["steps"][2]["fields"]["sku"] = {"selector": ".sku", "attr": "text"}
    _, result = await run_repair(tmp_path, changed)

    assert not result.is_minor
    assert any("'sku' added" in r for r in result.reasons)


async def test_usage_comes_back_priced(tmp_path):
    fixed = script_dict()
    fixed["steps"][2]["selector"] = "css=.item"
    _, result = await run_repair(tmp_path, fixed)
    assert result.usage.cost_usd == pytest.approx((60_000 * 5 + 2_000 * 25) / 1_000_000)


# --------------------------------------------------------------------------- refusals
async def test_a_custom_python_repair_is_refused_by_the_gateway(tmp_path):
    sneaky = script_dict()
    sneaky["steps"].insert(1, {"op": "custom_python", "code": "import os"})
    gateway, result = await run_repair(tmp_path, sneaky)

    assert not result.ok
    assert result.candidate is None
    assert gateway.denials == ["propose_script"]


async def test_a_repair_that_never_validates_reports_the_errors(tmp_path):
    broken = script_dict()
    broken["steps"][2]["fields"] = 5
    _, result = await run_repair(tmp_path, broken)

    assert not result.ok
    assert "did not validate" in result.error


async def test_a_candidate_that_fails_its_trial_run_is_not_returned(tmp_path):
    async def failing(_script):
        return TrialRun(ok=False, error="still 0 rows")

    fixed = script_dict()
    fixed["steps"][2]["selector"] = "css=.item"
    gateway = FakeGateway([repair_turn(fixed)])
    result = await repair(
        a_request(),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path),
        test_run=failing,
    )
    assert not result.ok
    assert result.candidate is None


# --------------------------------------------------------------------------- prompt
def test_the_prompt_carries_the_script_the_failures_and_the_last_good_rows():
    prompt = repair_prompt(a_request())
    assert "min_rows" in prompt
    assert "css=.product" in prompt
    assert "Alpha" in prompt
    assert "https://example.com/products" in prompt


def test_the_prompt_survives_a_run_with_no_captured_page():
    prompt = repair_prompt(a_request(snapshot=None, last_good_sample=[]))
    assert "min_rows" in prompt
    assert "Alpha" not in prompt


async def test_the_repair_model_from_settings_is_used(tmp_path):
    fixed = script_dict()
    fixed["steps"][2]["selector"] = "css=.item"
    gateway = FakeGateway([repair_turn(fixed)])
    await repair(
        a_request(),
        gateway=gateway,
        engine=FakeEngine(probes=good_probes()),
        settings=settings_for(tmp_path, repair_model="claude-opus-5"),
        test_run=a_trial(),
    )
    assert gateway.runs[0]["model"] == "claude-opus-5"
    assert gateway.runs[0]["system"].startswith("A scrape script that used to work has failed")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("\n\n  one  two  \n\nthree", "one two"),
        ("single line", "single line"),
    ],
)
def test_first_paragraph(text, expected):
    assert first_paragraph(text) == expected
