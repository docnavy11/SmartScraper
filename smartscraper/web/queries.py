"""Read queries the UI needs that smartscraper/repo.py does not expose yet.

Every function here is read-only and belongs in repo.py once that file grows
the aggregates. They are kept apart so the move is a cut and paste and so it is
obvious what the web layer had to reach past repo for. The list is repeated in
the web report.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper.db.models import (
    AuditEntry,
    Delivery,
    DeliveryTarget,
    LlmUsage,
    Profile,
    ProxyPool,
    Record,
    Repair,
    Run,
    RunMetric,
    RunStatus,
    Scraper,
    ScriptVersion,
)

FAILED = (RunStatus.VALIDATION_FAILED, RunStatus.ERROR, RunStatus.BLOCKED)


def _start_of_day() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# ------------------------------------------------------------------ overview
async def today_counts(s: AsyncSession) -> dict[str, int]:
    since = _start_of_day()
    total = int(await s.scalar(select(func.count(Run.id)).where(Run.created_at >= since)) or 0)
    passed = int(
        await s.scalar(
            select(func.count(Run.id)).where(Run.created_at >= since, Run.status == RunStatus.PASSED)
        )
        or 0
    )
    failed = int(
        await s.scalar(select(func.count(Run.id)).where(Run.created_at >= since, Run.status.in_(FAILED)))
        or 0
    )
    blocked = int(
        await s.scalar(
            select(func.count(Run.id)).where(Run.created_at >= since, Run.status == RunStatus.BLOCKED)
        )
        or 0
    )
    rows = int(
        await s.scalar(
            select(func.coalesce(func.sum(Run.row_count), 0)).where(
                Run.created_at >= since, Run.status == RunStatus.PASSED
            )
        )
        or 0
    )
    median = await s.scalar(
        select(func.avg(Run.duration_ms)).where(Run.created_at >= since, Run.duration_ms.is_not(None))
    )
    return {
        "runs": total,
        "passed": passed,
        "failed": failed,
        "blocked": blocked,
        "rows": rows,
        "mean_ms": int(median or 0),
    }


async def scraper_health(s: AsyncSession) -> dict[str, int]:
    """Healthy / drift / failed / paused, judged by each scraper's latest run."""
    out = {"total": 0, "healthy": 0, "drift": 0, "failed": 0, "paused": 0}
    for scraper in (await s.scalars(select(Scraper))).all():
        out["total"] += 1
        if not scraper.enabled:
            out["paused"] += 1
            continue
        last = await s.scalar(
            select(Run).where(Run.scraper_id == scraper.id).order_by(desc(Run.created_at))
        )
        if last is None or last.status in (RunStatus.PASSED, RunStatus.RUNNING, RunStatus.QUEUED):
            out["healthy"] += 1
        elif last.status == RunStatus.VALIDATION_FAILED:
            out["drift"] += 1
        else:
            out["failed"] += 1
    return out


