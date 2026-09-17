"""A gateway that runs scripted responses instead of a model.

Every test in this package goes through it, so the whole agent subsystem can be
exercised with no API key and no network. It executes the real tool handlers, so
a scripted `propose_script` call really does validate against the DSL, and it
applies the same custom_python refusal the live gateway applies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..contracts import AgentResult, Usage
from .cost import cost_of
from .gateway import Budget, BudgetExceeded, contains_custom_python


@dataclass(slots=True)
class ToolCall:
    """One tool the scripted agent calls."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Turn:
    """One scripted `run_agent` response: some tool calls, then an answer."""

    calls: list[ToolCall] = field(default_factory=list)
    text: str = ""
    data: dict[str, Any] | None = None
    usage: Usage | None = None
    ok: bool = True
    error: str | None = None
    # Stop calling tools as soon as one of them reports an error.
    stop_on_tool_error: bool = False


@dataclass(slots=True)
class ToolOutcome:
    """What a scripted tool call produced, for assertions in tests."""

    tool: str
    args: dict[str, Any]
    text: str
    is_error: bool


class FakeGateway:
    """`contracts.LLMGateway` driven by a list of `Turn`s."""

    def __init__(
        self,
        turns: list[Turn] | None = None,
        *,
        extractions: list[AgentResult] | None = None,
        budget: Budget | None = None,
        allow_custom_python: bool = False,
    ) -> None:
        self.turns: list[Turn] = list(turns or [])
        self.extractions: list[AgentResult] = list(extractions or [])
        self.budget = budget or Budget()
        self.allow_custom_python = allow_custom_python

        self.runs: list[dict[str, Any]] = []
        self.extracts: list[dict[str, Any]] = []
        self.outcomes: list[ToolOutcome] = []
        self.usages: list[Usage] = []
        self.denials: list[str] = []

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _text_of(result: dict[str, Any]) -> str:
        blocks = result.get("content") or []
        return "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))

    def _account(self, usage: Usage | None) -> Usage | None:
        if usage is None:
            return None
        if not usage.cost_usd:
            usage.cost_usd = cost_of(usage)
        self.usages.append(usage)
        self.budget.add(usage.cost_usd)
        return usage

    # -- the protocol -------------------------------------------------------
    async def run_agent(
        self,
        *,
        system: str,
        prompt: str,
        tools: list[Any],
        model: str,
        max_turns: int,
        output_schema: dict[str, Any] | None = None,
        on_event: Any | None = None,
    ) -> AgentResult:
        self.runs.append(
            {
                "system": system,
                "prompt": prompt,
                "model": model,
                "max_turns": max_turns,
                "output_schema": output_schema,
                "tools": [t.name for t in tools],
            }
        )
        try:
            self.budget.check()
        except BudgetExceeded as exc:
            return AgentResult(ok=False, error=str(exc))

        if not self.turns:
            return AgentResult(ok=False, error="FakeGateway ran out of scripted turns")
        turn = self.turns.pop(0)

        if len(turn.calls) > max_turns:
            return AgentResult(
                ok=False,
                error=f"max_turns exceeded: {len(turn.calls)} tool calls, limit {max_turns}",
                usage=self._account(turn.usage),
            )

        by_name = {t.name: t for t in tools}
        for call in turn.calls:
            tool = by_name.get(call.tool)
            if tool is None:
                self.denials.append(call.tool)
                self.outcomes.append(
                    ToolOutcome(call.tool, call.args, f"no such tool: {call.tool}", True)
                )
                continue
            if (
                call.tool == "propose_script"
                and not self.allow_custom_python
                and contains_custom_python(call.args)
            ):
                self.denials.append(call.tool)
                self.outcomes.append(
                    ToolOutcome(
                        call.tool,
                        call.args,
                        "denied: a custom_python step needs human approval",
                        True,
                    )
                )
                continue
            result = await tool.handler(call.args)
            outcome = ToolOutcome(
                call.tool, call.args, self._text_of(result), bool(result.get("isError"))
            )
            self.outcomes.append(outcome)
            if outcome.is_error and turn.stop_on_tool_error:
                break

        data = turn.data
        if data is None and output_schema is not None and turn.text:
            try:
                parsed = json.loads(turn.text)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                data = parsed

        return AgentResult(
            ok=turn.ok,
            text=turn.text,
            data=data,
            usage=self._account(turn.usage),
            error=turn.error,
        )

    async def extract(
        self,
        *,
        text: str,
        output_schema: dict[str, Any],
        model: str,
        instructions: str = "",
    ) -> AgentResult:
        self.extracts.append(
            {
                "text": text,
                "output_schema": output_schema,
                "model": model,
                "instructions": instructions,
            }
        )
        try:
            self.budget.check()
        except BudgetExceeded as exc:
            return AgentResult(ok=False, error=str(exc))

        if not self.extractions:
            return AgentResult(ok=False, error="FakeGateway ran out of scripted extractions")
        result = self.extractions.pop(0)
        self._account(result.usage)
        return result
