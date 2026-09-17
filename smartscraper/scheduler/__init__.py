"""Scheduling and background work.

    from smartscraper.scheduler import huey, tasks, cron

Importing ``tasks`` or ``cron`` registers them against the Huey instance in
``huey_app``; the worker imports both before starting the consumer.
"""

from __future__ import annotations

from smartscraper.scheduler.huey_app import get_huey, huey

__all__ = ["get_huey", "huey"]
