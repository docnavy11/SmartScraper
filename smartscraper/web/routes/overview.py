"""Overview, the first-run empty state, and the two self-refreshing partials."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper.web import queries
from smartscraper.web.deps import DB, fragment, render

router = APIRouter(tags=["overview"])


async def _overview_ctx(session: AsyncSession) -> dict:
    return {
        "health": await queries.scraper_health(session),
        "today": await queries.today_counts(session),
        "strip": await queries.hourly_strip(session),
        "attention": await queries.needs_attention(session),
        "activity": await queries.activity(session),
        "scheduled": await queries.next_scheduled(session),
        "spend": await queries.agent_spend(session),
        "repairs": await queries.repair_counts(session),
    }


@router.get("/")
async def overview(request: Request, session: AsyncSession = DB):
    health = await queries.scraper_health(session)
    if health["total"] == 0:
        return await render(
            request,
            session,
            "first_run.html",
            {"page_title": "Overview", "page_sub": "nothing configured", "active": "Overview"},
        )
    ctx = await _overview_ctx(session)
    ctx |= {"page_title": "Overview", "active": "Overview", "health": health}
    return await render(request, session, "overview.html", ctx)


@router.get("/partials/activity")
async def activity_feed(request: Request, session: AsyncSession = DB):
    return fragment(request, "partials/activity.html", {"activity": await queries.activity(session)})


@router.get("/partials/attention")
async def attention_panel(request: Request, session: AsyncSession = DB):
    return fragment(
        request,
        "partials/attention.html",
        {"attention": await queries.needs_attention(session)},
    )