async def hourly_strip(s: AsyncSession, hours: int = 24) -> list[dict[str, int]]:
    """Runs per hour for the last 24h, split passed / drift / failed."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    buckets = [{"hour": i, "ok": 0, "warn": 0, "fail": 0, "total": 0} for i in range(hours)]
    rows = (await s.scalars(select(Run).where(Run.created_at >= since))).all()
    for r in rows:
        created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)
        idx = int((created - since).total_seconds() // 3600)
        if not 0 <= idx < hours:
            continue
        b = buckets[idx]
        b["total"] += 1
        if r.status == RunStatus.PASSED:
            b["ok"] += 1
        elif r.status == RunStatus.VALIDATION_FAILED:
            b["warn"] += 1
        elif r.status in FAILED:
            b["fail"] += 1
    return buckets


async def activity(s: AsyncSession, limit: int = 24) -> list[dict[str, Any]]:
    """The live feed: recent runs and recent audit entries, newest first."""
    feed: list[dict[str, Any]] = []
    runs = (
        await s.execute(
            select(Run, Scraper.name)
            .join(Scraper, Scraper.id == Run.scraper_id)
            .order_by(desc(Run.created_at))
            .limit(limit)
        )
    ).all()
    for run, name in runs:
        kind = {
            RunStatus.PASSED: "ok",
            RunStatus.RUNNING: "run",
            RunStatus.QUEUED: "queued",
            RunStatus.VALIDATION_FAILED: "drift",
        }.get(run.status, "fail")
        detail = f"{run.row_count:,} rows" if run.status == RunStatus.PASSED else run.status
        engine = f" · {run.engine_used}" if run.engine_used else ""
        feed.append(
            {
                "at": run.created_at,
                "kind": kind,
                "text": f"{name}  run #{run.id} {detail}{engine}",
                "href": f"/runs/{run.id}",
            }
        )
    for entry in (
        await s.scalars(select(AuditEntry).order_by(desc(AuditEntry.created_at)).limit(limit))
    ).all():
        feed.append(
            {
                "at": entry.created_at,
                "kind": "agent" if "agent" in entry.actor else "ok",
                "text": f"{entry.actor} {entry.action} {entry.object_ref}".strip(),
                "href": "/audit",
            }
        )
    feed.sort(key=lambda f: f["at"].replace(tzinfo=UTC) if f["at"].tzinfo is None else f["at"], reverse=True)
    return feed[:limit]


async def needs_attention(s: AsyncSession, *, include_acked: bool = True) -> list[dict[str, Any]]:
    """Scrapers whose newest run did not pass, with their acknowledgement state."""
    now = datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for scraper in (await s.scalars(select(Scraper).order_by(Scraper.name))).all():
        last = await s.scalar(
            select(Run).where(Run.scraper_id == scraper.id).order_by(desc(Run.created_at))
        )
        if last is None or last.status in (
            RunStatus.PASSED,
            RunStatus.RUNNING,
            RunStatus.QUEUED,
        ):
            continue
        acked_until = scraper.acknowledged_until
        if acked_until is not None and acked_until.tzinfo is None:
            acked_until = acked_until.replace(tzinfo=UTC)
        acked = bool(acked_until and acked_until > now)
        if acked and not include_acked:
            continue
        if last.status == RunStatus.VALIDATION_FAILED:
            kind, why = "drift", _validator_reason(last)
        elif last.status == RunStatus.BLOCKED:
            kind, why = "fail", f"blocked · {last.block_reason or 'unknown'}"
        else:
            kind, why = "fail", (last.error or "run errored")[:160]
        out.append(
            {
                "scraper": scraper,
                "run": last,
                "kind": kind,
                "why": why,
                "acked": acked,
                "acked_until": acked_until,
                "ack_note": scraper.ack_note or "",
            }
        )
    out.sort(key=lambda r: (r["acked"], r["scraper"].name))
    return out


def _validator_reason(run: Run) -> str:
    report = run.validator_report or {}
    rules = report.get("rules") or []
    bad = [r for r in rules if not r.get("passed", True)]
    if bad:
        first = bad[0]
        return (
            f"{first.get('rule', 'rule')} measured {first.get('measured', '?')}, "
            f"expected {first.get('expected', '?')}"
        )
    return "validation failed"


async def next_scheduled(s: AsyncSession, limit: int = 8) -> list[Scraper]:
    """Enabled scrapers that carry a schedule.

    TODO(scheduler): there is no cron parser in the project, so this cannot sort
    by actual next fire time. It lists by name and shows the cron expression.
    """
    q = (
        select(Scraper)
        .where(Scraper.enabled.is_(True), Scraper.schedule.is_not(None), Scraper.schedule != "")
        .order_by(Scraper.name)
        .limit(limit)
    )
    return list((await s.scalars(q)).all())


# ------------------------------------------------------------------ scrapers
async def scraper_rows(s: AsyncSession) -> list[dict[str, Any]]:
    """One display row per scraper for the dense table."""
    since7 = datetime.now(UTC) - timedelta(days=7)
    out = []
    for scraper in (await s.scalars(select(Scraper).order_by(Scraper.name))).all():
        last = await s.scalar(
            select(Run).where(Run.scraper_id == scraper.id).order_by(desc(Run.created_at))
        )
        prev_counts = [
            c
            for c in (
                await s.scalars(
                    select(Run.row_count)
                    .where(Run.scraper_id == scraper.id, Run.status == RunStatus.PASSED)
                    .order_by(desc(Run.created_at))
                    .limit(6)
                )
            ).all()
        ]
        baseline = prev_counts[1:]
        delta = None
        if last and baseline:
            mean = sum(baseline) / len(baseline)
            if mean:
                delta = (last.row_count - mean) / mean
        active = await s.scalar(
            select(ScriptVersion)
            .where(ScriptVersion.scraper_id == scraper.id, ScriptVersion.status == "active")
            .order_by(desc(ScriptVersion.version))
        )
        spend = float(
            await s.scalar(
                select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(
                    LlmUsage.scraper_id == scraper.id, LlmUsage.created_at >= since7
                )
            )
            or 0.0
        )
        pending = await s.scalar(
            select(func.count(Repair.id)).where(
                Repair.scraper_id == scraper.id, Repair.status == "pending_approval"
            )
        )
        out.append(
            {
                "scraper": scraper,
                "last": last,
                "delta": delta,
                "version": active.version if active else None,
                "version_by": active.created_by if active else None,
                "spend7": spend,
                "pending_repairs": int(pending or 0),
            }
        )
    return out


async def version_rows(s: AsyncSession, scraper_id: int) -> list[ScriptVersion]:
    return list(
        (
            await s.scalars(
                select(ScriptVersion)
                .where(ScriptVersion.scraper_id == scraper_id)
                .order_by(desc(ScriptVersion.version))
            )
        ).all()
    )


async def scraper_stats(s: AsyncSession, scraper_id: int) -> dict[str, Any]:
    since7 = datetime.now(UTC) - timedelta(days=7)
    total = int(
        await s.scalar(
            select(func.count(Run.id)).where(Run.scraper_id == scraper_id, Run.created_at >= since7)
        )
        or 0
    )
    passed = int(
        await s.scalar(
            select(func.count(Run.id)).where(
                Run.scraper_id == scraper_id, Run.created_at >= since7, Run.status == RunStatus.PASSED
            )
        )
        or 0
    )
    avg_ms = await s.scalar(
        select(func.avg(Run.duration_ms)).where(
            Run.scraper_id == scraper_id, Run.duration_ms.is_not(None)
        )
    )
    spend = float(
        await s.scalar(
            select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(
                LlmUsage.scraper_id == scraper_id, LlmUsage.created_at >= since7
            )
        )
        or 0.0
    )
    rows = int(
        await s.scalar(select(func.count(Record.id)).where(Record.scraper_id == scraper_id)) or 0
    )
    return {
        "runs7": total,
        "passed7": passed,
        "pass_rate": (passed / total) if total else None,
        "avg_ms": int(avg_ms or 0),
        "spend7": spend,
        "rows_total": rows,
    }


async def row_count_series(s: AsyncSession, scraper_id: int, n: int = 20) -> list[Run]:
    q = select(Run).where(Run.scraper_id == scraper_id).order_by(desc(Run.created_at)).limit(n)
    return list(reversed(list((await s.scalars(q)).all())))


# ---------------------------------------------------------------------- spend
async def agent_spend(s: AsyncSession, days: int = 7) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await s.execute(
            select(LlmUsage.agent, func.sum(LlmUsage.cost_usd))
            .where(LlmUsage.created_at >= since)
            .group_by(LlmUsage.agent)
            .order_by(desc(func.sum(LlmUsage.cost_usd)))
        )
    ).all()
    data = [{"agent": a, "cost": float(c or 0.0)} for a, c in rows]
    total = sum(d["cost"] for d in data) or 0.0
    for d in data:
        d["pct"] = (d["cost"] / total * 100) if total else 0
    return data


# ----------------------------------------------------------------- run detail
async def run_with_scraper(s: AsyncSession, run_id: int) -> tuple[Run, Scraper] | None:
    row = (
        await s.execute(
            select(Run, Scraper).join(Scraper, Scraper.id == Run.scraper_id).where(Run.id == run_id)
        )
    ).first()
    return (row[0], row[1]) if row else None


async def runs_with_scrapers(
    s: AsyncSession,
    *,
    scraper_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple[Run, str]]:
    q = (
        select(Run, Scraper.name)
        .join(Scraper, Scraper.id == Run.scraper_id)
        .order_by(desc(Run.created_at))
        .limit(limit)
        .offset(offset)
    )
    if scraper_id is not None:
        q = q.where(Run.scraper_id == scraper_id)
    if status:
        q = q.where(Run.status == status)
    return [(r, n) for r, n in (await s.execute(q)).all()]


async def count_runs(s: AsyncSession, *, scraper_id: int | None = None, status: str | None = None) -> int:
    q = select(func.count(Run.id))
    if scraper_id is not None:
        q = q.where(Run.scraper_id == scraper_id)
    if status:
        q = q.where(Run.status == status)
    return int(await s.scalar(q) or 0)


# -------------------------------------------------------------------- records
async def records_page(
    s: AsyncSession,
    *,
    scraper_id: int | None = None,
    run_id: int | None = None,
    include_fallback: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple[Record, str]]:
    q = (
        select(Record, Scraper.name)
        .join(Scraper, Scraper.id == Record.scraper_id)
        .order_by(desc(Record.id))
        .limit(limit)
        .offset(offset)
    )
    if scraper_id is not None:
        q = q.where(Record.scraper_id == scraper_id)
    if run_id is not None:
        q = q.where(Record.run_id == run_id)
    if not include_fallback:
        q = q.where(Record.source == "script")
    return [(r, n) for r, n in (await s.execute(q)).all()]


def record_columns(rows: list[tuple[Record, str]], limit: int = 6) -> list[str]:
    """Union of the keys present, in first-seen order. Records are free-form JSON."""
    cols: list[str] = []
    for rec, _ in rows:
        for k in (rec.data or {}):
            if k not in cols:
                cols.append(k)
        if len(cols) >= limit:
            break
    return cols[:limit]


# --------------------------------------------------------------- field health
async def field_metrics(s: AsyncSession, scraper_id: int, runs: int = 30) -> list[dict[str, Any]]:
    """Per-field null rate across the last `runs` runs of one scraper."""
    recent = [
        r
        for r in (
            await s.scalars(
                select(Run.id).where(Run.scraper_id == scraper_id).order_by(desc(Run.created_at)).limit(runs)
            )
        ).all()
    ]
    if not recent:
        return []
    rows = (
        await s.execute(
            select(
                RunMetric.field,
                func.avg(RunMetric.null_rate.cast(Float)),
                func.max(RunMetric.distinct_count),
                func.count(RunMetric.id),
            )
            .where(RunMetric.run_id.in_(recent))
            .group_by(RunMetric.field)
            .order_by(RunMetric.field)
        )
    ).all()
    out = []
    for field, null_rate, distinct, n in rows:
        latest = await s.scalar(
            select(RunMetric)
            .where(RunMetric.field == field, RunMetric.run_id.in_(recent))
            .order_by(desc(RunMetric.run_id))
        )
        out.append(
            {
                "field": field,
                "null_rate": float(null_rate or 0.0),
                "distinct": int(distinct or 0),
                "runs": int(n or 0),
                "sample": latest.sample if latest else None,
                "latest_null_rate": float(latest.null_rate) if latest else 0.0,
            }
        )
    return out


async def field_series(s: AsyncSession, scraper_id: int, field: str, runs: int = 30) -> list[float]:
    recent = [
        r
        for r in (
            await s.scalars(
                select(Run.id).where(Run.scraper_id == scraper_id).order_by(desc(Run.created_at)).limit(runs)
            )
        ).all()
    ]
    if not recent:
        return []
    rows = (
        await s.execute(
            select(RunMetric.run_id, RunMetric.null_rate)
            .where(RunMetric.run_id.in_(recent), RunMetric.field == field)
            .order_by(RunMetric.run_id)
        )
    ).all()
    return [float(v or 0.0) for _, v in rows]


# ------------------------------------------------------------------ coverage
async def coverage_grid(s: AsyncSession, days: int = 30) -> tuple[list[dict[str, Any]], list[str]]:
    """Attempts and success rate per scraper per engine."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await s.execute(
            select(
                Scraper.name,
                Run.engine_used,
                func.count(Run.id),
                func.sum(case((Run.status == RunStatus.PASSED, 1), else_=0)),
            )
            .join(Scraper, Scraper.id == Run.scraper_id)
            .where(Run.created_at >= since, Run.engine_used.is_not(None))
            .group_by(Scraper.name, Run.engine_used)
            .order_by(Scraper.name)
        )
    ).all()
    by_scraper: dict[str, dict[str, dict[str, int]]] = {}
    engines: list[str] = []
    for name, engine, total, passed in rows:
        by_scraper.setdefault(name, {})[engine] = {"total": int(total), "passed": int(passed or 0)}
        if engine not in engines:
            engines.append(engine)
    engines.sort()
    return [
        {
            "name": name,
            "cells": [
                {
                    "engine": e,
                    "total": cells.get(e, {}).get("total", 0),
                    "pct": (
                        int(cells[e]["passed"] / cells[e]["total"] * 100)
                        if cells.get(e, {}).get("total")
                        else 0
                    ),
                }
                for e in engines
            ],
        }
        for name, cells in by_scraper.items()
    ], engines


