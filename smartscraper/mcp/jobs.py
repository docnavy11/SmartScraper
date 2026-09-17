"""The seam between the MCP server and the job queue.

The MCP tools must not import Huey. The scheduler does not exist yet and, when
it does, it is owned by another subsystem. So enqueueing goes through here: we
look for `smartscraper.scheduler.tasks.<name>` at call time and use it if it is
there. If it is not, the row is still written to the DB with status `queued` and
the job id comes back empty, which the tool reports honestly as
`queued_to_worker: false` rather than pretending the work has started.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _task(name: str) -> Any:
    try:
        from smartscraper.scheduler import tasks
    except Exception:  # noqa: BLE001 - the scheduler is optional at this layer
        return None
    return getattr(tasks, name, None)


def enqueue(name: str, /, **kwargs: Any) -> str | None:
    """Enqueue a scheduler task by name. Returns the queue's job id, or None
    when no worker task of that name exists."""
    fn = _task(name)
    if fn is None:
        log.info("no scheduler task %r; row written, nothing enqueued", name)
        return None
    try:
        result = fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 - a broken queue must not 500 the tool
        log.warning("enqueue of %r failed: %s", name, exc)
        return None
    # Huey returns a Result whose id is the task uuid; a plain callable may
    # return anything. Both are reported as a string or not at all.
    job_id = getattr(result, "id", None) or getattr(result, "task_id", None)
    return str(job_id) if job_id else None
