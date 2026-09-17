"""Entry point for ``smartscraper worker``.

One process: Huey's consumer, running the tasks in ``tasks.py`` and evaluating
the periodic tasks registered by ``cron.py``. Runs themselves are subprocesses
of this process, so the worker count here bounds how many can be in flight,
alongside ``config.max_concurrent_runs``.
"""

from __future__ import annotations

import logging
import sys

from smartscraper.config import get_settings
from smartscraper.scheduler import cron, tasks  # noqa: F401  (registers the tasks)
from smartscraper.scheduler.huey_app import enable_stats, huey

log = logging.getLogger(__name__)


def build_consumer(*, workers: int | None = None, periodic: bool = True):
    settings = get_settings()
    return huey.create_consumer(
        workers=workers or max(1, settings.max_concurrent_runs),
        worker_type="thread",
        periodic=periodic,
        initial_delay=0.1,
        max_delay=2.0,
        flush_locks=True,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stderr,
    )
    settings = get_settings()
    settings.ensure_dirs()
    enable_stats()

    schedules = cron.register_scrapers()
    log.info("worker starting: %s scheduled scraper(s), queue at %s",
             len(schedules), settings.data_dir / "huey.db")

    consumer = build_consumer()
    try:
        consumer.run()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        log.info("worker stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
