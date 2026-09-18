"""Pricing and cost accounting for every LLM call the system makes.

Prices are USD per million tokens, read off Anthropic's pricing page on
2026-09-17. They are configuration, not a measurement: if Anthropic changes a
price this table is wrong until someone edits it. Cache writes are 1.25x input
(5-minute TTL); see the note on PRICES.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts import Usage

MILLION = 1_000_000


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


# Cache writes are priced at 1.25x the model's input rate. That is the published
# multiplier for a 5-minute cache write, read off Anthropic's pricing page on
# 2026-09-17 in the same pass that produced the per-model rates above. A 1-hour
# write is 2x input; add a second column here if a TTL of 1h is ever used.
PRICES: dict[str, Price] = {
    "claude-opus-5": Price(input=5.00, output=25.00, cache_read=0.50, cache_write=6.25),
    "claude-sonnet-5": Price(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
    "claude-haiku-4-5": Price(input=1.00, output=5.00, cache_read=0.10, cache_write=1.25),
}

# Aliases the Agent SDK / CLI may report instead of the id we asked for.
ALIASES: dict[str, str] = {
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}


class UnknownModelError(KeyError):
    """Raised when a model has no entry in PRICES.

    Deliberately loud: a silent 0.0 would understate spend and let a budget
    cap pass a run it should have stopped.
    """


def price_for(model: str) -> Price:
    key = ALIASES.get(model, model)
    if key in PRICES:
        return PRICES[key]
    raise UnknownModelError(f"no price for model {model!r}; add it to smartscraper.agents.cost.PRICES")


def cost_of(usage: Usage) -> float:
    """USD for one Usage record. Does not mutate it."""
    p = price_for(usage.model)
    return (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_read_tokens * p.cache_read
        + usage.cache_write_tokens * p.cache_write
    ) / MILLION


def priced(usage: Usage) -> Usage:
    """Fill in `cost_usd` and return the same object."""
    usage.cost_usd = cost_of(usage)
    return usage


def total_cost(usages: list[Usage]) -> float:
    return sum(cost_of(u) for u in usages)


def usage_row(
    usage: Usage,
    *,
    agent: str,
    scraper_id: int | None = None,
    run_id: int | None = None,
):
    """Build (do not commit) an `LlmUsage` row for this usage record.

    The caller owns the session: `session.add(usage_row(...))`.
    """
    from ..db.models import LlmUsage

    return LlmUsage(
        scraper_id=scraper_id,
        run_id=run_id,
        agent=agent,
        model=usage.model,
        by_model=dict(getattr(usage, "by_model", {}) or {}),
        cost_source=getattr(usage, "cost_source", "computed"),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cost_usd=usage.cost_usd or cost_of(usage),
        turns=usage.turns,
    )
