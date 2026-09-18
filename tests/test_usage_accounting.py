"""What a build actually cost, and which model actually did it.

This feeds the budget guard, so being wrong here means the cap never trips.

The harness runs more than one model: a build asking for Opus also showed about
nine thousand Haiku input tokens from the CLI's own internal work. The first
implementation summed tokens across every model, priced the total at a single
rate, and labelled the row with whichever model had the most tokens. On a short
build Haiku's input count exceeded Opus's, so the row said Haiku and 1.4 million
Opus cache reads were billed at Haiku's rate. One build recorded $0.0998 against
a real $1.979.

The payload below is copied from a real run, not invented.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from smartscraper.agents.gateway import usage_from_result

# data/runs/build-a retailer-dryers-final/agent.jsonl
REAL = {
    "claude-haiku-4-5-20251001": {
        "inputTokens": 9406, "outputTokens": 16, "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0, "costUSD": 0.009486,
        "canonicalModel": "claude-haiku-4-5",
    },
    "claude-opus-5": {
        "inputTokens": 66, "outputTokens": 23696, "cacheReadInputTokens": 1418544,
        "cacheCreationInputTokens": 66753, "costUSD": 1.9695319999999998,
        "canonicalModel": "claude-opus-5",
    },
}
REAL_TOTAL = 1.9790179999999997


def result(model_usage=None, total=0.0, usage=None, turns=14):
    return SimpleNamespace(
        num_turns=turns, total_cost_usd=total, usage=usage, model_usage=model_usage
    )


def test_the_recorded_cost_is_what_was_actually_charged():
    u = usage_from_result(result(REAL, REAL_TOTAL), "claude-opus-5")
    assert u.cost_usd == pytest.approx(REAL_TOTAL, abs=1e-4)
    assert u.cost_source == "harness"


def test_the_row_is_labelled_with_the_model_that_was_asked_for():
    """Not with whichever model happened to use the most tokens."""
    u = usage_from_result(result(REAL, REAL_TOTAL), "claude-opus-5")
    assert u.model == "claude-opus-5"


def test_a_short_build_is_not_mislabelled_as_haiku():
    """The exact shape that produced five wrong rows: Haiku's input count beats
    Opus's input-plus-output, because Opus's work sits in cache reads."""
    short = {
        "claude-haiku-4-5": {"inputTokens": 9406, "outputTokens": 16, "costUSD": 0.0095,
                             "canonicalModel": "claude-haiku-4-5"},
        "claude-opus-5": {"inputTokens": 40, "outputTokens": 3000,
                          "cacheReadInputTokens": 200000, "costUSD": 0.42,
                          "canonicalModel": "claude-opus-5"},
    }
    u = usage_from_result(result(short, 0.4295), "claude-opus-5")

    assert u.model == "claude-opus-5"
    assert u.cost_usd == pytest.approx(0.4295, abs=1e-4)


def test_each_model_is_priced_on_its_own_slice():
    u = usage_from_result(result(REAL, REAL_TOTAL), "claude-opus-5")

    assert set(u.by_model) == {"claude-haiku-4-5", "claude-opus-5"}
    assert u.by_model["claude-haiku-4-5"]["cost_usd"] == pytest.approx(0.009486, abs=1e-5)
    assert u.by_model["claude-opus-5"]["cost_usd"] == pytest.approx(1.96953, abs=1e-4)
    # and the slices add up to the bill
    assert sum(d["cost_usd"] for d in u.by_model.values()) == pytest.approx(REAL_TOTAL, abs=1e-3)


def test_tokens_are_still_summed_across_models():
    u = usage_from_result(result(REAL, REAL_TOTAL), "claude-opus-5")

    assert u.input_tokens == 9406 + 66
    assert u.output_tokens == 16 + 23696
    assert u.cache_read_tokens == 1418544


def test_our_own_prices_are_used_when_the_harness_gives_none():
    no_cost = {
        "claude-opus-5": {"inputTokens": 1_000_000, "outputTokens": 0,
                          "canonicalModel": "claude-opus-5"},
    }
    u = usage_from_result(result(no_cost, 0.0), "claude-opus-5")

    assert u.cost_source == "computed"
    assert u.cost_usd == pytest.approx(5.0, abs=0.01)   # $5 per Mtok input


def test_a_model_we_cannot_price_is_not_recorded_as_free_when_the_harness_knows():
    unknown = {
        "claude-future-9": {"inputTokens": 500_000, "outputTokens": 10_000,
                            "canonicalModel": "claude-future-9"},
    }
    u = usage_from_result(result(unknown, 7.25), "claude-future-9")

    assert u.cost_usd == pytest.approx(7.25, abs=1e-4), "the harness total must win"
    assert u.cost_source == "harness"


def test_a_disagreement_between_our_table_and_the_harness_is_logged(caplog):
    """A stale price table should announce itself rather than quietly drift."""
    import logging

    wrong = {
        "claude-opus-5": {"inputTokens": 1_000_000, "outputTokens": 0,
                          "canonicalModel": "claude-opus-5"},
    }
    # Name the logger and force propagation: another test in the suite configures
    # logging for the app, and a bare at_level then catches nothing.
    gateway_log = logging.getLogger("smartscraper.agents.gateway")
    gateway_log.propagate = True
    caplog.set_level(logging.WARNING, logger="smartscraper.agents.gateway")

    usage_from_result(result(wrong, 50.0), "claude-opus-5")

    assert any("cost disagreement" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]


def test_the_messages_api_shape_still_works_when_there_is_no_breakdown():
    plain = {"input_tokens": 1000, "output_tokens": 500,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    u = usage_from_result(result(None, 0.0, usage=plain), "claude-sonnet-5")

    assert u.input_tokens == 1000
    assert u.output_tokens == 500
    assert u.cost_usd > 0
