"""Scrapers: the dense list, one scraper, its script, its schema, and the
interaction endpoints that write to Scraper and the audit log."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper import repo
from smartscraper.web import actions, queries
from smartscraper.web.deps import DB, render

router = APIRouter(tags=["scrapers"])

SEE_OTHER = 303


def _tabs(scraper_id: int, active: str) -> list[tuple[str, str, bool]]:
    base = f"/scrapers/{scraper_id}"
    return [
        ("Overview", base, active == "overview"),
        ("Script", f"{base}/script", active == "script"),
        ("Schema", f"{base}/schema", active == "schema"),
        ("Runs", f"/runs?scraper_id={scraper_id}", active == "runs"),
        ("Records", f"/records?scraper_id={scraper_id}", active == "records"),
        ("Delivery", "/delivery", active == "delivery"),
    ]


async def _get(session: AsyncSession, scraper_id: int):
    scraper = await repo.get_scraper(session, scraper_id)
    if scraper is None:
        raise HTTPException(status_code=404, detail="no such scraper")
    return scraper


# ------------------------------------------------------------------- listing
@router.get("/scrapers")
async def index(request: Request, session: AsyncSession = DB, q: str = ""):
    rows = await queries.scraper_rows(session)
    needle = q.strip().lower()
    if needle:
        rows = [
            r
            for r in rows
            if needle in r["scraper"].name.lower()
            or needle in (r["scraper"].url or "").lower()
            or any(needle in str(t).lower() for t in (r["scraper"].tags or []))
        ]
    return await render(
        request,
        session,
        "scrapers.html",
        {
            "page_title": "Scrapers",
            "active": "Scrapers",
            "rows": rows,
            "q": q,
            "custom_python": await queries.custom_python_versions(session),
        },
    )


@router.get("/scrapers/new")
async def new(request: Request, session: AsyncSession = DB, mode: str = "describe", error: str = ""):
    return await render(
        request,
        session,
        "scraper_new.html",
        {
            "page_title": "New scraper",
            "page_sub": "step 1 of 2",
            "active": "Scrapers",
            "mode": mode,
            "error": error,
        },
    )


@router.post("/scrapers")
async def create(
    request: Request,
    session: AsyncSession = DB,
    url: str = Form(""),
    goal: str = Form(""),
    name: str = Form(""),
    engine: str = Form("Auto"),
    schedule: str = Form("Manual"),
):
    from urllib.parse import quote_plus

    result = await actions.start_builder(
        session, url=url, goal=goal, name=name or None, engine=engine, schedule=schedule
    )
    # A build takes minutes. The request starts it and hands back somewhere to
    # watch, rather than holding the connection open with nothing to show.
    if result.get("job_id"):
        return RedirectResponse(f"/builder/{result['job_id']}", status_code=SEE_OTHER)
    return RedirectResponse(
        f"/scrapers/new?error={quote_plus(str(result.get('error', 'the build could not be started')))}"
        f"&url={quote_plus(url)}&goal={quote_plus(goal)}",
        status_code=SEE_OTHER,
    )


# -------------------------------------------------------------------- detail
@router.get("/scrapers/{scraper_id}")
async def detail(scraper_id: int, request: Request, session: AsyncSession = DB):
    scraper = await _get(session, scraper_id)
    runs = await repo.list_runs(session, scraper_id=scraper_id, limit=10)
    return await render(
        request,
        session,
        "scraper_detail.html",
        {
            "page_title": scraper.name,
            "page_sub": scraper.url,
            "page_tabs": _tabs(scraper_id, "overview"),
            "active": "Scrapers",
            "scraper": scraper,
            "runs": runs,
            "stats": await queries.scraper_stats(session, scraper_id),
            "series": await queries.row_count_series(session, scraper_id),
            "version": await repo.active_version(session, scraper_id),
            "targets": await repo.targets_for(session, scraper_id),
            "freshness": await repo.freshness(session, scraper_id),
            "pending": [
                r for r, _ in await queries.repairs_with_scrapers(session, status="pending_approval")
                if r.scraper_id == scraper_id
            ],
        },
    )


@router.get("/scrapers/{scraper_id}/script")
async def script(scraper_id: int, request: Request, session: AsyncSession = DB, version: int | None = None):
    scraper = await _get(session, scraper_id)
    versions = await queries.version_rows(session, scraper_id)
    shown = None
    if version is not None:
        shown = next((v for v in versions if v.version == version), None)
    if shown is None:
        shown = next((v for v in versions if v.status == "active"), None)
        if shown is None and versions:
            shown = versions[0]
    return await render(
        request,
        session,
        "scraper_script.html",
        {
            "page_title": scraper.name,
            "page_sub": f"script v{shown.version}" if shown else "no script yet",
            "page_tabs": _tabs(scraper_id, "script"),
            "active": "Scrapers",
            "scraper": scraper,
            "versions": versions,
            "shown": shown,
        },
    )


@router.get("/scrapers/{scraper_id}/schema")
async def schema(scraper_id: int, request: Request, session: AsyncSession = DB):
    scraper = await _get(session, scraper_id)
    version = await repo.active_version(session, scraper_id)
    fields = await queries.field_metrics(session, scraper_id)
    for f in fields:
        f["series"] = await queries.field_series(session, scraper_id, f["field"])
    return await render(
        request,
        session,
        "scraper_schema.html",
        {
            "page_title": scraper.name,
            "page_sub": "output schema",
            "page_tabs": _tabs(scraper_id, "schema"),
            "active": "Records",
            "scraper": scraper,
            "version": version,
            "fields": fields,
            "versions": await queries.version_rows(session, scraper_id),
            "freshness": await repo.freshness(session, scraper_id),
            "targets": await repo.targets_for(session, scraper_id),
        },
    )


# --------------------------------------------------------------- interactions
@router.post("/scrapers/{scraper_id}/run")
async def run_now(scraper_id: int, request: Request, session: AsyncSession = DB):
    scraper = await _get(session, scraper_id)
    run = await actions.enqueue_run(session, scraper)
    return RedirectResponse(f"/runs/{run.id}", status_code=SEE_OTHER)


@router.post("/scrapers/{scraper_id}/acknowledge")
async def acknowledge(
    scraper_id: int,
    request: Request,
    session: AsyncSession = DB,
    hours: int = Form(24),
    note: str = Form(""),
    back: str = Form("/"),
):
    scraper = await _get(session, scraper_id)
    await actions.acknowledge(session, scraper, hours=hours, note=note)
    return RedirectResponse(back, status_code=SEE_OTHER)


@router.post("/scrapers/{scraper_id}/reopen")
async def reopen(scraper_id: int, request: Request, session: AsyncSession = DB, back: str = Form("/")):
    scraper = await _get(session, scraper_id)
    await actions.unacknowledge(session, scraper)
    return RedirectResponse(back, status_code=SEE_OTHER)


@router.post("/scrapers/{scraper_id}/enable")
async def enable(
    scraper_id: int, request: Request, session: AsyncSession = DB, back: str = Form("/scrapers")
):
    scraper = await _get(session, scraper_id)
    await actions.set_enabled(session, scraper, True)
    return RedirectResponse(back, status_code=SEE_OTHER)


@router.post("/scrapers/{scraper_id}/disable")
async def disable(
    scraper_id: int, request: Request, session: AsyncSession = DB, back: str = Form("/scrapers")
):
    scraper = await _get(session, scraper_id)
    await actions.set_enabled(session, scraper, False)
    return RedirectResponse(back, status_code=SEE_OTHER)
