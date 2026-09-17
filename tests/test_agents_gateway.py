"""The real AgentSdkGateway, exercised as far as it goes without an API key.

Everything here runs offline: option assembly, the permission callback, budget
refusal (which short-circuits before any subprocess starts) and usage folding.
Anything past `query()` needs a live key and is listed in the report instead.
"""

from __future__ import annotations

import pytest
from claude_agent_sdk import ResultMessage, ToolPermissionContext

from smartscraper.agents.gateway import (
    AgentSdkGateway,
    Budget,
    BudgetExceeded,
    contains_custom_python,
    usage_from_result,
)
from smartscraper.agents.tools_browser import BrowserToolbox, qualified
from tests.test_agents_support import FakeEngine, script_dict, settings_for


def a_result(**kwargs) -> ResultMessage:
    base = {
        "subtype": "success",
        "duration_ms": 1000,
        "duration_api_ms": 900,
        "is_error": False,
        "num_turns": 4,
        "session_id": "s1",
    }
    base.update(kwargs)
    return ResultMessage(**base)


def a_gateway(tmp_path, **kwargs) -> AgentSdkGateway:
    return AgentSdkGateway(settings=settings_for(tmp_path), **kwargs)


def a_toolbox(tmp_path) -> BrowserToolbox:
    return BrowserToolbox(FakeEngine(), settings=settings_for(tmp_path))


# --------------------------------------------------------------------------- budget
def test_budget_tracks_remaining_and_exhaustion():
    budget = Budget(limit_usd=1.0)
    assert budget.remaining == 1.0
    budget.add(0.75)
    assert budget.remaining == pytest.approx(0.25)
    assert not budget.exhausted
    budget.add(0.30)
    assert budget.exhausted
    assert budget.remaining == 0.0
    with pytest.raises(BudgetExceeded):
        budget.check()


def test_an_unset_budget_never_blocks():
    budget = Budget()
    budget.add(1_000.0)
    assert budget.remaining is None
    assert not budget.exhausted
    budget.check()


async def test_run_agent_refuses_before_it_starts_a_subprocess_when_the_budget_is_gone(tmp_path):
    gateway = a_gateway(tmp_path, budget=Budget(limit_usd=5.0, spent_usd=5.0))
    result = await gateway.run_agent(
        system="s", prompt="p", tools=[], model="claude-opus-5", max_turns=3
    )
    assert not result.ok
    assert "budget exhausted" in result.error
    assert gateway.usages == []


async def test_extract_refuses_on_an_exhausted_budget_too(tmp_path):
    gateway = a_gateway(tmp_path, budget=Budget(limit_usd=1.0, spent_usd=1.5))
    result = await gateway.extract(
        text="<html/>", output_schema={"type": "object"}, model="claude-sonnet-5"
    )
    assert not result.ok
    assert "budget exhausted" in result.error


def test_the_remaining_budget_is_handed_to_the_sdk_as_max_budget_usd(tmp_path):
    gateway = a_gateway(tmp_path, budget=Budget(limit_usd=4.0, spent_usd=1.5))
    options = gateway._options(
        system="s", tools=[], model="claude-opus-5", max_turns=5, output_schema=None
    )
    assert options.max_budget_usd == pytest.approx(2.5)


# --------------------------------------------------------------------------- options
def test_no_built_in_tool_is_reachable(tmp_path):
    gateway = a_gateway(tmp_path)
    tools = a_toolbox(tmp_path).tools()
    options = gateway._options(
        system="sys", tools=tools, model="claude-opus-5", max_turns=9, output_schema=None
    )
    assert options.tools == []
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert all(name.startswith("mcp__browser__") for name in options.allowed_tools)
    assert "Bash" not in options.allowed_tools
    assert "Read" not in options.allowed_tools


def test_propose_script_is_kept_off_the_allowlist_so_the_gate_can_see_it(tmp_path):
    gateway = a_gateway(tmp_path)
    options = gateway._options(
        system="sys",
        tools=a_toolbox(tmp_path).tools(),
        model="claude-opus-5",
        max_turns=9,
        output_schema=None,
    )
    assert qualified("propose_script") not in options.allowed_tools
    assert qualified("snapshot") in options.allowed_tools
    assert options.can_use_tool is not None


def test_the_in_process_browser_server_is_the_only_mcp_server(tmp_path):
    gateway = a_gateway(tmp_path)
    options = gateway._options(
        system="sys",
        tools=a_toolbox(tmp_path).tools(),
        model="claude-opus-5",
        max_turns=9,
        output_schema=None,
    )
    assert list(options.mcp_servers) == ["browser"]
    assert options.mcp_servers["browser"]["type"] == "sdk"


def test_options_carry_the_model_turn_limit_effort_and_schema(tmp_path):
    gateway = AgentSdkGateway(settings=settings_for(tmp_path, agent_effort="high"))
    schema = {"type": "object", "properties": {"rows": {"type": "array"}}}
    options = gateway._options(
        system="sys", tools=[], model="claude-sonnet-5", max_turns=1, output_schema=schema
    )
    assert options.model == "claude-sonnet-5"
    assert options.max_turns == 1
    assert options.effort == "high"
    assert options.output_format == {"type": "json_schema", "schema": schema}
    assert options.system_prompt == "sys"


