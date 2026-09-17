"""Templates, filters, the session dependency and the shell context.

Every route renders through `render()`, which merges the top-bar counters into
the context so `base.html` never has to reach for a global.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper import repo
from smartscraper.config import get_settings
from smartscraper.db.models import Repair, Run, RunStatus, Scraper
from smartscraper.web import highlight
from smartscraper.web.icons import ICONS, RUN_STATUS_KIND, RUN_STATUS_WORD, STATUS

HERE = Path(__file__).resolve().parent
TEMPLATE_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"

NAV: list[tuple[str, str]] = [
    ("Overview", "/"),
    ("Scrapers", "/scrapers"),
    ("Runs", "/runs"),
    ("Records", "/records"),
    ("Repairs", "/repairs"),
    ("Network", "/network"),
    ("Delivery", "/delivery"),
    ("Settings", "/settings"),
]


# --------------------------------------------------------------------- filters
def fmt_dt(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return value.strftime(fmt) if value else "—"


def fmt_time(value: datetime | None) -> str:
    return value.strftime("%H:%M:%S") if value else "—"


def fmt_hhmm(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value else "—"


def fmt_ago(value: datetime | None) -> str:
    """Coarse relative time. Anything older than a week gets a date."""
    if not value:
        return "never"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    delta = (datetime.now(UTC) - value).total_seconds()
    if delta < 0:
        delta = abs(delta)
        if delta < 3600:
            return f"in {int(delta // 60) or 1}m"
        if delta < 86400:
            return f"in {int(delta // 3600)}h"
        return f"in {int(delta // 86400)}d"
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 604800:
        return f"{int(delta // 86400)}d ago"
    return value.strftime("%d %b %Y").lstrip("0")


def fmt_dur(ms: int | None) -> str:
    if not ms:
        return "—"
    s = ms / 1000
    if s < 10:
        return f"{s:.1f}s"
    if s < 60:
        return f"{int(s)}s"
    return f"{int(s // 60)}m{int(s % 60):02d}s"


def fmt_money(v: float | None) -> str:
    return f"${v or 0:,.2f}"


def fmt_num(v: Any) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "—"


def fmt_pct(v: float | None, digits: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.{digits}f}%"


# ------------------------------------------------------------------ templates
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals.update(
    ICONS=ICONS,
    STATUS=STATUS,
    RUN_STATUS_KIND=RUN_STATUS_KIND,
    RUN_STATUS_WORD=RUN_STATUS_WORD,
    NAV=NAV,
)
templates.env.filters.update(
    dt=fmt_dt,
    time=fmt_time,
    hhmm=fmt_hhmm,
    ago=fmt_ago,
    dur=fmt_dur,
    money=fmt_money,
    num=fmt_num,
    pct=fmt_pct,
    yaml_lines=highlight.yaml_lines,
    json_lines=highlight.json_lines,
    parse_diff=highlight.parse_diff,
    log_record=highlight.log_record,
)


# -------------------------------------------------------------------- session
async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.sessionmaker
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DB = Depends(get_db)


# -------------------------------------------------------------- shell context
async def shell(session: AsyncSession) -> dict[str, Any]:
    """The counters in the top bar. Cheap enough to run on every page."""
    pending = int(
        await session.scalar(select(func.count(Repair.id)).where(Repair.status == "pending_approval")) or 0
    )
    running = int(
        await session.scalar(select(func.count(Run.id)).where(Run.status == RunStatus.RUNNING)) or 0
    )
    scrapers = int(await session.scalar(select(func.count(Scraper.id))) or 0)
    spend = await repo.spend_month_to_date(session)
    return {
        "nav": NAV,
        "repairs_pending": pending,
        "running_count": running,
        "scraper_count": scrapers,
        "spend_mtd": spend,
        "settings": get_settings(),
    }


async def render(
    request: Request,
    session: AsyncSession,
    template: str,
    ctx: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
):
    data: dict[str, Any] = {"request": request, "now": datetime.now(UTC)}
    data.update(await shell(session))
    data.update(ctx or {})
    return templates.TemplateResponse(request, template, data, status_code=status_code)


def fragment(request: Request, template: str, ctx: dict[str, Any] | None = None):
    """A partial. No shell context: fragments never render the top bar."""
    data: dict[str, Any] = {"request": request, "now": datetime.now(UTC)}
    data.update(ctx or {})
    return templates.TemplateResponse(request, template, data)
