"""The LLM side of the system: builder, repair, fallback, and the gateway they share.

Nothing outside this package should import the Claude Agent SDK. Everything here
talks to `contracts.LLMGateway`, which `AgentSdkGateway` implements today and a
plain Anthropic SDK tool runner can implement tomorrow.
"""

from __future__ import annotations

from .builder import BuildRequest, BuildResult, build
from .cost import PRICES, cost_of, price_for, total_cost, usage_row
from .fake import FakeGateway, ToolCall, Turn
from .fallback import FallbackResult, extract_rows
from .gateway import AgentSdkGateway, Budget, BudgetExceeded
from .repair import RepairRequest, RepairResult, classify_change, repair, unified_diff
from .tools_browser import ALLOWED_TOOLS, BrowserToolbox, TrialRun

__all__ = [
    "ALLOWED_TOOLS",
    "PRICES",
    "AgentSdkGateway",
    "BrowserToolbox",
    "Budget",
    "BudgetExceeded",
    "BuildRequest",
    "BuildResult",
    "FakeGateway",
    "FallbackResult",
    "RepairRequest",
    "RepairResult",
    "TrialRun",
    "ToolCall",
    "Turn",
    "build",
    "classify_change",
    "cost_of",
    "extract_rows",
    "price_for",
    "repair",
    "total_cost",
    "unified_diff",
    "usage_row",
]