def test_a_toolless_call_registers_no_server_and_no_permission_callback(tmp_path):
    gateway = a_gateway(tmp_path)
    options = gateway._options(
        system="sys", tools=[], model="claude-sonnet-5", max_turns=1, output_schema=None
    )
    assert options.mcp_servers == {}
    assert options.allowed_tools == []
    assert options.can_use_tool is None


# --------------------------------------------------------------------------- permission gate
def a_context() -> ToolPermissionContext:
    return ToolPermissionContext(tool_use_id="tu_1")


async def test_a_proposal_with_custom_python_is_denied_with_a_reason(tmp_path):
    gateway = a_gateway(tmp_path)
    callback = gateway._permission_callback({qualified("propose_script")})
    sneaky = script_dict()
    sneaky["steps"].insert(1, {"op": "custom_python", "code": "import os"})

    decision = await callback(qualified("propose_script"), {"script": sneaky}, a_context())
    assert decision.behavior == "deny"
    assert "custom_python" in decision.message
    assert gateway.denials == [qualified("propose_script")]


async def test_a_clean_proposal_is_allowed(tmp_path):
    gateway = a_gateway(tmp_path)
    callback = gateway._permission_callback({qualified("propose_script")})
    decision = await callback(qualified("propose_script"), {"script": script_dict()}, a_context())
    assert decision.behavior == "allow"
    assert gateway.denials == []


async def test_custom_python_is_allowed_when_the_gateway_was_told_to_allow_it(tmp_path):
    gateway = a_gateway(tmp_path, allow_custom_python=True)
    callback = gateway._permission_callback({qualified("propose_script")})
    sneaky = script_dict()
    sneaky["steps"].insert(1, {"op": "custom_python", "code": "return 1"})
    decision = await callback(qualified("propose_script"), {"script": sneaky}, a_context())
    assert decision.behavior == "allow"


async def test_a_tool_that_is_not_ours_is_denied(tmp_path):
    gateway = a_gateway(tmp_path)
    callback = gateway._permission_callback({qualified("snapshot")})
    decision = await callback("Bash", {"command": "rm -rf /"}, a_context())
    assert decision.behavior == "deny"
    assert gateway.denials == ["Bash"]


@pytest.mark.parametrize(
    "payload",
    [
        {"script": {"steps": [{"op": "custom_python", "code": "x"}]}},
        {"script": {"steps": [{"op": "loop", "steps": [{"op": "custom_python", "code": "x"}]}]}},
        {"script": "steps:\n  - op: custom_python\n    code: x\n"},
    ],
)
def test_custom_python_is_found_however_it_is_nested(payload):
    assert contains_custom_python(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        {"script": script_dict()},
        {"note": "the word custom_python appears in prose only"},
        {},
    ],
)
def test_a_clean_payload_is_not_flagged(payload):
    assert contains_custom_python(payload) is False


# --------------------------------------------------------------------------- usage
def test_usage_is_read_from_the_camelcase_model_usage_block():
    result = a_result(
        model_usage={
            "claude-opus-5": {
                "inputTokens": 120_000,
                "outputTokens": 6_000,
                "cacheReadInputTokens": 900_000,
                "cacheCreationInputTokens": 0,
                "costUSD": 1.0,
                "canonicalModel": "claude-opus-5",
            }
        }
    )
    usage = usage_from_result(result, "claude-opus-5")
    assert usage.input_tokens == 120_000
    assert usage.cache_read_tokens == 900_000
    assert usage.turns == 4
    # Priced from our own table, not from the CLI's costUSD.
    assert usage.cost_usd == pytest.approx((120_000 * 5 + 6_000 * 25 + 900_000 * 0.5) / 1_000_000)


def test_usage_falls_back_to_the_snake_case_messages_api_block():
    result = a_result(
        usage={
            "input_tokens": 1_000,
            "output_tokens": 500,
            "cache_read_input_tokens": 2_000,
            "cache_creation_input_tokens": 100,
        }
    )
    usage = usage_from_result(result, "claude-sonnet-5")
    assert (usage.input_tokens, usage.output_tokens) == (1_000, 500)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (2_000, 100)
    assert usage.model == "claude-sonnet-5"


def test_a_model_our_table_does_not_know_falls_back_to_the_cli_cost():
    result = a_result(
        total_cost_usd=0.42,
        model_usage={"some-future-model": {"inputTokens": 10, "outputTokens": 10}},
    )
    usage = usage_from_result(result, "some-future-model")
    assert usage.cost_usd == pytest.approx(0.42)


def test_a_result_with_no_usage_at_all_costs_nothing():
    usage = usage_from_result(a_result(), "claude-opus-5")
    assert usage.cost_usd == 0.0
    assert usage.input_tokens == 0


# --------------------------------------------------------------------------- the seam
def test_both_gateways_satisfy_the_contract(tmp_path):
    from smartscraper.agents.fake import FakeGateway
    from smartscraper.contracts import LLMGateway

    assert isinstance(a_gateway(tmp_path), LLMGateway)
    assert isinstance(FakeGateway(), LLMGateway)
