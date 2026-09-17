"""The Huey instance every scheduler task is registered against.

SqliteHuey writes to ``data/huey.db``, a second file beside the metadata DB, so
a queue wipe never touches run history. Huey 3.4's optional task history
(``huey[stats]``) is enabled when its dependency is present; it needs peewee,
which is not a hard dependency here, so absence is logged and ignored.

Set ``SS_HUEY_IMMEDIATE=1`` to run tasks inline in the calling process, which is
how the tests drive the queue without a worker.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from huey import SqliteHuey

from smartscraper.config import get_settings

log = logging.getLogger(__name__)

_huey: SqliteHuey | None = None
_stats_enabled: bool | None = None


def queue_path() -> Path:
    return get_settings().data_dir / "huey.db"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def make_huey(*, path: Path | None = None, immediate: bool | None = None) -> SqliteHuey:
    settings = get_settings()
    settings.ensure_dirs()
    if immediate is None:
        immediate = _truthy(os.environ.get("SS_HUEY_IMMEDIATE"))
    instance = SqliteHuey(
        name="smartscraper",
        filename=str(path or queue_path()),
        immediate=immediate,
        results=True,
        utc=True,
    )
    return instance


def get_huey() -> SqliteHuey:
    global _huey
    if _huey is None:
        _huey = make_huey()
    return _huey


#: Module-level instance. Task modules decorate against this.
huey = get_huey()


def enable_stats() -> bool:
    """Turn on Huey's SQLite task history if the optional extra is installed.

    Returns True when it is running. Never raises: history is a nicety, and a
    missing optional dependency must not stop the worker.
    """
    global _stats_enabled
    if _stats_enabled is not None:
        return _stats_enabled
    try:
        from huey.contrib.stats import enable_stats as _enable
    except ImportError as exc:
        log.info("huey task history disabled: %s (install huey[stats] for it)", exc)
        _stats_enabled = False
        return False
    try:
        _enable(huey, str(get_settings().data_dir / "huey_stats.db"))
    except Exception as exc:  # peewee present but unhappy, e.g. a locked file
        log.warning("huey task history could not start: %r", exc)
        _stats_enabled = False
        return False
    _stats_enabled = True
    log.info("huey task history enabled")
    return True
