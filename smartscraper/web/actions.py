"""Interaction endpoints call these, not the subsystems directly.

The UI is wired end to end today. Where the owning subsystem does not exist
yet, the function below does the part that is unambiguously the web layer's
(the DB row the screen reads back, plus the audit entry) and leaves an explicit
TODO for the part that belongs to the runner, the scheduler or delivery. None
of these TODOs silently succeed: each one is listed in the web report.

Owners once they land:
  enqueue_run, cancel_run ......... smartscraper/scheduler/tasks.py
  start_builder ................... smartscraper/agents/builder.py
  retry_delivery, test_target ..... smartscraper/delivery/

Promotion is no longer one of them: approving a repair goes through
smartscraper.pipeline.promote_version, which owns both the table and the YAML
file, so the web UI, MCP and the repair agent all land in the same state.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper import repo
from smartscraper.db.models import Repair, Run, RunStatus, Scraper, ScriptVersion, VersionStatus
from smartscraper.pipeline import promote_version

log = logging.getLogger("smartscraper.web.actions")

ACTOR = "you"


# --------------------------------------------------------------------- scrapers
async def acknowledge(session: AsyncSession, scraper: Scraper, *, hours: int = 24, note: str = "") -> Scraper:
    """Stop a known problem shouting until `hours` from now."""
    scraper.acknowledged_until = datetime.now(UTC) + timedelta(hours=hours)
    scraper.ack_note = note or f"acknowledged by {ACTOR}"
    await repo.log(
        session,
        actor=ACTOR,
        action="acknowledged",
        object_type="scraper",
        object_ref=scraper.name,
        detail=f"quiet for {hours}h",
        meta={"until": scraper.acknowledged_until.isoformat(), "note": scraper.ack_note},
    )
    return scraper


async def unacknowledge(session: AsyncSession, scraper: Scraper) -> Scraper:
    scraper.acknowledged_until = None
    scraper.ack_note = None
    await repo.log(
        session, actor=ACTOR, action="reopened", object_type="scraper", object_ref=scraper.name,
        detail="acknowledgement cleared",
    )
    return scraper


async def set_enabled(session: AsyncSession, scraper: Scraper, enabled: bool) -> Scraper:
    scraper.enabled = enabled
    await repo.log(
        session, actor=ACTOR, action="enabled" if enabled else "disabled", object_type="scraper",
        object_ref=scraper.name, detail="from the scrapers list",
    )
    return scraper


# ------------------------------------------------------------------------ runs
async def enqueue_run(session: AsyncSession, scraper: Scraper, *, trigger: str = "manual") -> Run:
    """Queue a run.

    The row is real, so the run appears in the list and on the scraper at once.

    The row is written first so it appears in the list at once, then handed to
    the scheduler, which adopts it rather than creating a second one. Without the
    hand-off the row would sit in `queued` forever, which is what it used to do.
    """
    active = await repo.active_version(session, scraper.id)
    run = Run(
        scraper_id=scraper.id,
        script_version=active.version if active else 1,
        status=RunStatus.QUEUED,
        trigger=trigger,
    )
    session.add(run)
    await session.flush()
    await repo.log(
        session, actor=ACTOR, action="ran manually", object_type="scraper", object_ref=scraper.name,
        detail=f"queued run #{run.id}", meta={"run_id": run.id, "trigger": trigger},
    )
    await session.commit()
    _dispatch(run.id, scraper.id, trigger)
    return run


async def rerun(session: AsyncSession, runs: list[Run]) -> list[Run]:
    """Queue a fresh run for each selected run. Same TODO as `enqueue_run`."""
    out: list[Run] = []
    for old in runs:
        fresh = Run(
            scraper_id=old.scraper_id,
            script_version=old.script_version,
            status=RunStatus.QUEUED,
            trigger="rerun",
        )
        session.add(fresh)
        out.append(fresh)
    await session.flush()
    if out:
        await repo.log(
            session, actor=ACTOR, action="re-ran", object_type="run",
            object_ref=", ".join(f"#{r.id}" for r in runs),
            detail=f"{len(out)} runs queued",
            meta={"from": [r.id for r in runs], "to": [r.id for r in out]},
        )
    return out


async def cancel_run(session: AsyncSession, run: Run) -> Run:
    """Mark a run cancelled.

    TODO(runner): this only writes the row. The runner subprocess owning the
    browser is not signalled, so a truly running job keeps going until its own
    timeout. smartscraper/runner/ needs a cancel channel (pid file or a
    cancellation flag the step loop checks).
    """
    run.status = RunStatus.CANCELLED
    run.finished_at = datetime.now(UTC)
    await repo.log(
        session, actor=ACTOR, action="cancelled run", object_type="run", object_ref=f"#{run.id}",
        detail="from the live run view",
    )
    return run


# --------------------------------------------------------------------- repairs
async def approve_repair(session: AsyncSession, repair: Repair) -> Repair:
    """Promote the candidate version to active, in the table and on disk.

    smartscraper.pipeline.promote_version does the promotion so this path cannot
    drift from the MCP `approve_repair` tool or from the repair agent. It raises
    LookupError when the candidate version row is missing; the route turns that
    into a 409 rather than recording a decision that did not take effect.
    """
    scraper = await repo.get_scraper(session, repair.scraper_id)
    written = await promote_version(
        session, repair.scraper_id, repair.candidate_version, approved_by=ACTOR
    )

    repair.status = "approved"
    repair.decided_by = ACTOR
    repair.decided_at = datetime.now(UTC)

    await repo.log(
        session, actor=ACTOR, action="approved version", object_type="scraper",
        object_ref=f"{scraper.name if scraper else repair.scraper_id} v{repair.candidate_version}",
        detail="approved from the repair review"
        + (f"; wrote {written}" if written else "; scraper has no yaml_path, file not written"),
        meta={"repair_id": repair.id, "version": repair.candidate_version, "file": str(written or "")},
    )
    return repair


async def reject_repair(session: AsyncSession, repair: Repair, *, reason: str = "") -> Repair:
    repair.status = "rejected"
    repair.decided_by = ACTOR
    repair.decided_at = datetime.now(UTC)
    candidate = await session.scalar(
        select(ScriptVersion).where(
            ScriptVersion.scraper_id == repair.scraper_id,
            ScriptVersion.version == repair.candidate_version,
        )
    )
    if candidate is not None:
        candidate.status = VersionStatus.REJECTED
    scraper = await repo.get_scraper(session, repair.scraper_id)
    await repo.log(
        session, actor=ACTOR, action="rejected version", object_type="scraper",
        object_ref=f"{scraper.name if scraper else repair.scraper_id} v{repair.candidate_version}",
        detail=reason or "rejected from the repair review",
        meta={"repair_id": repair.id},
    )
    return repair


# ---------------------------------------------------------------- builder
async def start_builder(
    session: AsyncSession, *, url: str, goal: str, name: str | None = None, **opts: object
) -> dict[str, object]:
    """Start a build in the background and hand back something to watch.

    A build takes minutes: a browser opens, an agent explores the page, probes
    selectors, writes a script and test-runs it. Doing that inside the request
    left the form POST hanging with no output at all. So this returns as soon as
    the work is registered, with `job_id` pointing at a page that shows progress.

    Returns `job_id` on success, or `error` when the form itself is wrong.
    """
    from smartscraper.web import builds

    url = (url or "").strip()
    goal = (goal or "").strip()
    if not url:
        return {"ok": False, "error": "A target URL is required."}
    if not goal:
        return {"ok": False, "error": "Say what the scraper should extract."}

    await repo.log(
        session, actor=ACTOR, action="started builder", object_type="scraper",
        object_ref=name or url, detail=goal[:200],
    )
    await session.commit()

    job = builds.start(url, goal, name)
    if job.status == "failed":
        return {"ok": False, "error": job.error or "the build could not be started"}
    return {"ok": True, "job_id": job.id}


# ---------------------------------------------------------------- delivery
async def retry_delivery(session: AsyncSession, delivery_id: int) -> dict[str, object]:
    """Re-attempt one parked delivery now, instead of waiting for the sweep."""
    from smartscraper.db.models import Delivery, DeliveryTarget
    from smartscraper.delivery.base import deliver_one, load_rows

    delivery = await session.get(Delivery, delivery_id)
    if delivery is None:
        return {"ok": False, "error": f"no delivery {delivery_id}"}
    run = await session.get(Run, delivery.run_id)
    target = await session.get(DeliveryTarget, delivery.target_id)
    if run is None or target is None:
        return {"ok": False, "error": "the run or the target no longer exists"}

    rows = await load_rows(session, run)
    outcome = await deliver_one(session, run, target, rows, delivery=delivery)
    await repo.log(
        session, actor="you", action="retried delivery", object_type="delivery",
        object_ref=str(delivery_id), detail=f"{outcome.status}: {outcome.detail}",
    )
    return {"ok": outcome.status == "sent", "status": outcome.status, "detail": outcome.detail}


async def test_target(session: AsyncSession, target_id: int) -> dict[str, object]:
    """Send one synthetic row so a misconfigured target fails here, not at 03:00."""
    from datetime import UTC, datetime

    from smartscraper.db.models import DeliveryTarget
    from smartscraper.delivery.base import get_sink

    target = await session.get(DeliveryTarget, target_id)
    if target is None:
        return {"ok": False, "error": f"no target {target_id}"}

    row = {"_test": True, "sent_at": datetime.now(UTC).isoformat(), "note": "smartscraper target test"}
    try:
        sink = get_sink(target.kind)
        result = await sink.send(
            [row], config=dict(target.config or {}), run_id=0,
            scraper="target-test", meta={"test": True},
        )
    except Exception as exc:
        log.exception("target test failed for %s", target_id)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    await repo.log(
        session, actor="you", action="tested delivery target", object_type="delivery_target",
        object_ref=str(target_id),
        detail=f"{'ok' if result.ok else 'failed'}: {result.detail or result.error}",
    )
    return {"ok": result.ok, "detail": result.detail or result.error or ""}


async def relogin_profile(session: AsyncSession, profile_id: int) -> None:
    """TODO(profiles): no-op. Needs profiles.py to launch a headed browser."""
    log.warning("relogin_profile is a no-op for profile %s", profile_id)


# --------------------------------------------------------------- dispatch
# Background tasks are held in a module-level set. asyncio keeps only a weak
# reference to a running task, so a local variable would let it be collected
# mid-run and the scrape would vanish without a trace.
_RUNNING: set = set()


def _dispatch(run_id: int, scraper_id: int, trigger: str) -> None:
    """Hand a queued run to the scheduler without blocking the request.

    A scrape takes tens of seconds; an HTTP handler must not wait for it. If no
    event loop is running, as in a synchronous test, the run stays queued and
    says so rather than failing the request.
    """
    import asyncio

    from smartscraper.scheduler.tasks import run_scraper_now

    async def _go() -> None:
        try:
            await run_scraper_now(scraper_id, trigger, adopt_run_id=run_id)
        except Exception:
            log.exception("dispatched run %s failed", run_id)
            await _mark_failed(run_id)

    try:
        task = asyncio.get_running_loop().create_task(_go())
    except RuntimeError:
        log.warning("no event loop; run %s stays queued", run_id)
        return
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def _mark_failed(run_id: int) -> None:
    from smartscraper.db.session import get_session

    try:
        async with get_session() as s:
            run = await s.get(Run, run_id)
            if run is not None and run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                run.status = RunStatus.ERROR
                run.error = "the run could not be dispatched"
    except Exception:
        log.exception("could not mark run %s failed", run_id)
