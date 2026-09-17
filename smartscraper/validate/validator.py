"""The judge. A run that raised nothing and still fails here is a failed run.

`validate` is a pure function of (rows, script, history). Nothing here touches
the database, the filesystem or the clock, so every rule is testable directly.

Rule order, fixed, because the report reads top to bottom in the UI:

1. min_rows
2. max_rows
3. required_fields
4. max_null_rate (one rule per configured field)
5. unique (one rule per configured field)
6. row_count_band
7. output_schema / field_set
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from smartscraper.contracts import RuleResult, ValidatorReport
from smartscraper.dsl.models import ScrapeScript, Validation
from smartscraper.validate.drift import row_count_band_rule
from smartscraper.validate.metrics import compute_metrics, is_null
from smartscraper.validate.schema import check_schema

__all__ = ["validate"]

_MAX_LISTED = 3


def _key(value: Any) -> Any:
    try:
        hash(value)
        return value
    except TypeError:
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except Exception:
            return repr(value)


def _min_rows(rows: Sequence[dict[str, Any]], v: Validation) -> RuleResult:
    return RuleResult(
        rule="min_rows",
        passed=len(rows) >= v.min_rows,
        measured=f"{len(rows)} rows",
        expected=f">= {v.min_rows} rows",
    )


def _max_rows(rows: Sequence[dict[str, Any]], v: Validation) -> RuleResult | None:
    if v.max_rows is None:
        return None
    return RuleResult(
        rule="max_rows",
        passed=len(rows) <= v.max_rows,
        measured=f"{len(rows)} rows",
        expected=f"<= {v.max_rows} rows",
    )


def _required_fields(rows: Sequence[dict[str, Any]], v: Validation) -> RuleResult | None:
    if not v.required_fields:
        return None
    offenders: dict[str, int] = {}
    for field in v.required_fields:
        bad = sum(1 for r in rows if not isinstance(r, dict) or is_null(r.get(field)))
        if bad:
            offenders[field] = bad
    if not offenders:
        return RuleResult(
            rule="required_fields",
            passed=True,
            measured=f"all present in {len(rows)} rows",
            expected=f"non-null in every row: {', '.join(v.required_fields)}",
        )
    detail = ", ".join(f"{f} null in {n}/{len(rows)}" for f, n in offenders.items())
    return RuleResult(
        rule="required_fields",
        passed=False,
        measured=detail,
        expected=f"non-null in every row: {', '.join(v.required_fields)}",
    )


def _null_rates(rows: Sequence[dict[str, Any]], v: Validation, metrics) -> list[RuleResult]:
    by_field = {m.field: m for m in metrics}
    out: list[RuleResult] = []
    for field, limit in v.max_null_rate.items():
        metric = by_field.get(field)
        rate = metric.null_rate if metric else (1.0 if rows else 0.0)
        nulls = round(rate * len(rows))
        out.append(
            RuleResult(
                rule=f"max_null_rate[{field}]",
                passed=rate <= limit + 1e-9,
                measured=f"{rate:.2f} ({nulls} of {len(rows)} rows null)",
                expected=f"<= {limit:.2f}",
            )
        )
    return out


def _unique(rows: Sequence[dict[str, Any]], v: Validation) -> list[RuleResult]:
    out: list[RuleResult] = []
    for field in v.unique:
        seen: dict[Any, int] = {}
        for row in rows:
            if isinstance(row, dict) and not is_null(row.get(field)):
                k = _key(row[field])
                seen[k] = seen.get(k, 0) + 1
        dupes = {k: n for k, n in seen.items() if n > 1}
        values = len(seen)
        if not dupes:
            out.append(
                RuleResult(
                    rule=f"unique[{field}]",
                    passed=True,
                    measured=f"{values} distinct of {values} non-null values",
                    expected="no duplicate values",
                )
            )
            continue
        listed = ", ".join(f"{k!r} x{n}" for k, n in list(dupes.items())[:_MAX_LISTED])
        more = f" (+{len(dupes) - _MAX_LISTED} more)" if len(dupes) > _MAX_LISTED else ""
        repeats = sum(dupes.values()) - len(dupes)
        out.append(
            RuleResult(
                rule=f"unique[{field}]",
                passed=False,
                measured=f"{len(dupes)} duplicated value(s), {repeats} extra row(s): {listed}{more}",
                expected="no duplicate values",
            )
        )
    return out


def validate(
    rows: Sequence[dict[str, Any]],
    script: ScrapeScript,
    history: Sequence[int] = (),
) -> ValidatorReport:
    """Judge one run's rows.

    `history` is the row counts of recent passing runs, most recent first, as
    `smartscraper.repo.recent_row_counts` returns them. An empty history skips
    the band rule rather than failing it.
    """
    rows = [r for r in rows]
    v = script.validation
    metrics = compute_metrics(rows, fields=script.field_names())

    rules: list[RuleResult] = [_min_rows(rows, v)]
    for maybe in (_max_rows(rows, v), _required_fields(rows, v)):
        if maybe is not None:
            rules.append(maybe)
    rules.extend(_null_rates(rows, v, metrics))
    rules.extend(_unique(rows, v))
    if v.row_count_band is not None:
        rules.append(row_count_band_rule(len(rows), v.row_count_band, history))
    rules.append(check_schema(rows, script))

    return ValidatorReport(
        passed=all(r.passed for r in rules),
        rules=rules,
        metrics=metrics,
        row_count=len(rows),
    )
