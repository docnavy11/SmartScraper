"""Shared read/write helpers over the DB models.

The web UI and the MCP server both read through here so query logic exists once.
Subsystems that own their own tables (runner, validate, delivery) write directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper.contracts import Freshness
from smartscraper.db.models import (
    AuditEntry,
    Delivery,
    DeliveryTarget,
    LlmUsage,
    Record,
    Repair,
    Run,
    RunMetric,
    RunStatus,
    Scraper,
    ScriptVersion,
    VersionStatus,
)


# --------------------------------------------------------------------------- scrapers
async def list_scrapers(s: AsyncSession) -> list[Scraper]:
    return list((await s.scalars(select(Scraper).order_by(Scraper.name))).all())


async def get_scraper(s: AsyncSession, ident: int | str) -> Scraper | None:
    q = select(Scraper).where(Scraper.id == ident if isinstance(ident, int) else Scraper.name == ident)
    return await s.scalar(q)


async def active_version(s: AsyncSession, scraper_id: int) -> ScriptVersion | None:
    return await s.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.scraper_id == scraper_id, ScriptVersion.status == VersionStatus.ACTIVE)
        .order_by(desc(ScriptVersion.version))
    )


async def versions(s: AsyncSession, scraper_id: int) -> list[ScriptVersion]:
    return list((await s.scalars(
        select(ScriptVersion).where(ScriptVersion.scraper_id == scraper_id)
        .order_by(desc(ScriptVersion.version))
    )).all())


# --------------------------------------------------------------------------- runs
async def list_runs(
    s: AsyncSession, *, scraper_id: int | None = None, status: str | None = None, limit: int = 50,
    offset: int = 0,
) -> list[Run]:
    q = select(Run).order_by(desc(Run.created_at)).limit(limit).offset(offset)
    if scraper_id is not None:
        q = q.where(Run.scraper_id == scraper_id)
    if status:
        q = q.where(Run.status == status)
    return list((await s.scalars(q)).all())


async def get_run(s: AsyncSession, run_id: int) -> Run | None:
    return await s.get(Run, run_id)


async def run_metrics(s: AsyncSession, run_id: int) -> list[RunMetric]:
    return list((await s.scalars(select(RunMetric).where(RunMetric.run_id == run_id))).all())


async def recent_row_counts(
    s: AsyncSession, scraper_id: int, n: int = 5, *, exclude_run_id: int | None = None
) -> list[int]:
    """Row counts of recent PASSED runs, newest first: the baseline for drift.

    Only passed runs count, so a run being judged is normally excluded already,
    since it is still `running` at that moment. `exclude_run_id` covers the case
    of re-judging a run that has already passed, where including itself would
    drag the baseline toward its own value and hide the drift.
    """
    q = select(Run.row_count).where(Run.scraper_id == scraper_id, Run.status == RunStatus.PASSED)
    if exclude_run_id is not None:
        q = q.where(Run.id != exclude_run_id)
    return list((await s.scalars(q.order_by(desc(Run.created_at)).limit(n))).all())


async def last_clean_run(s: AsyncSession, scraper_id: int) -> Run | None:
    return await s.scalar(
        select(Run).where(Run.scraper_id == scraper_id, Run.status == RunStatus.PASSED)
        .order_by(desc(Run.created_at))
    )


async def freshness(s: AsyncSession, scraper_id: int) -> Freshness:
    clean = await last_clean_run(s, scraper_id)
    last = await s.scalar(select(Run).where(Run.scraper_id == scraper_id).order_by(desc(Run.created_at)))
    stale = bool(last and clean and last.id != clean.id) or clean is None
    reason = ""
    if stale and clean:
        reason = f"last clean run {clean.created_at:%Y-%m-%d %H:%M}, newer runs did not pass"
    elif clean is None:
        reason = "no run has passed validation yet"
    return Freshness(
        last_clean_run_at=clean.created_at if clean else None,
        last_run_at=last.created_at if last else None,
        stale=stale, reason=reason,
    )


# --------------------------------------------------------------------------- records
async def get_records(
    s: AsyncSession, *, scraper_id: int, run_id: int | None = None, include_fallback: bool = True,
    limit: int = 100, offset: int = 0,
) -> list[Record]:
    q = select(Record).where(Record.scraper_id == scraper_id)
    if run_id is not None:
        q = q.where(Record.run_id == run_id)
    if not include_fallback:
        q = q.where(Record.source == "script")
    return list((await s.scalars(q.order_by(desc(Record.id)).limit(limit).offset(offset))).all())


async def search_records(
    s: AsyncSession, *, query: str, scraper_id: int | None = None, include_fallback: bool = True,
    limit: int = 100, offset: int = 0,
) -> list[Record]:
    """Substring match over the stored JSON blob.

    SQLite has no JSON full-text index, so this casts the blob to text and does a
    LIKE. It is honest about what it is: fine for one user's dataset, and the
    thing to replace with FTS5 if a single scraper ever holds millions of rows.
    """
    needle = f"%{query.lower()}%"
    q = select(Record).where(
        or_(
            cast(Record.data, String).ilike(needle),
            cast(Record.data, String).like(f"%{query}%"),
        )
    )
    if scraper_id is not None:
        q = q.where(Record.scraper_id == scraper_id)
    if not include_fallback:
        q = q.where(Record.source == "script")
    return list((await s.scalars(q.order_by(desc(Record.id)).limit(limit).offset(offset))).all())


async def count_records(s: AsyncSession, scraper_id: int | None = None) -> int:
    q = select(func.count(Record.id))
    if scraper_id is not None:
        q = q.where(Record.scraper_id == scraper_id)
    return int(await s.scalar(q) or 0)


# --------------------------------------------------------------------------- repairs
async def pending_repairs(s: AsyncSession) -> list[Repair]:
    return list((await s.scalars(
        select(Repair).where(Repair.status == "pending_approval").order_by(desc(Repair.created_at))
    )).all())


# --------------------------------------------------------------------------- delivery
async def targets_for(s: AsyncSession, scraper_id: int) -> list[DeliveryTarget]:
    return list((await s.scalars(
        select(DeliveryTarget).where(
            (DeliveryTarget.scraper_id == scraper_id) | (DeliveryTarget.scraper_id.is_(None)),
            DeliveryTarget.enabled.is_(True),
        )
    )).all())


async def deliveries_for_run(s: AsyncSession, run_id: int) -> list[Delivery]:
    return list((await s.scalars(select(Delivery).where(Delivery.run_id == run_id))).all())


# --------------------------------------------------------------------------- spend
async def spend_since(s: AsyncSession, since: datetime, scraper_id: int | None = None) -> float:
    q = select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(LlmUsage.created_at >= since)
    if scraper_id is not None:
        q = q.where(LlmUsage.scraper_id == scraper_id)
    return float(await s.scalar(q) or 0.0)


async def spend_month_to_date(s: AsyncSession, scraper_id: int | None = None) -> float:
    now = datetime.now(UTC)
    return await spend_since(s, now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), scraper_id)


async def spend_by_agent(s: AsyncSession, days: int = 7) -> dict[str, float]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await s.execute(
        select(LlmUsage.agent, func.sum(LlmUsage.cost_usd)).where(LlmUsage.created_at >= since)
        .group_by(LlmUsage.agent)
    )
    return {a: float(c or 0.0) for a, c in rows.all()}


# --------------------------------------------------------------------------- audit
async def audit(s: AsyncSession, *, limit: int = 100, actor: str | None = None) -> list[AuditEntry]:
    q = select(AuditEntry).order_by(desc(AuditEntry.created_at)).limit(limit)
    if actor:
        q = q.where(AuditEntry.actor == actor)
    return list((await s.scalars(q)).all())


async def log(
    s: AsyncSession, *, actor: str, action: str, object_type: str = "", object_ref: str = "",
    detail: str = "", meta: dict[str, Any] | None = None,
) -> AuditEntry:
    e = AuditEntry(actor=actor, action=action, object_type=object_type, object_ref=object_ref,
                   detail=detail, meta=meta or {})
    s.add(e)
    return e
