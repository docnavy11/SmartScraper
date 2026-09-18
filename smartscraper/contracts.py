"""Interfaces between subsystems.

Every module below implements one of these. Nothing imports another subsystem's
internals; they meet here. Owners:
  Engine, StepContext .......... smartscraper/runner/
  ValidatorReport, Validator ... smartscraper/validate/
  LLMGateway, AgentResult ...... smartscraper/agents/
  Sink ......................... smartscraper/delivery/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# --------------------------------------------------------------------------- runner


@dataclass(slots=True)
class ProbeResult:
    """Answer to 'what would this selector give me?'. Used by the builder agent
    and by the human probe pane in the script editor."""

    selector: str
    matched: int
    non_empty: int
    samples: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class PageSnapshot:
    url: str
    title: str
    accessibility_tree: str
    html: str
    screenshot_path: Path | None = None
    truncated: bool = False


class BlockReason:
    CLOUDFLARE = "cloudflare"
    DATADOME = "datadome"
    PERIMETERX = "perimeterx"
    AKAMAI = "akamai"
    CAPTCHA = "captcha"
    RATE_LIMIT = "rate_limit"
    FORBIDDEN = "forbidden"
    EMPTY_JS_ONLY = "empty_js_only"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class RunOutcome:
    rows: list[dict[str, Any]]
    engine_used: str
    escalation_level: int
    proxy_used: str | None = None
    blocked: bool = False
    block_reason: str | None = None
    error: str | None = None
    artifacts: dict[str, Path] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)


class EngineCapabilityError(RuntimeError):
    """Raised by an engine asked to do something it structurally cannot.

    The HTTP engine cannot click: there is no live page. It must raise this
    rather than return a falsy value, so a script that needs interaction
    escalates to a browser rung instead of silently extracting nothing.
    """


@runtime_checkable
class Engine(Protocol):
    """One rung of the escalation ladder.

    Every engine implements the whole surface. An engine that cannot perform an
    interaction raises EngineCapabilityError from that method; it does not omit
    the method, because callers check behaviour, not attribute presence.
    """

    name: str
    interactive: bool

    async def open(self, *, proxy: str | None, profile: str | None, headed: bool) -> None: ...
    async def goto(self, url: str, *, timeout_ms: int) -> int: ...
    async def probe(self, selector: str, *, limit: int = 5) -> ProbeResult: ...
    async def snapshot(self, *, budget_bytes: int) -> PageSnapshot: ...
    async def close(self) -> None: ...

    # -- interaction: the builder agent needs all three --------------------
    async def click(self, selector: str, *, timeout_ms: int = 10_000) -> bool: ...
    async def fill(self, selector: str, value: str, *, timeout_ms: int = 10_000) -> bool: ...
    async def scroll(
        self, *, to: str = "bottom", selector: str | None = None, times: int = 1
    ) -> bool: ...


# --------------------------------------------------------------------------- validation


@dataclass(slots=True)
class RuleResult:
    rule: str
    passed: bool
    measured: str
    expected: str


@dataclass(slots=True)
class FieldMetric:
    field: str
    null_rate: float
    distinct_count: int
    sample: str | None = None


@dataclass(slots=True)
class ValidatorReport:
    """A run that raised nothing but fails here is a failed run."""

    passed: bool
    rules: list[RuleResult] = field(default_factory=list)
    metrics: list[FieldMetric] = field(default_factory=list)
    row_count: int = 0

    @property
    def failures(self) -> list[RuleResult]:
        return [r for r in self.rules if not r.passed]

    def summary(self) -> str:
        if self.passed:
            return f"{self.row_count} rows, {len(self.rules)} of {len(self.rules)} rules passed"
        names = ", ".join(r.rule for r in self.failures)
        return f"{len(self.failures)} of {len(self.rules)} rules failed: {names}"


# --------------------------------------------------------------------------- agents


@dataclass(slots=True)
class Usage:
    """What one agent call consumed.

    `model` is the model that was asked for. A harness may also run other models
    of its own for internal work, so `by_model` carries the real breakdown and
    `cost_usd` is the sum across all of them. Summing tokens across models and
    pricing them at one rate understates or overstates the bill, and this is what
    the budget guard reads.
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    turns: int = 0
    cost_usd: float = 0.0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)
    cost_source: str = "computed"      # computed | harness


@dataclass(slots=True)
class AgentResult:
    ok: bool
    text: str = ""
    data: dict[str, Any] | None = None
    usage: Usage | None = None
    transcript_path: Path | None = None
    error: str | None = None


@runtime_checkable
class LLMGateway(Protocol):
    """One seam between the agents and whatever runs them.

    Backed by the Claude Agent SDK today. Swapping to the Anthropic SDK tool
    runner must not require touching builder.py, repair.py or fallback.py.
    """

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
    ) -> AgentResult: ...

    async def extract(
        self,
        *,
        text: str,
        output_schema: dict[str, Any],
        model: str,
        instructions: str = "",
    ) -> AgentResult: ...


# --------------------------------------------------------------------------- delivery


@dataclass(slots=True)
class DeliveryResult:
    ok: bool
    rows_sent: int = 0
    detail: str = ""
    retryable: bool = True
    error: str | None = None


@runtime_checkable
class Sink(Protocol):
    kind: str

    async def send(
        self,
        rows: list[dict[str, Any]],
        *,
        config: dict[str, Any],
        run_id: int,
        scraper: str,
        meta: dict[str, Any],
    ) -> DeliveryResult: ...


# --------------------------------------------------------------------------- misc


@dataclass(slots=True)
class Freshness:
    """What Records and the MCP get_results tool report so a consumer can tell
    provisional data from clean data."""

    last_clean_run_at: datetime | None
    last_run_at: datetime | None
    stale: bool
    reason: str = ""