async def block_reasons(s: AsyncSession, days: int = 30) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await s.execute(
            select(Run.block_reason, func.count(Run.id))
            .where(Run.created_at >= since, Run.block_reason.is_not(None))
            .group_by(Run.block_reason)
            .order_by(desc(func.count(Run.id)))
        )
    ).all()
    total = sum(int(c) for _, c in rows) or 1
    return [{"reason": r, "n": int(c), "pct": int(int(c) / total * 100)} for r, c in rows]


async def escalation_ladder(s: AsyncSession, days: int = 7) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await s.execute(
            select(
                Run.engine_used,
                func.count(Run.id),
                func.sum(case((Run.status == RunStatus.PASSED, 1), else_=0)),
            )
            .where(Run.created_at >= since, Run.engine_used.is_not(None))
            .group_by(Run.engine_used)
            .order_by(Run.engine_used)
        )
    ).all()
    out = []
    for engine, total, passed in rows:
        total, passed = int(total), int(passed or 0)
        out.append(
            {
                "engine": engine,
                "total": total,
                "passed": passed,
                "pct": int(passed / total * 100) if total else 0,
            }
        )
    return out


# ------------------------------------------------------------------ network
async def profiles(s: AsyncSession) -> list[Profile]:
    return list((await s.scalars(select(Profile).order_by(Profile.name))).all())


