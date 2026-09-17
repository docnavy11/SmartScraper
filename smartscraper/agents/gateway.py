"""The one seam between the agents and whatever runs them.

Backed by the Claude Agent SDK (`claude-agent-sdk` 0.2.154). Builder, repair and
fallback speak only `contracts.LLMGateway`, so replacing this with the Anthropic
SDK tool runner is a new class here and nothing else.

What was read out of the installed package rather than guessed:

* `ClaudeAgentOptions` fields used below all exist on 0.2.154: `tools`,
  `allowed_tools`, `system_prompt`, `mcp_servers`, `strict_mcp_config`,
  `setting_sources`, `permission_mode`, `can_use_tool`, `max_turns`,
  `max_budget_usd`, `model`, `effort`, `output_format`, `cwd`, `env`.
* In-process tools are exposed to the model as `mcp__<server>__<tool>`.
* `can_use_tool` is NOT called for a tool already listed in `allowed_tools`
  (the SDK says so in the field's docstring). So `propose_script` is kept out
  of the allowlist on purpose: it is the call the custom_python gate has to
  see. Everything else is allowlisted and runs without a round trip.
* `ResultMessage.usage` is the Messages API shape (snake_case);
  `ResultMessage.model_usage` is the CLI's `modelUsage` shape (camelCase).
  Both are read below, camelCase first.
* Structured output comes back on `ResultMessage.structured_output`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ToolPermissionContext,
    create_sdk_mcp_server,
    query,
)

from ..config import Settings, get_settings
from ..contracts import AgentResult, Usage
from .cost import UnknownModelError, cost_of
from .tools_browser import SERVER_NAME, qualified

OnEvent = Callable[[Any], Any] | None

# Anything carrying this op has to be approved by a human before it can run.
CUSTOM_PYTHON = "custom_python"


class BudgetExceeded(RuntimeError):
    """Raised by `Budget.check` when there is no money left for a call."""


@dataclass
class Budget:
    """Per-scraper spend cap, shared by every call a gateway makes."""

    limit_usd: float | None = None
    spent_usd: float = 0.0

    @property
    def remaining(self) -> float | None:
        if self.limit_usd is None:
            return None
        return max(self.limit_usd - self.spent_usd, 0.0)

    @property
    def exhausted(self) -> bool:
        return self.limit_usd is not None and self.spent_usd >= self.limit_usd

    def check(self) -> None:
        if self.exhausted:
            raise BudgetExceeded(
                f"budget exhausted: spent ${self.spent_usd:.4f} of ${self.limit_usd:.4f}"
            )

    def add(self, usd: float) -> None:
        self.spent_usd += usd


def contains_custom_python(value: Any) -> bool:
    """True when a proposed script anywhere in `value` carries a custom_python step."""
    if isinstance(value, dict):
        if value.get("op") == CUSTOM_PYTHON:
            return True
        return any(contains_custom_python(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_custom_python(v) for v in value)
    if isinstance(value, str):
        # A script handed over as YAML/JSON text rather than as an object.
        return f"{CUSTOM_PYTHON}" in value and "op" in value
    return False


def usage_from_result(result: ResultMessage, model: str) -> Usage:
    """Fold a ResultMessage into our Usage record.

    `model_usage` (the CLI's camelCase `modelUsage`) is preferred because it is
    broken down per model and carries the canonical model id. `usage` is the
    Messages API shape and is used when the former is absent.
    """
    usage = Usage(model=model, turns=result.num_turns)
    per_model = result.model_usage or {}
    if per_model:
        best_tokens = -1
        for key, entry in per_model.items():
            usage.input_tokens += int(entry.get("inputTokens", 0) or 0)
            usage.output_tokens += int(entry.get("outputTokens", 0) or 0)
            usage.cache_read_tokens += int(entry.get("cacheReadInputTokens", 0) or 0)
            usage.cache_write_tokens += int(entry.get("cacheCreationInputTokens", 0) or 0)
            tokens = int(entry.get("inputTokens", 0) or 0) + int(entry.get("outputTokens", 0) or 0)
            if tokens > best_tokens:
                best_tokens = tokens
                usage.model = str(entry.get("canonicalModel") or key or model)
    elif result.usage:
        raw = result.usage
        usage.input_tokens = int(raw.get("input_tokens", 0) or 0)
        usage.output_tokens = int(raw.get("output_tokens", 0) or 0)
        usage.cache_read_tokens = int(raw.get("cache_read_input_tokens", 0) or 0)
        usage.cache_write_tokens = int(raw.get("cache_creation_input_tokens", 0) or 0)

    try:
        usage.cost_usd = cost_of(usage)
    except UnknownModelError:
        # Our table does not know this model id. Fall back to what the CLI
        # charged rather than silently recording zero.
        usage.cost_usd = float(result.total_cost_usd or 0.0)
    return usage


class AgentSdkGateway:
    """`contracts.LLMGateway` on top of the Claude Agent SDK."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        budget: Budget | None = None,
        allow_custom_python: bool = False,
        transcript_path: Path | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.budget = budget or Budget()
        self.allow_custom_python = allow_custom_python
        self.transcript_path = transcript_path
        self.cwd = cwd
        self.usages: list[Usage] = []
        self.denials: list[str] = []

    # -- permissions --------------------------------------------------------
    def _permission_callback(self, allowed: set[str]) -> Callable[..., Awaitable[Any]]:
        propose = qualified("propose_script")

        async def can_use_tool(
            tool_name: str,
            tool_input: dict[str, Any],
            context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            if tool_name != propose and tool_name not in allowed:
                self.denials.append(tool_name)
                return PermissionResultDeny(
                    message=f"{tool_name} is not available to this agent.",
                )
            if (
                tool_name == propose
                and not self.allow_custom_python
                and contains_custom_python(tool_input)
            ):
                self.denials.append(tool_name)
                return PermissionResultDeny(
                    message=(
                        "This proposal contains a custom_python step, which runs unsandboxed "
                        "and needs human approval. Express the step with the DSL vocabulary, "
                        "or explain in your answer why only custom_python can do it."
                    ),
                )
            return PermissionResultAllow()

        return can_use_tool

    # -- transcript ---------------------------------------------------------
    def _record(self, message: Any) -> None:
        if self.transcript_path is None:
            return
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "type": type(message).__name__,
            "repr": repr(message),
        }
        with self.transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    # -- options ------------------------------------------------------------
    def _options(
        self,
        *,
        system: str,
        tools: list[SdkMcpTool[Any]],
        model: str,
        max_turns: int,
        output_schema: dict[str, Any] | None,
    ) -> ClaudeAgentOptions:
        mcp_servers: dict[str, Any] = {}
        allowed: list[str] = []
        can_use_tool = None

        if tools:
            mcp_servers[SERVER_NAME] = create_sdk_mcp_server(name=SERVER_NAME, tools=list(tools))
            names = {qualified(t.name) for t in tools}
            # DO NOT add propose_script to this allowlist. It looks like an
            # oversight and it is not. The SDK does not call `can_use_tool` for
            # a tool that `allowed_tools` already permits (it says so in that
            # field's own docstring), so allowlisting propose_script would
            # silently disable the custom_python gate below: unsandboxed Python
            # would reach a script with nobody asked. Leaving it off means the
            # CLI routes the call to our callback, which is the whole point.
            # Every other browser tool is read-only or harmless and is allowed.
            allowed = sorted(names - {qualified("propose_script")})
            can_use_tool = self._permission_callback(names)

        options = ClaudeAgentOptions(
            system_prompt=system,
            # [] means: none of the built-in Claude Code tools. No Bash, no file
            # tools, no web tools reach the model.
            tools=[],
            allowed_tools=allowed,
            mcp_servers=mcp_servers,
            strict_mcp_config=True,
            setting_sources=[],
            permission_mode="default",
            can_use_tool=can_use_tool,
            max_turns=max_turns,
            model=model,
            effort=self.settings.agent_effort,  # type: ignore[arg-type]
        )
        if self.budget.remaining is not None:
            options.max_budget_usd = self.budget.remaining
        if output_schema is not None:
            options.output_format = {"type": "json_schema", "schema": output_schema}
        if self.cwd is not None:
            options.cwd = self.cwd
        return options

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
        try:
            self.budget.check()
        except BudgetExceeded as exc:
            return AgentResult(ok=False, error=str(exc))

        options = self._options(
            system=system,
            tools=list(tools),
            model=model,
            max_turns=max_turns,
            output_schema=output_schema,
        )

        texts: list[str] = []
        result: ResultMessage | None = None
        try:
            async for message in query(prompt=prompt, options=options):
                self._record(message)
                if on_event is not None:
                    outcome = on_event(message)
                    if hasattr(outcome, "__await__"):
                        await outcome
                if isinstance(message, AssistantMessage):
                    texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
                elif isinstance(message, ResultMessage):
                    result = message
        except Exception as exc:  # noqa: BLE001 - one failed agent must not kill the worker
            return AgentResult(
                ok=False,
                text="\n".join(texts),
                error=f"{type(exc).__name__}: {exc}",
                transcript_path=self.transcript_path,
            )

        if result is None:
            return AgentResult(
                ok=False,
                text="\n".join(texts),
                error="the agent stream ended without a result message",
                transcript_path=self.transcript_path,
            )

        usage = usage_from_result(result, model)
        self.usages.append(usage)
        self.budget.add(usage.cost_usd)

        data = result.structured_output if isinstance(result.structured_output, dict) else None
        text = result.result or "\n".join(texts)
        error = None
        if result.is_error:
            error = "; ".join(result.errors or []) or result.subtype or "agent reported an error"

        return AgentResult(
            ok=not result.is_error,
            text=text,
            data=data,
            usage=usage,
            transcript_path=self.transcript_path,
            error=error,
        )

    async def extract(
        self,
        *,
        text: str,
        output_schema: dict[str, Any],
        model: str,
        instructions: str = "",
    ) -> AgentResult:
        """One structured-output call, no tools, one turn."""
        return await self.run_agent(
            system=instructions,
            prompt=text,
            tools=[],
            model=model,
            max_turns=1,
            output_schema=output_schema,
        )
