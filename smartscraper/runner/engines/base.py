"""The surface `steps.py` is written against.

`smartscraper.contracts.Engine` is the narrow part other subsystems see: open,
goto, probe, snapshot, close. Running a script needs more than that, so this
module widens it into `PageEngine` without changing the contract: every
`PageEngine` is still a structural `Engine`.

Two implementations exist. `HttpEngine` fetches HTML and queries it with lxml,
so it cannot click or type; `BrowserEngine` drives Playwright and can. Steps ask
`interactive` rather than checking the engine's name, so a third engine
slots in without editing `steps.py`.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from smartscraper.contracts import EngineCapabilityError, PageSnapshot, ProbeResult
from smartscraper.runner.locators import Selector, parse_selector


@runtime_checkable
class Element(Protocol):
    """One matched node. Deliberately tiny: extraction needs text, attributes,
    inner HTML, and the ability to search inside itself."""

    async def text(self) -> str | None: ...
    async def attr(self, name: str) -> str | None: ...
    async def inner_html(self) -> str | None: ...
    async def query(self, selector: Selector, *, limit: int | None = None) -> list[Element]: ...
    async def count(self, selector: Selector) -> int: ...


class PageEngine(Protocol):
    """One rung of the ladder, with everything a step can ask of it."""

    name: str
    interactive: bool
    current_url: str
    last_status: int | None
    last_headers: dict[str, str]

    async def open(self, *, proxy: str | None = None, profile: str | None = None,
                   headed: bool = False) -> None: ...
    async def goto(self, url: str, *, timeout_ms: int = 30_000,
                   wait_until: str = "domcontentloaded") -> int: ...
    async def content(self) -> str: ...
    async def query(self, selector: Selector, *, limit: int | None = None) -> list[Element]: ...
    async def count(self, selector: Selector) -> int: ...
    async def wait_for(self, selector: Selector | None, *, state: str = "visible",
                       timeout_ms: int = 15_000, url_contains: str | None = None) -> bool: ...
    async def click(self, selector: str | Selector, *, timeout_ms: int = 10_000) -> bool: ...
    async def fill(self, selector: str | Selector, value: str, *,
                   timeout_ms: int = 10_000) -> bool: ...
    async def select_option(self, selector: Selector, value: str, *,
                            timeout_ms: int = 10_000) -> None: ...
    async def hover(self, selector: Selector, *, timeout_ms: int = 10_000) -> None: ...
    async def press(self, key: str, *, selector: Selector | None = None,
                    timeout_ms: int = 10_000) -> None: ...
    async def scroll(self, *, to: str = "bottom", selector: str | Selector | None = None,
                     times: int = 1, pause_ms: int = 700) -> bool: ...
    async def screenshot(self, path: Any, *, full_page: bool = False) -> Any | None: ...
    async def probe(self, selector: str, *, limit: int = 5) -> ProbeResult: ...
    async def snapshot(self, *, budget_bytes: int = 40_000) -> PageSnapshot: ...
    async def close(self) -> None: ...


class InteractionUnsupported(EngineCapabilityError):
    """`EngineCapabilityError` with the op and engine attached.

    The escalation ladder reads it as "climb a rung", not as a broken script,
    which is why it carries which op was refused: a `fill` on the HTTP rung is
    a routing decision, not a bug in the script.
    """

    def __init__(self, op: str, engine: str) -> None:
        super().__init__(f"step `{op}` needs a browser; engine `{engine}` cannot do it")
        self.op = op
        self.engine = engine


def as_selector(selector: str | Selector) -> Selector:
    """Engines accept either form: `contracts.Engine` passes selector strings,
    while `steps.py` has already parsed one and passes the object."""
    return selector if isinstance(selector, Selector) else parse_selector(selector)


def trim(text: str, budget: int) -> tuple[str, bool]:
    """Cut to a byte budget, reporting whether anything was lost."""
    if budget <= 0 or len(text) <= budget:
        return text, False
    return text[:budget], True
