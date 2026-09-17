"""Per-field metrics for one row set.

`null_rate` is the number that decides most silent-breakage cases: a page whose
price selector stopped matching still returns rows, still raises nothing, and
shows up here as a null rate that jumped.

Null means: the key is absent, or its value is None, or it is an empty string
(after stripping). An empty list or dict counts as null too, because an
extract step that matched nothing produces exactly that.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from smartscraper.contracts import FieldMetric

__all__ = ["compute_metrics", "distinct_count", "field_names", "is_null", "null_rate", "row_count"]

_SAMPLE_CHARS = 80


def is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | dict | tuple | set):
        return len(value) == 0
    return False


def row_count(rows: Sequence[dict[str, Any]]) -> int:
    return len(rows)


def field_names(rows: Iterable[dict[str, Any]], extra: Iterable[str] = ()) -> list[str]:
    """Field names in first-seen order, with `extra` (declared fields) appended."""
    out: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            out.extend(k for k in row if k not in out)
    out.extend(k for k in extra if k not in out)
    return out


def null_rate(rows: Sequence[dict[str, Any]], field: str) -> float:
    """Fraction of rows where `field` is null. Zero rows means a zero rate."""
    if not rows:
        return 0.0
    nulls = sum(1 for row in rows if not isinstance(row, dict) or is_null(row.get(field)))
    return nulls / len(rows)


def _hashable(value: Any) -> Any:
    try:
        hash(value)
        return value
    except TypeError:
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except Exception:
            return repr(value)


def _sample(rows: Sequence[dict[str, Any]], field: str) -> str | None:
    for row in rows:
        if isinstance(row, dict) and not is_null(row.get(field)):
            text = row[field] if isinstance(row[field], str) else json.dumps(row[field], default=str)
            text = " ".join(str(text).split())
            return text if len(text) <= _SAMPLE_CHARS else text[: _SAMPLE_CHARS - 1] + "…"
    return None


def distinct_count(rows: Sequence[dict[str, Any]], field: str) -> int:
    """Distinct non-null values. Unhashable values are keyed by their JSON form."""
    seen = {
        _hashable(row.get(field))
        for row in rows
        if isinstance(row, dict) and not is_null(row.get(field))
    }
    return len(seen)


def compute_metrics(
    rows: Sequence[dict[str, Any]], *, fields: Iterable[str] = ()
) -> list[FieldMetric]:
    """One `FieldMetric` per field, over the union of observed and declared fields.

    A declared field that never appears in any row still gets a metric, with a
    null rate of 1.0 — that is the case worth seeing, not hiding.
    """
    return [
        FieldMetric(
            field=name,
            null_rate=null_rate(rows, name),
            distinct_count=distinct_count(rows, name),
            sample=_sample(rows, name),
        )
        for name in field_names(rows, fields)
    ]
