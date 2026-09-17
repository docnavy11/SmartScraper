"""'What would this selector give me?'

The builder agent asks this before committing a selector to a script, and the
probe pane in the script editor asks it on the human's behalf. Both want the
same three numbers: how many nodes matched, how many carried a usable value,
and what the first few look like.

`non_empty` is the one that matters. A selector matching 40 nodes that all yield
an empty string is worse than one matching 12 that yield text, and only this
distinction tells the two apart before a run burns a schedule slot.
"""

from __future__ import annotations

from typing import Any

from smartscraper.contracts import ProbeResult
from smartscraper.runner.engines.base import Element
from smartscraper.runner.locators import (
    Resolution,
    SelectorSyntaxError,
    candidates_of,
    parse_selector,
    resolve_first,
)
from smartscraper.runner.parsing import clean_text

SAMPLE_CHARS = 160


async def probe_engine(
    engine: Any,
    selector: str,
    *,
    limit: int = 5,
    attr: str = "text",
) -> ProbeResult:
    """Run one selector against an open engine and report what it found."""
    try:
        sel = parse_selector(selector)
    except SelectorSyntaxError as exc:
        return ProbeResult(selector=selector, matched=0, non_empty=0, error=str(exc))
    try:
        elements = await engine.query(sel)
    except Exception as exc:
        return ProbeResult(selector=selector, matched=0, non_empty=0, error=f"{type(exc).__name__}: {exc}")
    return await _summarise(selector, elements, limit=limit, attr=attr)


async def probe_elements(
    elements: list[Element],
    selector: str,
    *,
    limit: int = 5,
    attr: str = "text",
) -> ProbeResult:
    """Same summary for an already-resolved node list."""
    return await _summarise(selector, elements, limit=limit, attr=attr)


async def _summarise(selector: str, elements: list[Any], *, limit: int, attr: str) -> ProbeResult:
    matched = len(elements)
    non_empty = 0
    samples: list[str] = []
    # Reading every node's text is the point of the probe, but a 5000-row page
    # should not cost 5000 round trips on the browser engine.
    for el in elements[: max(limit, 50)]:
        try:
            value = clean_text(await el.attr(attr))
        except Exception:
            value = None
        if value:
            non_empty += 1
            if len(samples) < limit:
                samples.append(value[:SAMPLE_CHARS])
    return ProbeResult(selector=selector, matched=matched, non_empty=non_empty, samples=samples)


async def probe_with_fallbacks(
    engine: Any,
    selector: str,
    fallbacks: list[str] | None = None,
    *,
    limit: int = 5,
    attr: str = "text",
) -> tuple[ProbeResult, Resolution | None]:
    """Probe the whole candidate ladder and report which rung answered.

    A repair agent reads the `Resolution` to learn that the primary selector is
    dead while a fallback still works, which is a different repair from "every
    selector is dead".
    """
    resolution = await resolve_first(selector, fallbacks, engine.count)
    if resolution is None:
        tried = ", ".join(candidates_of(selector, fallbacks))
        return (
            ProbeResult(selector=selector, matched=0, non_empty=0, error=f"no match for: {tried}"),
            None,
        )
    result = await probe_engine(engine, resolution.used, limit=limit, attr=attr)
    return result, resolution


def describe(result: ProbeResult) -> str:
    """One line for a log or an agent's tool output."""
    if result.error:
        return f"{result.selector}: error — {result.error}"
    if not result.matched:
        return f"{result.selector}: no match"
    head = "; ".join(result.samples[:3])
    return (
        f"{result.selector}: {result.matched} matched, {result.non_empty} non-empty"
        + (f" — {head}" if head else "")
    )