async def proxy_pools(s: AsyncSession) -> list[ProxyPool]:
    return list((await s.scalars(select(ProxyPool).order_by(ProxyPool.name))).all())


# ------------------------------------------------------------------ delivery
async def delivery_targets(s: AsyncSession) -> list[tuple[DeliveryTarget, str | None]]:
    rows = (
        await s.execute(
            select(DeliveryTarget, Scraper.name)
            .outerjoin(Scraper, Scraper.id == DeliveryTarget.scraper_id)
            .order_by(DeliveryTarget.id)
        )
    ).all()
    return [(t, n) for t, n in rows]


async def recent_deliveries(s: AsyncSession, limit: int = 30) -> list[tuple[Delivery, DeliveryTarget | None]]:
    rows = (
        await s.execute(
            select(Delivery, DeliveryTarget)
            .outerjoin(DeliveryTarget, DeliveryTarget.id == Delivery.target_id)
            .order_by(desc(Delivery.created_at))
            .limit(limit)
        )
    ).all()
    return [(d, t) for d, t in rows]


# ------------------------------------------------------------------- repairs
async def repairs_with_scrapers(
    s: AsyncSession, *, status: str | None = None, limit: int = 50
) -> list[tuple[Repair, str]]:
    q = (
        select(Repair, Scraper.name)
        .join(Scraper, Scraper.id == Repair.scraper_id)
        .order_by(desc(Repair.created_at))
        .limit(limit)
    )
    if status:
        q = q.where(Repair.status == status)
    return [(r, n) for r, n in (await s.execute(q)).all()]


