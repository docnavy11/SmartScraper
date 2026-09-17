"""Inbound MCP server: SmartScraper as a tool for other agents.

Every tool reads through `smartscraper.repo` so query logic exists once. The
argument list and the description of each tool are the entire interface another
agent sees, so both are written for a reader who has never seen this codebase.

The one thing this server must never do is hand back rows that look clean when
they are not. `get_results` therefore always returns a `freshness` block saying
whether the newest run passed validation, why not, and how many of the returned
rows came from the LLM fallback rather than the deterministic script.

Mounting on the existing FastAPI app:

    from smartscraper.mcp.server import http_app
    mcp_asgi = http_app()                      # build once
    app = FastAPI(lifespan=mcp_asgi.lifespan)  # REQUIRED: session manager
    app.mount("/mcp", mcp_asgi)

Stdio:

    from smartscraper.mcp.server import run_stdio
    run_stdio()
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper import repo
from smartscraper.config import get_settings
from smartscraper.db.models import Record, Repair, Run, RunStatus, ScriptVersion, VersionStatus
from smartscraper.db.session import get_session
from smartscraper.mcp.jobs import enqueue

log = logging.getLogger(__name__)

SERVER_NAME = "smartscraper"
TERMINAL_RUN_STATES = {
    RunStatus.PASSED,
    RunStatus.VALIDATION_FAILED,
    RunStatus.ERROR,
    RunStatus.BLOCKED,
    RunStatus.CANCELLED,
}
MAX_LIMIT = 1000
MAX_WAIT_S = 600
#: How long to wait for the worker to create the Run row when wait=False.
APPEAR_TIMEOUT_S = 3.0


class InsecureBindError(RuntimeError):
    """Raised when the HTTP transport would listen off-localhost with no token."""


# --------------------------------------------------------------------------- auth
def _loopback(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    if h in {"localhost", "", "::1"}:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def ensure_bind_allowed(host: str, *, token: str | None = None) -> None:
    """Refuse to expose an unauthenticated MCP server to the network.

    Called by whoever binds the socket (the web app, or `run_http`). With no
    bearer token configured the only acceptable host is loopback, and the reason
    is stated rather than silently downgraded.
    """
    if token is None:
        token = get_settings().mcp_bearer_token
    if token:
        return
    if _loopback(host):
        log.warning(
            "MCP: no SS_MCP_BEARER_TOKEN configured. Serving on %s (loopback) without "
            "authentication. Set SS_MCP_BEARER_TOKEN before binding to any other address.",
            host,
        )
        return
    raise InsecureBindError(
        f"refusing to bind the MCP HTTP transport to {host!r}: no bearer token is configured. "
        "Set SS_MCP_BEARER_TOKEN, or bind to 127.0.0.1."
    )


def build_auth(token: str | None = None):
    """Bearer-token verification for the HTTP transport, or None on loopback."""
    if token is None:
        token = get_settings().mcp_bearer_token
    if not token:
        return None
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    return StaticTokenVerifier(
        tokens={token: {"client_id": "smartscraper-mcp", "scopes": ["scrapers:read", "scrapers:write"]}}
    )


# --------------------------------------------------------------------------- shaping
def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _scraper_row(sc: Any) -> dict[str, Any]:
    return {
        "id": sc.id,
        "name": sc.name,
        "url": sc.url,
        "goal": sc.goal,
        "enabled": sc.enabled,
        "schedule": sc.schedule,
        "promotion_policy": sc.promotion_policy,
        "fallback_enabled": sc.fallback_enabled,
        "tags": list(sc.tags or []),
        "created_at": _iso(sc.created_at),
    }


def _run_row(r: Run) -> dict[str, Any]:
    return {
        "id": r.id,
        "scraper_id": r.scraper_id,
        "script_version": r.script_version,
        "status": r.status,
        "trigger": r.trigger,
        "created_at": _iso(r.created_at),
        "started_at": _iso(r.started_at),
        "finished_at": _iso(r.finished_at),
        "duration_ms": r.duration_ms,
        "row_count": r.row_count,
        "engine_used": r.engine_used,
        "escalation_level": r.escalation_level,
        "block_reason": r.block_reason,
        "error": r.error,
        "cost_usd": r.cost_usd,
    }


def _record_row(rec: Record) -> dict[str, Any]:
    return {
        "id": rec.id,
        "run_id": rec.run_id,
        "scraper_id": rec.scraper_id,
        "source": rec.source,
        "created_at": _iso(rec.created_at),
        "data": rec.data,
    }


def _repair_row(rp: Repair) -> dict[str, Any]:
    return {
        "id": rp.id,
        "scraper_id": rp.scraper_id,
        "run_id": rp.run_id,
        "candidate_version": rp.candidate_version,
        "status": rp.status,
        "reason": rp.reason,
        "is_minor": rp.is_minor,
        "diff": rp.diff,
        "test_run_id": rp.test_run_id,
        "created_at": _iso(rp.created_at),
    }


async def _freshness_block(
    s: AsyncSession, scraper_id: int, rows: list[Record], *, include_fallback: bool
) -> dict[str, Any]:
    """Whether these rows can be trusted, and why not when they cannot."""
    f = await repo.freshness(s, scraper_id)
    fallback = sum(1 for r in rows if r.source != "script")
    return {
        "stale": f.stale,
        "reason": f.reason,
        "last_clean_run_at": _iso(f.last_clean_run_at),
        "last_run_at": _iso(f.last_run_at),
        "include_fallback": include_fallback,
        "fallback_rows": fallback,
        "script_rows": len(rows) - fallback,
        "fallback_row_ids": [r.id for r in rows if r.source != "script"],
    }


async def _resolve(s: AsyncSession, scraper: str | int) -> Any:
    ident: int | str = int(scraper) if str(scraper).isdigit() else str(scraper)
    sc = await repo.get_scraper(s, ident)
    if sc is None:
        raise ToolError(f"no scraper named {scraper!r}; call list_scrapers to see what exists")
    return sc


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


# --------------------------------------------------------------------------- server
mcp: FastMCP = FastMCP(
    name=SERVER_NAME,
    instructions=(
        "SmartScraper turns a URL and a plain-language goal into a deterministic scrape "
        "script, runs it on a schedule, validates every run, and repairs the script when "
        "the site changes. Use list_scrapers to see what is already collected, "
        "create_scraper to build a new one, run_scraper to trigger a run, and get_results "
        "to read rows. Always read the `freshness` block on get_results before using rows: "
        "it tells you whether the newest run passed validation and how many rows came from "
        "the LLM fallback rather than the script."
    ),
)


# ------------------------------------------------------------------ read: scrapers
@mcp.tool(
    annotations={"readOnlyHint": True, "title": "List scrapers"},
    description=(
        "List every configured scraper with its schedule, last run and data freshness. "
        "Start here: the `name` of each scraper is what every other tool accepts."
    ),
)
async def list_scrapers(
    enabled_only: Annotated[
        bool, Field(description="Return only scrapers that are currently enabled.")
    ] = False,
) -> dict[str, Any]:
    """Returns {"scrapers": [{...scraper, last_run, freshness}], "count": int}."""
    async with get_session() as s:
        out = []
        for sc in await repo.list_scrapers(s):
            if enabled_only and not sc.enabled:
                continue
            runs = await repo.list_runs(s, scraper_id=sc.id, limit=1)
            f = await repo.freshness(s, sc.id)
            row = _scraper_row(sc)
            row["last_run"] = _run_row(runs[0]) if runs else None
            row["freshness"] = {
                "stale": f.stale,
                "reason": f.reason,
                "last_clean_run_at": _iso(f.last_clean_run_at),
                "last_run_at": _iso(f.last_run_at),
            }
            out.append(row)
        return {"scrapers": out, "count": len(out)}


@mcp.tool(
    annotations={"readOnlyHint": True, "title": "Get scraper"},
    description=(
        "Full detail for one scraper: configuration, the active script version, recent runs, "
        "record count and freshness. Use it before run_scraper to see what the scraper "
        "collects and when it last succeeded."
    ),
)
async def get_scraper(
    scraper: Annotated[str, Field(description="Scraper name, or its numeric id as a string.")],
    recent_runs: Annotated[int, Field(description="How many recent runs to include.", ge=0, le=50)] = 5,
) -> dict[str, Any]:
    """Returns {"scraper": {...}, "active_version": {...}|None, "versions": int,
    "recent_runs": [...], "record_count": int, "freshness": {...}}."""
    async with get_session() as s:
        sc = await _resolve(s, scraper)
        av = await repo.active_version(s, sc.id)
        runs = await repo.list_runs(s, scraper_id=sc.id, limit=recent_runs)
        f = await repo.freshness(s, sc.id)
        return {
            "scraper": _scraper_row(sc),
            "active_version": (
                {
                    "version": av.version,
                    "created_by": av.created_by,
                    "change_summary": av.change_summary,
                    "has_custom_python": av.has_custom_python,
                    "output_schema": av.output_schema,
                    "created_at": _iso(av.created_at),
                }
                if av
                else None
            ),
            "versions": len(await repo.versions(s, sc.id)),
            "recent_runs": [_run_row(r) for r in runs],
            "record_count": await repo.count_records(s, sc.id),
            "freshness": {
                "stale": f.stale,
                "reason": f.reason,
                "last_clean_run_at": _iso(f.last_clean_run_at),
                "last_run_at": _iso(f.last_run_at),
            },
        }


# ------------------------------------------------------------------ write: build
@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "title": "Create scraper"},
    description=(
        "Create a scraper from a URL and a plain-language goal, and queue the builder agent "
        "that drives a real browser and writes the scrape script. This returns immediately "
        "with a job id; the build takes minutes and costs LLM tokens. Poll get_scraper with "
        "the returned name until active_version is set."
    ),
)
async def create_scraper(
    url: Annotated[str, Field(description="The page to scrape, including scheme, e.g. https://example.com/products")],
    goal: Annotated[
        str,
        Field(
            description=(
                "What to extract, in plain language. Be specific about the fields you want, "
                "e.g. 'every product card: name, price in EUR, and the link to its page'."
            )
        ),
    ],
    name: Annotated[
        str | None,
        Field(description="Unique short name. Defaults to a slug of the host and goal."),
    ] = None,
    schema: Annotated[
        dict[str, Any] | None,
        Field(description="Optional JSON Schema the output must match. The builder infers one when omitted."),
    ] = None,
    schedule: Annotated[
        str | None,
        Field(description="Cron expression for recurring runs, e.g. '0 6 * * *'. Omit for manual runs only."),
    ] = None,
) -> dict[str, Any]:
    """Returns {"name": str, "job_id": str|None, "queued_to_worker": bool,
    "status": "building", "scraper_id": int|None, "schedule": str|None}.

    The scraper row is created by the builder agent, not here: the builder is the
    only thing that knows whether a working script exists. scraper_id is null
    until it does.
    """
    if not url.startswith(("http://", "https://")):
        raise ToolError(f"url must start with http:// or https://, got {url!r}")
    if not goal.strip():
        raise ToolError("goal must not be empty: it is the entire specification for the builder")

    slug = _slug(name or _suggest_name(url, goal))
    async with get_session() as s:
        if await repo.get_scraper(s, slug) is not None:
            raise ToolError(f"a scraper named {slug!r} already exists; pass a different name")
        await repo.log(
            s, actor="mcp", action="create_scraper", object_type="scraper", object_ref=slug,
            detail=goal.strip()[:400],
            meta={"url": url, "schema": schema, "schedule": schedule},
        )

    job_id = enqueue("build_scraper", url=url, goal=goal.strip(), name=slug, output_schema=schema)
    async with get_session() as s:
        sc = await repo.get_scraper(s, slug)
        scraper_id = sc.id if sc else None
    return {
        "name": slug,
        "scraper_id": scraper_id,
        "schedule": schedule,
        "job_id": job_id,
        "queued_to_worker": job_id is not None,
        "status": "building",
    }


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:120] or "scraper"


def _suggest_name(url: str, goal: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "site").replace("www.", "")
    words = "-".join(goal.lower().split()[:3])
    return f"{host}-{words}"


# ------------------------------------------------------------------ write: run
@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "title": "Run scraper"},
    description=(
        "Trigger a run of an existing scraper now. Returns the run id as soon as the worker "
        "picks the job up. "
        "Set wait=true to block until the run reaches a terminal state, which can take minutes: "
        "a browser run fetches live pages. The returned status is 'passed' only when the run "
        "also passed validation."
    ),
)
async def run_scraper(
    scraper: Annotated[str, Field(description="Scraper name, or its numeric id as a string.")],
    wait: Annotated[
        bool, Field(description="Block until the run finishes instead of returning immediately.")
    ] = False,
    timeout_s: Annotated[
        int,
        Field(description="Seconds to wait when wait=true before giving up.", ge=1, le=MAX_WAIT_S),
    ] = 120,
) -> dict[str, Any]:
    """Returns {"run_id": int|None, "scraper": str, "status": str, "job_id": str|None,
    "queued_to_worker": bool, "waited": bool, "timed_out": bool, "run": {...}|None}.

    The worker creates the Run row, so run_id is null if no worker picked the job
    up before the appearance timeout. queued_to_worker says whether the job was
    accepted by the queue at all.
    """
    async with get_session() as s:
        sc = await _resolve(s, scraper)
        if not sc.enabled:
            raise ToolError(f"scraper {sc.name!r} is disabled; enable it in the UI before running it")
        if await repo.active_version(s, sc.id) is None:
            raise ToolError(
                f"scraper {sc.name!r} has no active script version yet; the builder has not finished"
            )
        latest = await repo.list_runs(s, scraper_id=sc.id, limit=1)
        known_run_id = latest[0].id if latest else 0
        await repo.log(
            s, actor="mcp", action="run_scraper", object_type="scraper", object_ref=sc.name,
            detail="manual trigger over MCP",
        )
        scraper_id, name = sc.id, sc.name

    job_id = enqueue("run_scraper", scraper_id=scraper_id, trigger="mcp")

    # An immediate-mode queue has already finished the run by now; a real worker
    # takes a moment to write the row. Wait briefly either way so the caller gets
    # a run id to poll with.
    appear_timeout = float(_clamp(timeout_s, 1, MAX_WAIT_S)) if wait else APPEAR_TIMEOUT_S
    run_id = await _await_new_run(scraper_id, after_id=known_run_id, timeout_s=appear_timeout)

    status = RunStatus.QUEUED.value
    timed_out = run_id is None
    if run_id is not None and wait:
        status, timed_out = await _await_run(run_id, timeout_s)

    async with get_session() as s:
        run = await repo.get_run(s, run_id) if run_id is not None else None
        return {
            "run_id": run_id,
            "scraper": name,
            "status": run.status if run else status,
            "job_id": job_id,
            "queued_to_worker": job_id is not None,
            "waited": wait,
            "timed_out": timed_out,
            "run": _run_row(run) if run else None,
        }


async def _await_new_run(scraper_id: int, *, after_id: int, timeout_s: float) -> int | None:
    """Wait for the worker to create a Run row newer than the one we saw before
    enqueueing. Returns its id, or None if nothing appeared in time."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    delay = 0.05
    while True:
        async with get_session() as s:
            runs = await repo.list_runs(s, scraper_id=scraper_id, limit=1)
        if runs and runs[0].id > after_id:
            return runs[0].id
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(delay)
        delay = min(delay * 2, 1.0)


