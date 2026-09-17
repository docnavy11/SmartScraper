"""The tool surface the builder and repair agents drive a browser through.

Every tool is an in-process Claude Agent SDK tool (`@tool` + `create_sdk_mcp_server`
on the gateway side). They hold no browser code of their own: a `contracts.Engine`
is injected and this module only calls its protocol methods.

Every engine implements the whole `contracts.Engine` surface, interaction
included. One that structurally cannot interact, such as the HTTP rung, raises
`EngineCapabilityError`. That is an escalation signal, not a dead end: it is
recorded on `BrowserToolbox.capability_gaps` so the caller can move the script
to a browser rung, and the model is told to stop trying rather than to retry.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool
from pydantic import ValidationError

from ..config import Settings, get_settings
from ..contracts import Engine, EngineCapabilityError, PageSnapshot, ProbeResult
from ..dsl.models import ScrapeScript

SERVER_NAME = "browser"

TOOL_NAMES: tuple[str, ...] = (
    "navigate",
    "snapshot",
    "click",
    "fill",
    "scroll",
    "extract_probe",
    "propose_script",
    "finish",
)


def qualified(name: str) -> str:
    """The name the Agent SDK exposes an in-process MCP tool under."""
    return f"mcp__{SERVER_NAME}__{name}"


ALLOWED_TOOLS: list[str] = [qualified(n) for n in TOOL_NAMES]


# --------------------------------------------------------------------------- results
@dataclass(slots=True)
class TrialRun:
    """What a single test execution of a proposed script came back with."""

    ok: bool
    row_count: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    error: str | None = None


TrialRunner = Callable[[ScrapeScript], Awaitable[TrialRun]]


@dataclass(slots=True)
class Proposal:
    """One `propose_script` call and what happened to it."""

    raw: dict[str, Any]
    script: ScrapeScript | None = None
    errors: str | None = None
    test: TrialRun | None = None

    @property
    def accepted(self) -> bool:
        return self.script is not None and (self.test is None or self.test.ok)


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- toolbox
class BrowserToolbox:
    """Holds the engine, the session state, and the tools that read and write it.

    One toolbox per agent run. Everything the agent measured stays on it, so the
    caller can build a fixture and a validation block out of real observations
    rather than out of the model's prose.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        settings: Settings | None = None,
        test_run: TrialRunner | None = None,
        goal: str = "",
    ) -> None:
        self.engine = engine
        self.settings = settings or get_settings()
        self.test_run = test_run
        self.goal = goal

        self.probes: list[ProbeResult] = []
        self.snapshots: list[PageSnapshot] = []
        self.proposals: list[Proposal] = []
        self.visited: list[str] = []
        self.capability_gaps: list[str] = []
        self.finished: bool = False
        self.finish_note: str = ""

    # -- state readers ------------------------------------------------------
    @property
    def accepted(self) -> ScrapeScript | None:
        """The last proposal that validated and (if test-run) passed."""
        for p in reversed(self.proposals):
            if p.accepted:
                return p.script
        return None

    @property
    def last_snapshot(self) -> PageSnapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    @property
    def last_test(self) -> TrialRun | None:
        for p in reversed(self.proposals):
            if p.test is not None:
                return p.test
        return None

    def probes_for(self, selector: str) -> list[ProbeResult]:
        return [p for p in self.probes if p.selector == selector]

    def measured_null_rates(self) -> dict[str, float]:
        """Per-selector observed null rate, from probes only.

        `1 - non_empty/matched` for the last probe of each selector. A selector
        that never matched is left out: nothing was measured about it.
        """
        out: dict[str, float] = {}
        for p in self.probes:
            if p.error or p.matched <= 0:
                continue
            out[p.selector] = round(1.0 - (p.non_empty / p.matched), 4)
        return out

    @property
    def needs_escalation(self) -> bool:
        """True when the page needed an interaction this engine cannot perform."""
        return bool(self.capability_gaps)

    # -- internal helpers ---------------------------------------------------
    def _cannot(self, op: str, exc: EngineCapabilityError) -> dict[str, Any]:
        """Record an escalation signal and tell the model not to retry."""
        self.capability_gaps.append(op)
        return _err(
            f"the {self.engine.name} engine cannot {op}: {exc}. "
            "Do not retry it. Either build the script without this interaction, "
            "or say in your answer that the page needs a browser engine."
        )

    def _budget(self) -> int:
        return int(self.settings.snapshot_budget_bytes)

    # -- tools --------------------------------------------------------------
    def tools(self) -> list[SdkMcpTool[Any]]:
        return [
            self._navigate(),
            self._snapshot(),
            self._click(),
            self._fill(),
            self._scroll(),
            self._extract_probe(),
            self._propose_script(),
            self._finish(),
        ]

    def _navigate(self) -> SdkMcpTool[Any]:
        @tool(
            "navigate",
            "Load a URL in the browser. Returns the HTTP status.",
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute URL to load"},
                    "timeout_ms": {"type": "integer", "default": 30000},
                },
                "required": ["url"],
            },
        )
        async def navigate(args: dict[str, Any]) -> dict[str, Any]:
            url = args["url"]
            try:
                status = await self.engine.goto(url, timeout_ms=int(args.get("timeout_ms") or 30_000))
            except Exception as exc:  # noqa: BLE001 - the model gets to see and react
                return _err(f"navigate failed: {type(exc).__name__}: {exc}")
            self.visited.append(url)
            return _ok(f"{url} -> HTTP {status}")

        return navigate

    def _snapshot(self) -> SdkMcpTool[Any]:
        @tool(
            "snapshot",
            "Accessibility tree plus trimmed HTML of the current page, size-capped.",
            {"type": "object", "properties": {}},
        )
        async def snapshot(args: dict[str, Any]) -> dict[str, Any]:
            try:
                snap = await self.engine.snapshot(budget_bytes=self._budget())
            except Exception as exc:  # noqa: BLE001
                return _err(f"snapshot failed: {type(exc).__name__}: {exc}")
            self.snapshots.append(snap)

            budget = self._budget()
            head = f"url: {snap.url}\ntitle: {snap.title}\n\n--- accessibility tree ---\n"
            tree = snap.accessibility_tree[:budget]
            remaining = max(budget - len(tree), 0)
            html = snap.html[:remaining]
            truncated = snap.truncated or len(html) < len(snap.html) or len(tree) < len(
                snap.accessibility_tree
            )
            tail = "\n\n[truncated to the snapshot budget]" if truncated else ""
            return _ok(f"{head}{tree}\n\n--- html ---\n{html}{tail}")

        return snapshot

    def _click(self) -> SdkMcpTool[Any]:
        @tool(
            "click",
            "Click the first element matching a selector.",
            {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "timeout_ms": {"type": "integer", "default": 10000},
                },
                "required": ["selector"],
            },
        )
        async def click(args: dict[str, Any]) -> dict[str, Any]:
            selector = args["selector"]
            try:
                hit = await self.engine.click(
                    selector, timeout_ms=int(args.get("timeout_ms") or 10_000)
                )
            except EngineCapabilityError as exc:
                return self._cannot("click", exc)
            except Exception as exc:  # noqa: BLE001
                return _err(f"click failed: {type(exc).__name__}: {exc}")
            if not hit:
                return _err(f"nothing matched {selector}, so nothing was clicked")
            return _ok(f"clicked {selector}")

        return click

    def _fill(self) -> SdkMcpTool[Any]:
        @tool(
            "fill",
            "Type a value into the first element matching a selector.",
            {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "value": {"type": "string"},
                    "timeout_ms": {"type": "integer", "default": 10000},
                },
                "required": ["selector", "value"],
            },
        )
        async def fill(args: dict[str, Any]) -> dict[str, Any]:
            selector = args["selector"]
            try:
                hit = await self.engine.fill(
                    selector, args["value"], timeout_ms=int(args.get("timeout_ms") or 10_000)
                )
            except EngineCapabilityError as exc:
                return self._cannot("fill", exc)
            except Exception as exc:  # noqa: BLE001
                return _err(f"fill failed: {type(exc).__name__}: {exc}")
            if not hit:
                return _err(f"nothing matched {selector}, so nothing was filled")
            return _ok(f"filled {selector}")

        return fill

    def _scroll(self) -> SdkMcpTool[Any]:
        @tool(
            "scroll",
            "Scroll the page, for lazily loaded lists.",
            {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "enum": ["bottom", "top", "selector"],
                        "default": "bottom",
                    },
                    "selector": {"type": "string", "description": "required when to=selector"},
                    "times": {"type": "integer", "default": 1},
                },
            },
        )
        async def scroll(args: dict[str, Any]) -> dict[str, Any]:
            to = args.get("to") or "bottom"
            selector = args.get("selector")
            times = int(args.get("times") or 1)
            if to == "selector" and not selector:
                return _err("scroll to=selector needs a selector")
            try:
                moved = await self.engine.scroll(to=to, selector=selector, times=times)
            except EngineCapabilityError as exc:
                return self._cannot("scroll", exc)
            except Exception as exc:  # noqa: BLE001
                return _err(f"scroll failed: {type(exc).__name__}: {exc}")
            if not moved:
                return _ok(f"scrolled {to} x{times}, but the page did not move; it is fully loaded")
            return _ok(f"scrolled {to} x{times}")

        return scroll

    def _extract_probe(self) -> SdkMcpTool[Any]:
        @tool(
            "extract_probe",
            "Run a candidate selector against the live page and report what it yields.",
            {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["selector"],
            },
        )
        async def extract_probe(args: dict[str, Any]) -> dict[str, Any]:
            selector = args["selector"]
            try:
                result = await self.engine.probe(selector, limit=int(args.get("limit") or 5))
            except Exception as exc:  # noqa: BLE001
                result = ProbeResult(selector=selector, matched=0, non_empty=0, error=str(exc))
            self.probes.append(result)
            if result.error:
                return _err(f"{selector}: {result.error}")
            body = _json(
                {
                    "selector": result.selector,
                    "matched": result.matched,
                    "non_empty": result.non_empty,
                    "samples": result.samples,
                }
            )
            return _ok(body)

        return extract_probe

    def _propose_script(self) -> SdkMcpTool[Any]:
        @tool(
            "propose_script",
            (
                "Submit a complete ScrapeScript. It is validated against the DSL and, "
                "when a runner is attached, executed once. Validation errors come back verbatim."
            ),
            {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "object",
                        "description": "A full ScrapeScript object: steps, output_schema, validation.",
                    },
                    "rationale": {"type": "string", "default": ""},
                },
                "required": ["script"],
            },
        )
        async def propose_script(args: dict[str, Any]) -> dict[str, Any]:
            raw = args["script"]
            if not isinstance(raw, dict):
                return _err("script must be an object, not " + type(raw).__name__)

            proposal = Proposal(raw=raw)
            self.proposals.append(proposal)

            try:
                script = ScrapeScript.model_validate(raw)
            except ValidationError as exc:
                proposal.errors = str(exc)
                return _err(f"the script did not validate against the DSL:\n\n{exc}")

            proposal.script = script

            if self.test_run is None:
                return _ok("script is valid. No runner attached, so it was not executed.")

            try:
                test = await self.test_run(script)
            except Exception as exc:  # noqa: BLE001
                test = TrialRun(ok=False, error=f"{type(exc).__name__}: {exc}")
            proposal.test = test

            if not test.ok:
                return _err(
                    "the script is valid DSL but the test run failed:\n"
                    f"{test.error or test.summary or 'no detail reported'}\n"
                    f"rows: {test.row_count}"
                )
            sample = _json(test.rows[:3]) if test.rows else "[]"
            return _ok(
                f"test run passed: {test.row_count} rows. {test.summary}\n"
                f"first rows:\n{sample}"
            )

        return propose_script

    def _finish(self) -> SdkMcpTool[Any]:
        @tool(
            "finish",
            "Declare the work done. Call this only after a proposal has passed.",
            {
                "type": "object",
                "properties": {"note": {"type": "string", "default": ""}},
            },
        )
        async def finish(args: dict[str, Any]) -> dict[str, Any]:
            if self.accepted is None:
                return _err("nothing has been accepted yet; propose_script must pass first")
            self.finished = True
            self.finish_note = str(args.get("note") or "")
            return _ok("done")

        return finish
