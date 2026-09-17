"""Comparison of this run against recent history.

Only one history rule exists today: `row_count_band`. A page that quietly starts
serving half its listings raises nothing, parses fine, validates against the
schema, and is caught here or nowhere.

The rule itself is a pure function over a list of past row counts, so it is
testable without a database. `load_history` is the only part that touches one.
"""

from __future__ import annotations

from collections.abc import Sequence

from smartscraper.contracts import RuleResult
from smartscraper.dsl.models import RowCountBand

__all__ = ["baseline_for", "history_size", "load_history", "row_count_band_rule"]

_SIZES = {"last_run": 1, "last_5_runs": 5, "last_10_runs": 10}


def history_size(band: RowCountBand) -> int:
    """How many past runs this band looks at."""
    return _SIZES.get(str(band.relative_to), 5)


def baseline_for(history: Sequence[int], band: RowCountBand) -> float | None:
    """Mean row count over the window, or None when there is no history.

    `history` is most-recent-first, the order `repo.recent_row_counts` returns.
    """
    window = [int(n) for n in list(history)[: history_size(band)] if n is not None]
    if not window:
        return None
    return sum(window) / len(window)


def row_count_band_rule(
    row_count: int, band: RowCountBand, history: Sequence[int] = ()
) -> RuleResult:
    """Is this run's row count within `tolerance` of the recent baseline?

    Tolerance is a fraction of the baseline and the edge is exclusive: with
    tolerance 0.5 a baseline of 120 fails at exactly 60 rows, because a page
    that halves is the case this rule exists for. A count equal to the baseline
    always passes, so tolerance 0 means "exactly the baseline". A baseline of
    zero passes any count; there is nothing to drift away from.
    """
    window = history_size(band)
    label = f"{band.relative_to} +/- {band.tolerance:.2f}"
    baseline = baseline_for(history, band)

    if baseline is None:
        return RuleResult(
            rule="row_count_band",
            passed=True,
            measured=f"{row_count} rows, no passing run in history",
            expected=f"within {label} (skipped: needs at least 1 of the last {window} runs)",
        )
    if baseline == 0:
        return RuleResult(
            rule="row_count_band",
            passed=True,
            measured=f"{row_count} rows, baseline 0",
            expected=f"within {label} (skipped: baseline is 0)",
        )

    low = baseline * (1.0 - band.tolerance)
    high = baseline * (1.0 + band.tolerance)
    delta = (row_count - baseline) / baseline
    passed = delta == 0.0 or abs(delta) < band.tolerance
    return RuleResult(
        rule="row_count_band",
        passed=passed,
        measured=f"{row_count} rows, baseline {baseline:.1f} ({delta:+.0%})",
        expected=f"{low:.1f} to {high:.1f} rows, edges excluded ({label})",
    )


async def load_history(session, scraper_id: int, band: RowCountBand) -> list[int]:
    """Row counts of the recent passing runs, most recent first.

    Thin wrapper over `smartscraper.repo.recent_row_counts` so callers do not
    have to work out the window size from the band.
    """
    from smartscraper import repo

    return await repo.recent_row_counts(session, scraper_id, n=history_size(band))
