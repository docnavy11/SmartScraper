"""Cost arithmetic against the price table the owner supplied on 2026-09-17."""

from __future__ import annotations

import pytest

from smartscraper.agents.cost import (
    PRICES,
    UnknownModelError,
    cost_of,
    price_for,
    priced,
    total_cost,
    usage_row,
)
from smartscraper.contracts import Usage


def test_table_matches_the_quoted_prices():
    assert (PRICES["claude-opus-5"].input, PRICES["claude-opus-5"].output) == (5.00, 25.00)
    assert PRICES["claude-opus-5"].cache_read == 0.50
    assert (PRICES["claude-sonnet-5"].input, PRICES["claude-sonnet-5"].output) == (2.00, 10.00)
    assert PRICES["claude-sonnet-5"].cache_read == 0.20
    assert (PRICES["claude-haiku-4-5"].input, PRICES["claude-haiku-4-5"].output) == (1.00, 5.00)
    assert PRICES["claude-haiku-4-5"].cache_read == 0.10


def test_cache_writes_are_one_and_a_quarter_times_input():
    """Anthropic's published 5-minute cache-write multiplier, pricing page 2026-09-17."""
    for model, price in PRICES.items():
        assert price.cache_write == pytest.approx(price.input * 1.25), model
    usage = Usage(model="claude-opus-5", cache_write_tokens=1_000_000)
    assert cost_of(usage) == pytest.approx(6.25)


def test_opus_one_million_in_one_million_out():
    usage = Usage(model="claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_of(usage) == pytest.approx(30.00)


def test_cache_reads_are_a_tenth_of_input_on_opus():
    usage = Usage(model="claude-opus-5", cache_read_tokens=1_000_000)
    assert cost_of(usage) == pytest.approx(0.50)


def test_sonnet_mixed_call():
    usage = Usage(
        model="claude-sonnet-5",
        input_tokens=120_000,
        output_tokens=8_000,
        cache_read_tokens=400_000,
    )
    expected = (120_000 * 2.00 + 8_000 * 10.00 + 400_000 * 0.20) / 1_000_000
    assert cost_of(usage) == pytest.approx(expected)
    assert cost_of(usage) == pytest.approx(0.40)


def test_haiku_alias_is_priced():
    usage = Usage(model="claude-haiku-4-5-20251001", input_tokens=1_000_000)
    assert cost_of(usage) == pytest.approx(1.00)
    assert price_for("haiku") is PRICES["claude-haiku-4-5"]


def test_empty_usage_costs_nothing():
    assert cost_of(Usage(model="claude-opus-5")) == 0.0


def test_unknown_model_raises_rather_than_reporting_zero():
    with pytest.raises(UnknownModelError):
        cost_of(Usage(model="gpt-whatever", input_tokens=1_000))


def test_priced_fills_the_field_and_totals_add_up():
    a = priced(Usage(model="claude-opus-5", output_tokens=40_000))
    b = Usage(model="claude-sonnet-5", output_tokens=40_000)
    assert a.cost_usd == pytest.approx(1.00)
    assert total_cost([a, b]) == pytest.approx(1.00 + 0.40)


def test_usage_row_carries_every_field_onto_the_db_model():
    usage = priced(
        Usage(
            model="claude-opus-5",
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=30,
            cache_write_tokens=40,
            turns=3,
        )
    )
    row = usage_row(usage, agent="builder", scraper_id=7, run_id=11)
    assert (row.agent, row.model, row.scraper_id, row.run_id) == ("builder", "claude-opus-5", 7, 11)
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_write_tokens) == (
        10,
        20,
        30,
        40,
    )
    assert row.turns == 3
    assert row.cost_usd == pytest.approx(usage.cost_usd)
