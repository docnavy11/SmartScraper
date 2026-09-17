"""Runs: the filterable list, run detail, the live view and the SSE log tail."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from smartscraper import repo
from smartscraper.db.models import Run, RunStatus
from smartscraper.web import actions, queries
from smartscraper.web.deps import DB, fragment, render
from smartscraper.web.highlight import log_line_html, log_record

router = APIRouter(tags=["runs"])

SEE_OTHER = 303
PAGE = 50
POLL_S = 1.0


# ------------------------------------------------------------------- listing
async def _list_ctx(session: AsyncSession, scraper_id: int | None, status: str | None, page: int) -> dict:
    offset = max(page - 1, 0) * PAGE
    rows = await queries.runs_with_scrapers(
        session, scraper_id=scraper_id, status=status, limit=PAGE, offset=offset
    )
    total = await queries.count_runs(session, scraper_id=scraper_id, status=status)
    return {
        "rows": rows,
        "total": total,
        "page": page,
        "pages": max((total + PAGE - 1) // PAGE, 1),
        "scraper_id": scraper_id,
        "status": status,
    }


@router.get("/runs")
async def index(
    request: Request,
    session: AsyncSession = DB,
    scraper_id: int | None = None,
    status: str | None = None,
    page: int = 1,
):
    ctx = await _list_ctx(session, scraper_id, status, page)
    ctx |= {
        "page_title": "Runs",
        "active": "Runs",
        "today": await queries.today_counts(session),
        "scrapers": await repo.list_scrapers(session),
        "statuses": [s.value for s in RunStatus],
    }
    return await render(request, session, "runs.html", ctx)


@router.get("/partials/runs")
async def runs_partial(
    request: Request,
    session: AsyncSession = DB,
    scraper_id: int | None = None,
    status: str | None = None,
    page: int = 1,
):
    return fragment(request, "partials/run_rows.html", await _list_ctx(session, scraper_id, status, page))


# -------------------------------------------------------------------- detail
@router.get("/runs/{run_id}")
async def detail(run_id: int, request: Request, session: AsyncSession = DB):
    pair = await queries.run_with_scraper(session, run_id)
    if pair is None:
        raise HTTPException(status_code=404, detail="no such run")
    run, scraper = pair
    if run.status in (RunStatus.RUNNING, RunStatus.QUEUED):
        return RedirectResponse(f"/runs/{run_id}/live", status_code=SEE_OTHER)
    return await render(
        request,
        session,
        "run_detail.html",
        {
            "page_title": f"Run #{run.id}",
            "page_sub": f"{scraper.name} · v{run.script_version} · {run.created_at:%d %b %H:%M:%S}",
            "active": "Runs",
            "run": run,
            "scraper": scraper,
            "metrics": await repo.run_metrics(session, run_id),
            "deliveries": await repo.deliveries_for_run(session, run_id),
            "records": await repo.get_records(session, scraper_id=scraper.id, run_id=run_id, limit=10),
            "log": _read_log(run, limit=400),
            "artifacts": _artifacts(run),
        },
    )


@router.get("/runs/{run_id}/live")
async def live(run_id: int, request: Request, session: AsyncSession = DB):
    pair = await queries.run_with_scraper(session, run_id)
    if pair is None:
        raise HTTPException(status_code=404, detail="no such run")
    run, scraper = pair
    return await render(
        request,
        session,
        "run_live.html",
        {
            "page_title": f"Run #{run.id}",
            "page_sub": f"{scraper.name} · v{run.script_version} · {run.status}",
            "active": "Runs",
            "run": run,
            "scraper": scraper,
            "log": _read_log(run, limit=200),
            "finished": run.status not in (RunStatus.RUNNING, RunStatus.QUEUED),
        },
    )


def _read_log(run: Run, limit: int = 400) -> list[dict[str, str]]:
    """The tail of the run's log file, already parsed. Missing file is normal."""
    if not run.log_path:
        return []
    path = Path(run.log_path)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [log_record(line) for line in lines[-limit:] if line.strip()]


def _artifacts(run: Run) -> list[dict[str, object]]:
    if not run.artifact_dir:
        return []
    directory = Path(run.artifact_dir)
    if not directory.is_dir():
        return []
    out = []
    for p in sorted(directory.iterdir()):
        if p.is_file():
            out.append({"name": p.name, "size": p.stat().st_size, "suffix": p.suffix.lstrip(".")})
    return out


# ----------------------------------------------------------------------- SSE
@router.get("/runs/{run_id}/log/stream")
async def log_stream(run_id: int, request: Request, session: AsyncSession = DB):
    """Tail the run's log file over server-sent events.

    Each `line` event carries one rendered log row, which the htmx SSE
    extension appends to the log pane. A `done` event closes the stream when
    the run leaves the running state.
    """
    run = await repo.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no such run")
    path = Path(run.log_path) if run.log_path else None
    factory = request.app.state.sessionmaker

    async def events():
        pos = 0
        buffer = ""
        while True:
            if await request.is_disconnected():
                return
            if path is not None and path.exists():
                try:
                    with path.open("r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                        pos = fh.tell()
                except OSError:
                    chunk = ""
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        yield {"event": "line", "data": log_line_html(line)}

            async with factory() as s2:
                current = await repo.get_run(s2, run_id)
            still_live = current is not None and current.status in (RunStatus.RUNNING, RunStatus.QUEUED)
            if not still_live:
                yield {
                    "event": "done",
                    "data": (
                        '<div class="logline l-info"><span class="ts"></span>'
                        '<span class="tag">runner</span><span class="msg">'
                        f'run finished · {current.status if current else "gone"}</span></div>'
                    ),
                }
                return
            await asyncio.sleep(POLL_S)

    return EventSourceResponse(events())


# --------------------------------------------------------------- interactions
@router.post("/runs/bulk/rerun")
async def bulk_rerun(request: Request, session: AsyncSession = DB, back: str = Form("/runs")):
    form = await request.form()
    ids = [int(v) for v in form.getlist("run_id") if str(v).isdigit()]
    runs = []
    for i in ids:
        found = await repo.get_run(session, i)
        if found is not None:
            runs.append(found)
    await actions.rerun(session, runs)
    return RedirectResponse(back, status_code=SEE_OTHER)


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: int, request: Request, session: AsyncSession = DB):
    run = await repo.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no such run")
    await actions.cancel_run(session, run)
    return RedirectResponse(f"/runs/{run_id}", status_code=SEE_OTHER)


@router.post("/runs/{run_id}/rerun")
async def rerun_one(run_id: int, request: Request, session: AsyncSession = DB):
    run = await repo.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no such run")
    fresh = await actions.rerun(session, [run])
    return RedirectResponse(f"/runs/{fresh[0].id}" if fresh else "/runs", status_code=SEE_OTHER)
