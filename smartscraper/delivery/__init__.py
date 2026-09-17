"""Delivery: getting a run's records to wherever the owner wants them.

Public surface:

    from smartscraper.delivery import deliver_run, get_sink, retry_pending

Sinks register themselves on import of their module; ``get_sink`` imports the
built-in for a kind on first use, so optional dependencies (boto3, pyarrow)
only matter to the target that needs them.
"""

from __future__ import annotations

from smartscraper.delivery.base import (
    FAILED,
    HELD,
    PENDING,
    SENT,
    SKIPPED,
    BaseSink,
    DeliveryOutcome,
    deliver_one,
    deliver_run,
    get_sink,
    known_kinds,
    register,
    retry_pending,
    run_context,
)
from smartscraper.delivery.mapping import MapContext, MappingError, map_record, map_records

__all__ = [
    "BaseSink", "DeliveryOutcome", "MapContext", "MappingError",
    "FAILED", "HELD", "PENDING", "SENT", "SKIPPED",
    "deliver_one", "deliver_run", "get_sink", "known_kinds", "map_record", "map_records",
    "register", "retry_pending", "run_context",
]