async def _await_run(run_id: int, timeout_s: int) -> tuple[str, bool]:
    """Poll the run row until it is terminal. The runner is a subprocess owned by
    the worker, so the DB row is the only status we can observe from here."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _clamp(timeout_s, 1, MAX_WAIT_S)
    delay = 0.25
    status = RunStatus.QUEUED.value
    while True:
        async with get_session() as s:
            run = await repo.get_run(s, run_id)
            status = run.status if run else status
        if status in TERMINAL_RUN_STATES:
            return status, False
        if loop.time() >= deadline:
            return status, True
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 3.0)


# ------------------------------------------------------------------ read: runs
@mcp.tool(
    annotations={"readOnlyHint": True, "title": "Get run"},
    description=(
        "Status and outcome of one run: timings, row count, engine used, escalation level, "
        "the validator report, and per-field metrics. Read the validator report to find out "
        "why a run has status 'validation_failed' even though it raised no error."
    ),
)
async def get_run(
    run_id: Annotated[int, Field(description="Numeric run id, as returned by run_scraper or list_scrapers.")],
) -> dict[str, Any]:
    """Returns {"run": {...}, "validator_report": {...}|None, "metrics": [...],
    "deliveries": [...], "log_available": bool}."""
    async with get_session() as s:
        run = await repo.get_run(s, run_id)
        if run is None:
            raise ToolError(f"no run with id {run_id}")
        metrics = await repo.run_metrics(s, run_id)
        deliveries = await repo.deliveries_for_run(s, run_id)
        return {
            "run": _run_row(run),
            "validator_report": run.validator_report,
            "metrics": [
                {"field": m.field, "null_rate": m.null_rate, "distinct_count": m.distinct_count,
                 "sample": m.sample}
                for m in metrics
            ],
            "deliveries": [
                {"id": d.id, "target_id": d.target_id, "status": d.status, "attempts": d.attempts,
                 "rows_sent": d.rows_sent, "last_error": d.last_error}
                for d in deliveries
            ],
            "log_available": bool(run.log_path and Path(run.log_path).exists()),
        }


# ------------------------------------------------------------------ read: results
@mcp.tool(
    annotations={"readOnlyHint": True, "title": "Get results"},
    description=(
        "Fetch scraped rows for a scraper, newest first, with a freshness block. "
        "ALWAYS read `freshness` before using the rows: `stale` is true when the newest run "
        "did not pass validation, `reason` says why, and `fallback_rows` counts rows produced "
        "by the LLM fallback extractor rather than the deterministic script. Those rows carry "
        "source='llm_fallback' and are provisional. Pass include_fallback=false to exclude them."
    ),
)
async def get_results(
    scraper: Annotated[str, Field(description="Scraper name, or its numeric id as a string.")],
    run_id: Annotated[
        int | None,
        Field(description="Rows from this run only. Omit for rows across all runs, newest first."),
    ] = None,
    latest_run_only: Annotated[
        bool,
        Field(description="Restrict to the most recent run of this scraper. Ignored when run_id is given."),
    ] = False,
    include_fallback: Annotated[
        bool,
        Field(description="Include provisional rows from the LLM fallback. False returns script rows only."),
    ] = True,
    limit: Annotated[int, Field(description="Maximum rows to return.", ge=1, le=MAX_LIMIT)] = 100,
    offset: Annotated[int, Field(description="Rows to skip, for paging.", ge=0)] = 0,
) -> dict[str, Any]:
    """Returns {"scraper": str, "scraper_id": int, "run_id": int|None, "rows": [...],
    "count": int, "limit": int, "offset": int, "freshness": {...}}."""
    async with get_session() as s:
        sc = await _resolve(s, scraper)
        target = run_id
        if target is None and latest_run_only:
            runs = await repo.list_runs(s, scraper_id=sc.id, limit=1)
            # No runs yet: -1 matches nothing, so we return no rows rather than
            # silently widening to every run.
            target = runs[0].id if runs else -1
        rows = await repo.get_records(
            s, scraper_id=sc.id, run_id=target, include_fallback=include_fallback,
            limit=_clamp(limit, 1, MAX_LIMIT), offset=max(0, offset),
        )
        return {
            "scraper": sc.name,
            "scraper_id": sc.id,
            "run_id": target if target != -1 else None,
            "rows": [_record_row(r) for r in rows],
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "freshness": await _freshness_block(s, sc.id, rows, include_fallback=include_fallback),
        }


@mcp.tool(
    annotations={"readOnlyHint": True, "title": "Search results"},
    description=(
        "Substring search across stored rows, optionally within one scraper. Matches anywhere "
        "in a row's JSON, so it finds a value without you knowing which field holds it. "
        "Case-insensitive. Returns the same freshness block as get_results when a scraper is named."
    ),
)
async def search_results(
    query: Annotated[
        str,
        Field(description="Text to look for anywhere in a row, e.g. a SKU or a product name.", min_length=1),
    ],
    scraper: Annotated[
        str | None,
        Field(description="Restrict to one scraper by name or numeric id. Omit to search all scrapers."),
    ] = None,
    include_fallback: Annotated[
        bool, Field(description="Include provisional rows from the LLM fallback extractor.")
    ] = True,
    limit: Annotated[int, Field(description="Maximum rows to return.", ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int, Field(description="Rows to skip, for paging.", ge=0)] = 0,
) -> dict[str, Any]:
    """Returns {"query": str, "rows": [...], "count": int, "freshness": {...}|None}."""
    async with get_session() as s:
        scraper_id = None
        name = None
        if scraper is not None:
            sc = await _resolve(s, scraper)
            scraper_id, name = sc.id, sc.name
        rows = await repo.search_records(
            s, query=query, scraper_id=scraper_id, include_fallback=include_fallback,
            limit=_clamp(limit, 1, MAX_LIMIT), offset=max(0, offset),
        )
        fresh = (
            await _freshness_block(s, scraper_id, rows, include_fallback=include_fallback)
            if scraper_id is not None
            else None
        )
        return {
            "query": query,
            "scraper": name,
            "rows": [_record_row(r) for r in rows],
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "freshness": fresh,
        }


# ------------------------------------------------------------------ repairs
@mcp.tool(
    annotations={"readOnlyHint": True, "title": "Get pending repairs"},
    description=(
        "Script repairs waiting for human approval, with the reason the run failed and a "
        "unified diff of the proposed change. A repair that adds or changes a custom_python "
        "step always requires approval regardless of the scraper's promotion policy, because "
        "it is arbitrary code execution."
    ),
)
async def get_pending_repairs() -> dict[str, Any]:
    """Returns {"repairs": [{...repair, scraper}], "count": int}."""
    async with get_session() as s:
        out = []
        for rp in await repo.pending_repairs(s):
            sc = await repo.get_scraper(s, rp.scraper_id)
            row = _repair_row(rp)
            row["scraper"] = sc.name if sc else None
            out.append(row)
        return {"repairs": out, "count": len(out)}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "title": "Approve repair"},
    description=(
        "Approve a pending repair: the candidate script version becomes the active one and the "
        "previously active version is retired. This changes what every future run of that "
        "scraper executes, so read the diff from get_pending_repairs first. Irreversible from "
        "here: rolling back means promoting an older version in the web UI."
    ),
)
async def approve_repair(
    repair_id: Annotated[int, Field(description="Numeric repair id from get_pending_repairs.")],
    note: Annotated[str, Field(description="Why you approved it. Stored in the audit trail.")] = "",
) -> dict[str, Any]:
    """Returns {"repair_id": int, "status": "approved", "scraper": str,
    "promoted_version": int, "retired_version": int|None}."""
    async with get_session() as s:
        rp = await s.get(Repair, repair_id)
        if rp is None:
            raise ToolError(f"no repair with id {repair_id}")
        if rp.status != "pending_approval":
            raise ToolError(f"repair {repair_id} is already {rp.status!r}; nothing to approve")
        sc = await repo.get_scraper(s, rp.scraper_id)
        candidate = await s.scalar(
            select(ScriptVersion).where(
                ScriptVersion.scraper_id == rp.scraper_id,
                ScriptVersion.version == rp.candidate_version,
            )
        )
        if candidate is None:
            raise ToolError(
                f"repair {repair_id} points at version {rp.candidate_version} of "
                f"{sc.name if sc else rp.scraper_id}, which does not exist"
            )
        previous = await repo.active_version(s, rp.scraper_id)
        if previous is not None and previous.id != candidate.id:
            previous.status = VersionStatus.RETIRED.value
        candidate.status = VersionStatus.ACTIVE.value
        candidate.approved_by = "mcp"
        candidate.approved_at = datetime.now(UTC)
        rp.status = "approved"
        rp.decided_by = "mcp"
        rp.decided_at = datetime.now(UTC)
        await repo.log(
            s, actor="mcp", action="approve_repair", object_type="repair", object_ref=str(repair_id),
            detail=note or rp.reason, meta={"promoted_version": candidate.version},
        )
        return {
            "repair_id": repair_id,
            "status": "approved",
            "scraper": sc.name if sc else None,
            "promoted_version": candidate.version,
            "retired_version": previous.version if previous and previous.id != candidate.id else None,
        }


# ------------------------------------------------------------------ resources
@mcp.resource(
    "scraper://{name}/script",
    name="scrape script",
    description="The active scrape script (YAML) for a scraper, as the runner executes it.",
    mime_type="application/yaml",
)
async def scraper_script(name: str) -> str:
    async with get_session() as s:
        sc = await _resolve(s, name)
        av = await repo.active_version(s, sc.id)
        if av is None:
            raise ToolError(f"scraper {sc.name!r} has no active script version")
        return av.yaml


@mcp.resource(
    "run://{run_id}/log",
    name="run log",
    description="The captured stdout/stderr log of one run, as written by the run subprocess.",
    mime_type="text/plain",
)
async def run_log(run_id: str) -> str:
    async with get_session() as s:
        try:
            rid = int(run_id)
        except ValueError as exc:
            raise ToolError(f"run id must be numeric, got {run_id!r}") from exc
        run = await repo.get_run(s, rid)
        if run is None:
            raise ToolError(f"no run with id {rid}")
        if not run.log_path:
            return f"run {rid} ({run.status}) has no log file recorded"
        p = Path(run.log_path)
        if not p.exists():
            return f"run {rid}: log file {p} is gone (retention is {get_settings().keep_artifacts_days} days)"
        return p.read_text(errors="replace")


@mcp.resource(
    "scraper://{name}/schema",
    name="output schema",
    description="The JSON Schema the scraper's rows are validated against.",
    mime_type="application/json",
)
async def scraper_schema(name: str) -> str:
    async with get_session() as s:
        sc = await _resolve(s, name)
        av = await repo.active_version(s, sc.id)
        return json.dumps((av.output_schema if av else None) or {}, indent=2)


# ------------------------------------------------------------------ transports
@lru_cache(maxsize=1)
def http_app(path: str = "/"):
    """The ASGI app for the streamable HTTP transport.

    Mount it on the FastAPI app at /mcp. Its lifespan runs the MCP session
    manager, so the parent app MUST adopt it:

        mcp_asgi = http_app()
        app = FastAPI(lifespan=mcp_asgi.lifespan)
        app.mount("/mcp", mcp_asgi)
    """
    mcp.auth = build_auth()
    return mcp.http_app(path=path, transport="http")


def run_stdio() -> None:
    """Entry point for `smartscraper mcp --stdio`. No auth: stdio is a pipe to a
    process the user already started."""
    mcp.run(transport="stdio", show_banner=False)


def run_http(host: str | None = None, port: int | None = None) -> None:
    """Standalone HTTP server, for running the MCP endpoint without the web app."""
    settings = get_settings()
    host = host or settings.host
    ensure_bind_allowed(host)
    mcp.auth = build_auth()
    mcp.run(transport="http", host=host, port=port or settings.port, path="/mcp", show_banner=False)


__all__ = [
    "InsecureBindError",
    "build_auth",
    "ensure_bind_allowed",
    "http_app",
    "mcp",
    "run_http",
    "run_stdio",
]
