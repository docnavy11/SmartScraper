"""Repairs: the approval queue and the review screen."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper import repo
from smartscraper.db.models import Repair, ScriptVersion
from smartscraper.web import actions, queries
from smartscraper.web.deps import DB, render

router = APIRouter(tags=["repairs"])
SEE_OTHER = 303


@router.get("/repairs")
async def index(request: Request, session: AsyncSession = DB):
    pending = await queries.repairs_with_scrapers(session, status="pending_approval")
    recent = await queries.repairs_with_scrapers(session, limit=40)
    history = [(r, n) for r, n in recent if r.status != "pending_approval"]
    return await render(
        request,
        session,
        "repairs.html",
        {
            "page_title": "Repairs",
            "active": "Repairs",
            "pending": pending,
            "history": history,
            "counts": await queries.repair_counts(session),
        },
    )


@router.get("/repairs/{repair_id}")
async def review(repair_id: int, request: Request, session: AsyncSession = DB):
    repair = await session.get(Repair, repair_id)
    if repair is None:
        raise HTTPException(status_code=404, detail="no such repair")
    scraper = await repo.get_scraper(session, repair.scraper_id)
    candidate = await session.scalar(
        select(ScriptVersion).where(
            ScriptVersion.scraper_id == repair.scraper_id,
            ScriptVersion.version == repair.candidate_version,
        )
    )
    active = await repo.active_version(session, repair.scraper_id)
    test_run = await repo.get_run(session, repair.test_run_id) if repair.test_run_id else None
    failed_run = await repo.get_run(session, repair.run_id) if repair.run_id else None
    sample = (
        await repo.get_records(session, scraper_id=repair.scraper_id, run_id=test_run.id, limit=5)
        if test_run
        else []
    )
    return await render(
        request,
        session,
        "repair_review.html",
        {
            "page_title": "{}  v{} → v{}".format(
                scraper.name if scraper else "repair",
                active.version if active else "?",
                repair.candidate_version,
            ),
            "page_sub": "proposed " + repair.created_at.strftime("%d %b %H:%M"),
            "active": "Repairs",
            "repair": repair,
            "scraper": scraper,
            "candidate": candidate,
            "active_version": active,
            "test_run": test_run,
            "failed_run": failed_run,
            "sample": sample,
            "test_metrics": await repo.run_metrics(session, test_run.id) if test_run else [],
            "targets": await repo.targets_for(session, repair.scraper_id),
        },
    )


@router.post("/repairs/{repair_id}/approve")
async def approve(repair_id: int, request: Request, session: AsyncSession = DB, back: str = Form("/repairs")):
    repair = await session.get(Repair, repair_id)
    if repair is None:
        raise HTTPException(status_code=404, detail="no such repair")
    try:
        await actions.approve_repair(session, repair)
    except LookupError as exc:
        # The candidate version row is gone. Nothing is written and the session
        # rolls back, so the repair stays pending rather than reading approved.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(back, status_code=SEE_OTHER)


@router.post("/repairs/{repair_id}/reject")
async def reject(
    repair_id: int,
    request: Request,
    session: AsyncSession = DB,
    reason: str = Form(""),
    back: str = Form("/repairs"),
):
    repair = await session.get(Repair, repair_id)
    if repair is None:
        raise HTTPException(status_code=404, detail="no such repair")
    await actions.reject_repair(session, repair, reason=reason)
    return RedirectResponse(back, status_code=SEE_OTHER)
