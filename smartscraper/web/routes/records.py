"""Records: extracted rows across runs, with the script / LLM-fallback split."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper import repo
from smartscraper.web import queries
from smartscraper.web.deps import DB, render

router = APIRouter(tags=["records"])
PAGE = 50


@router.get("/records")
async def index(
    request: Request,
    session: AsyncSession = DB,
    scraper_id: int | None = None,
    run_id: int | None = None,
    include_fallback: bool = True,
    page: int = 1,
):
    offset = max(page - 1, 0) * PAGE
    rows = await queries.records_page(
        session,
        scraper_id=scraper_id,
        run_id=run_id,
        include_fallback=include_fallback,
        limit=PAGE,
        offset=offset,
    )
    total = await repo.count_records(session, scraper_id)
    freshness = await repo.freshness(session, scraper_id) if scraper_id else None
    scraper = await repo.get_scraper(session, scraper_id) if scraper_id else None
    return await render(
        request,
        session,
        "records.html",
        {
            "page_title": "Records",
            "page_sub": f"{total:,} stored",
            "active": "Records",
            "rows": rows,
            "columns": queries.record_columns(rows),
            "total": total,
            "page": page,
            "pages": max((total + PAGE - 1) // PAGE, 1),
            "scraper_id": scraper_id,
            "scraper": scraper,
            "run_id": run_id,
            "include_fallback": include_fallback,
            "freshness": freshness,
            "scrapers": await repo.list_scrapers(session),
            "fallback_count": sum(1 for r, _ in rows if r.source != "script"),
        },
    )
