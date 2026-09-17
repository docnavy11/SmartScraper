"""Shared offline doubles for the agent tests. No API key, no network, no browser."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from smartscraper.config import Settings
from smartscraper.contracts import EngineCapabilityError, PageSnapshot, ProbeResult
from smartscraper.dsl.models import ScrapeScript

PAGE_HTML = """
<html><head><title>Widgets</title><style>.x{}</style></head>
<body><main>
  <div class="product"><h3>Alpha</h3><span class="price">$10.00</span></div>
  <div class="product"><h3>Beta</h3><span class="price">$12.50</span></div>
  <div class="product"><h3>Gamma</h3><span class="price">$8.25</span></div>
</main><script>var a=1;</script></body></html>
"""

ROWS: list[dict[str, Any]] = [
    {"name": "Alpha", "price": "$10.00"},
    {"name": "Beta", "price": "$12.50"},
    {"name": "Gamma", "price": "$8.25"},
]

SCRIPT: dict[str, Any] = {
    "version": 1,
    "engine": "playwright",
    "steps": [
        {"op": "goto", "url": "https://example.com/products"},
        {"op": "wait_for", "selector": "css=.product", "timeout_ms": 15000},
        {
            "op": "extract_list",
            "selector": "css=.product",
            "as": "products",
            "fields": {
                "name": {"selector": "h3", "attr": "text"},
                "price": {"selector": ".price", "attr": "text", "parse": "money"},
            },
        },
        {"op": "emit", "from": "products"},
    ],
}


def script_dict(**overrides: Any) -> dict[str, Any]:
    out = copy.deepcopy(SCRIPT)
    out.update(overrides)
    return out


def script(**overrides: Any) -> ScrapeScript:
    return ScrapeScript.model_validate(script_dict(**overrides))


def settings_for(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        scrapers_dir=tmp_path / "scrapers",
        db_path=tmp_path / "data" / "test.db",
        **overrides,
    )


class FakeEngine:
    """A browser rung: implements the whole contracts.Engine surface."""

    name = "fake"
    interactive = True

    def __init__(
        self,
        *,
        html: str = PAGE_HTML,
        probes: dict[str, ProbeResult] | None = None,
        status: int = 200,
        hits: bool = True,
    ) -> None:
        self.html = html
        self.probes = probes or {}
        self.status = status
        self.hits = hits
        self.opened = False
        self.closed = False
        self.gotos: list[str] = []
        self.actions: list[tuple[str, tuple[Any, ...]]] = []

    async def open(self, *, proxy: str | None, profile: str | None, headed: bool) -> None:
        self.opened = True

    async def goto(self, url: str, *, timeout_ms: int) -> int:
        self.gotos.append(url)
        return self.status

    async def probe(self, selector: str, *, limit: int = 5) -> ProbeResult:
        if selector in self.probes:
            return self.probes[selector]
        return ProbeResult(selector=selector, matched=0, non_empty=0, samples=[])

    async def snapshot(self, *, budget_bytes: int) -> PageSnapshot:
        html = self.html[:budget_bytes]
        return PageSnapshot(
            url="https://example.com/products",
            title="Widgets",
            accessibility_tree="main\n  list\n    listitem Alpha $10.00",
            html=html,
            truncated=len(html) < len(self.html),
        )

    async def close(self) -> None:
        self.closed = True

    async def click(self, selector: str, *, timeout_ms: int = 10_000) -> bool:
        self.actions.append(("click", (selector,)))
        return self.hits

    async def fill(self, selector: str, value: str, *, timeout_ms: int = 10_000) -> bool:
        self.actions.append(("fill", (selector, value)))
        return self.hits

    async def scroll(
        self, *, to: str = "bottom", selector: str | None = None, times: int = 1
    ) -> bool:
        self.actions.append(("scroll", (to, times)))
        return self.hits


class HttpEngine(FakeEngine):
    """The cheap rung: no live page, so every interaction escalates."""

    name = "http"
    interactive = False

    async def click(self, selector: str, *, timeout_ms: int = 10_000) -> bool:
        raise EngineCapabilityError("the http engine has no live page")

    async def fill(self, selector: str, value: str, *, timeout_ms: int = 10_000) -> bool:
        raise EngineCapabilityError("the http engine has no live page")

    async def scroll(
        self, *, to: str = "bottom", selector: str | None = None, times: int = 1
    ) -> bool:
        raise EngineCapabilityError("the http engine has no live page")


def good_probes() -> dict[str, ProbeResult]:
    return {
        "css=.product": ProbeResult("css=.product", matched=3, non_empty=3, samples=["Alpha $10.00"]),
        "h3": ProbeResult("h3", matched=3, non_empty=3, samples=["Alpha", "Beta", "Gamma"]),
        ".price": ProbeResult(".price", matched=3, non_empty=3, samples=["$10.00", "$12.50"]),
    }
