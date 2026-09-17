"""Validation: what decides whether a run passed.

A run that finished without an exception and returned wrong data is the failure
this subsystem exists to catch. Exceptions are the runner's business; pass or
fail is decided here.
"""

from __future__ import annotations

from smartscraper.validate.drift import baseline_for, load_history, row_count_band_rule
from smartscraper.validate.metrics import compute_metrics, null_rate, row_count
from smartscraper.validate.parse import ParsedMoney, parse_money, parse_value
from smartscraper.validate.schema import check_schema, infer_schema, schema_errors
from smartscraper.validate.validator import validate

__all__ = [
    "ParsedMoney",
    "baseline_for",
    "check_schema",
    "compute_metrics",
    "infer_schema",
    "load_history",
    "null_rate",
    "parse_money",
    "parse_value",
    "row_count",
    "row_count_band_rule",
    "schema_errors",
    "validate",
]
