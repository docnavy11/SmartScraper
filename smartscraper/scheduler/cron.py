"""Turn each enabled Scraper's cron expression into a Huey periodic task.

Schedules live in the database, not in this file, so ``register_scrapers``
reads them and registers one periodic task per enabled scraper. Huey evaluates
a periodic task's validator once a minute, which is why a 5-field expression is
the right granularity.

``reload_schedules`` re-reads the table on a timer and adds, drops or replaces
tasks, so editing a schedule in the UI takes effect without restarting the
worker.

Two gates run at fire time, not at registration time, because both depend on
state that moves:

* ``config.max_concurrent_runs`` - a scraper that would exceed it is skipped
  for this tick, not queued. A queue of stale runs is worse than a missed one.
* the scraper's monthly budget - when ``config.stop_at_budget`` is set, a
  scraper whose month-to-date LLM spend has reached ``budget_usd_month`` does
  not fire. Only agent work costs money, but a run can trigger a repair, so the
  gate sits here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from huey import crontab
from sqlalchemy import func, select

from smartscraper import repo
from smartscraper.config import get_settings
from smartscraper.db.models import Run, RunStatus, Scraper
from smartscraper.db.session import get_session
from smartscraper.scheduler.huey_app import huey
from smartscraper.scheduler.tasks import run_scraper

log = logging.getLogger(__name__)

#: Statuses that occupy a concurrency slot.
ACTIVE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)

#: How often the worker re-reads the schedule table.
RELOAD_EVERY_MINUTES = 5

ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

#: name -> (expression, TaskWrapper) for everything this module registered.
_registered: dict[str, tuple[str, Any]] = {}


class CronError(ValueError):
    """The schedule string is not a 5-field cron expression."""


def parse_cron(expression: str):
    """Compile a cron expression into Huey's ``validate_datetime`` callable."""
    expr = (expression or "").strip()
    expr = ALIASES.get(expr.lower(), expr)
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(
            f"{expression!r} is not a cron expression: expected 5 fields "
            "(minute hour day month day_of_week) or an alias like @daily"
        )
    minute, hour, day, month, day_of_week = fields
    try:
        return crontab(minute=minute, hour=hour, day=day, month=month,
                       day_of_week=day_of_week, strict=True)
    except ValueError as exc:
        raise CronError(f"{expression!r} is not a valid cron expression: {exc}") from exc


def cron_matches(expression: str, when: datetime) -> bool:
    """True when ``when`` falls on the expression. Used by the tests and the UI."""
    return bool(parse_cron(expression)(when))


# --------------------------------------------------------------------------- gates
async def active_run_count(s: Any, scraper_id: int | None = None) -> int:
    q = select(func.count(Run.id)).where(Run.status.in_(ACTIVE_STATUSES))
    if scraper_id is not None:
        q = q.where(Run.scraper_id == scraper_id)
    return int(await s.scalar(q) or 0)


async def should_fire(s: Any, scraper: Scraper) -> tuple[bool, str]:
    """Decide whether this scraper may start a run right now."""
    settings = get_settings()
    if not scraper.enabled:
        return False, "scraper is disabled"
    if await active_run_count(s, scraper.id) > 0:
        return False, "a run of this scraper is already queued or running"
    running = await active_run_count(s)
    if running >= settings.max_concurrent_runs:
        return False, f"max_concurrent_runs reached ({running}/{settings.max_concurrent_runs})"
    if settings.stop_at_budget:
        budget = scraper.budget_usd_month or settings.default_scraper_budget
        spent = await repo.spend_month_to_date(s, scraper.id)
        if budget and spent >= budget:
            return False, f"budget exhausted: ${spent:.2f} of ${budget:.2f} this month"
    return True, ""


async def fire(scraper_id: int, *, trigger: str = "schedule") -> int | None:
    """Gate, then enqueue. Returns the enqueued task id, or None when skipped."""
    async with get_session() as s:
        scraper = await repo.get_scraper(s, scraper_id)
        if scraper is None:
            log.warning("scheduled scraper %s no longer exists", scraper_id)
            return None
        ok, reason = await should_fire(s, scraper)
        name = scraper.name
    if not ok:
        log.info("skipping %s: %s", name, reason)
        return None
    result = run_scraper(scraper_id, trigger)
    log.info("queued %s", name)
    return getattr(result, "id", None)


def fire_sync(scraper_id: int, *, trigger: str = "schedule") -> int | None:
    return asyncio.run(fire(scraper_id, trigger=trigger))


# --------------------------------------------------------------------------- registration
def _task_name(scraper_id: int, scraper_name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in scraper_name)
    return f"cron_{scraper_id}_{safe}"


def register_one(scraper_id: int, scraper_name: str, expression: str) -> Any:
    """Register (or replace) one scraper's periodic task. Returns the TaskWrapper."""
    validator = parse_cron(expression)
    name = _task_name(scraper_id, scraper_name)
    unregister(name)

    @huey.periodic_task(validator, name=name, retries=0)
    def _periodic(_id: int = scraper_id) -> None:
        fire_sync(_id)

    _registered[name] = (expression, _periodic)
    return _periodic


def unregister(name: str) -> bool:
    entry = _registered.pop(name, None)
    if entry is None:
        return False
    try:
        entry[1].unregister()
    except Exception:  # pragma: no cover - huey registry already clean
        log.debug("could not unregister %s", name, exc_info=True)
    return True


def unregister_all() -> None:
    for name in list(_registered):
        unregister(name)


def registered() -> dict[str, str]:
    """Registered task name -> the cron expression it was built from."""
    return {name: expr for name, (expr, _) in _registered.items()}


async def load_schedules() -> list[tuple[int, str, str]]:
    """(id, name, schedule) for every enabled scraper that has one."""
    async with get_session() as s:
        return [
            (sc.id, sc.name, sc.schedule)
            for sc in await repo.list_scrapers(s)
            if sc.enabled and sc.schedule
        ]


def register_scrapers(schedules: list[tuple[int, str, str]] | None = None) -> dict[str, str]:
    """Sync the registered periodic tasks with the schedule table.

    A scraper with an unparseable expression is logged and skipped; one bad
    expression must not stop the rest of the schedule from loading.
    """
    rows = schedules if schedules is not None else asyncio.run(load_schedules())
    wanted: dict[str, tuple[int, str, str]] = {}
    for scraper_id, name, expression in rows:
        wanted[_task_name(scraper_id, name)] = (scraper_id, name, expression)

    for stale in set(_registered) - set(wanted):
        log.info("dropping schedule %s", stale)
        unregister(stale)

    for task_name, (scraper_id, name, expression) in wanted.items():
        if _registered.get(task_name, (None,))[0] == expression:
            continue
        try:
            register_one(scraper_id, name, expression)
            log.info("scheduled %s at %r", name, expression)
        except CronError as exc:
            log.error("scraper %s has a bad schedule: %s", name, exc)
    return registered()


@huey.periodic_task(crontab(minute=f"*/{RELOAD_EVERY_MINUTES}"), name="reload_schedules", retries=0)
def reload_schedules() -> dict[str, str]:
    """Pick up schedule edits without a worker restart."""
    return register_scrapers()