async def repair_counts(s: AsyncSession, days: int = 30) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    pending = int(
        await s.scalar(select(func.count(Repair.id)).where(Repair.status == "pending_approval")) or 0
    )
    approved = int(
        await s.scalar(
            select(func.count(Repair.id)).where(Repair.status == "approved", Repair.created_at >= since)
        )
        or 0
    )
    rejected = int(
        await s.scalar(
            select(func.count(Repair.id)).where(Repair.status == "rejected", Repair.created_at >= since)
        )
        or 0
    )
    spend = float(
        await s.scalar(
            select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(
                LlmUsage.agent == "repair", LlmUsage.created_at >= since
            )
        )
        or 0.0
    )
    return {"pending": pending, "approved": approved, "rejected": rejected, "spend": spend}


# --------------------------------------------------------------------- audit
async def custom_python_versions(s: AsyncSession) -> list[tuple[ScriptVersion, str]]:
    rows = (
        await s.execute(
            select(ScriptVersion, Scraper.name)
            .join(Scraper, Scraper.id == ScriptVersion.scraper_id)
            .where(ScriptVersion.has_custom_python.is_(True))
            .order_by(desc(ScriptVersion.created_at))
        )
    ).all()
    return [(v, n) for v, n in rows]


async def audit_stats(s: AsyncSession) -> dict[str, Any]:
    total = int(await s.scalar(select(func.count(AuditEntry.id))) or 0)
    oldest = await s.scalar(select(func.min(AuditEntry.created_at)))
    secret_reads = int(
        await s.scalar(
            select(func.count(AuditEntry.id)).where(
                AuditEntry.action.like("%secret%"),
                AuditEntry.created_at >= datetime.now(UTC) - timedelta(days=30),
            )
        )
        or 0
    )
    return {"entries": total, "oldest": oldest, "secret_reads": secret_reads}
