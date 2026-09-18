"""Network, coverage, delivery, the MCP console, audit, settings and the style
guide. These share one module because each is a single read-only screen."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper import repo
from smartscraper.config import get_settings
from smartscraper.web import actions, mcp_info, queries
from smartscraper.web.deps import DB, render

router = APIRouter(tags=["infra"])
SEE_OTHER = 303


# ------------------------------------------------------------------ network
@router.get("/network")
async def network(request: Request, session: AsyncSession = DB):
    return await render(
        request,
        session,
        "network.html",
        {
            "page_title": "Network",
            "page_sub": "profiles, proxies and solvers",
            "active": "Network",
            "profiles": await queries.profiles(session),
            "pools": await queries.proxy_pools(session),
            "ladder": await queries.escalation_ladder(session),
        },
    )


@router.post("/network/profiles/{profile_id}/relogin")
async def relogin(profile_id: int, request: Request, session: AsyncSession = DB):
    await actions.relogin_profile(session, profile_id)
    return RedirectResponse("/network", status_code=SEE_OTHER)


@router.get("/coverage")
async def coverage(request: Request, session: AsyncSession = DB):
    grid, engines = await queries.coverage_grid(session)
    return await render(
        request,
        session,
        "coverage.html",
        {
            "page_title": "Coverage",
            "page_sub": "which engine works on which site",
            "active": "Network",
            "grid": grid,
            "engines": engines,
            "blocks": await queries.block_reasons(session),
        },
    )


# ----------------------------------------------------------------- delivery
@router.get("/delivery")
async def delivery(request: Request, session: AsyncSession = DB):
    return await render(
        request,
        session,
        "delivery.html",
        {
            "page_title": "Delivery",
            "page_sub": "targets, log and the MCP server",
            "active": "Delivery",
            "targets": await queries.delivery_targets(session),
            "deliveries": await queries.recent_deliveries(session),
            "tools": await mcp_info.tools(),
            "templates": await mcp_info.resource_templates(),
            "mcp_endpoint": mcp_info.endpoint(str(request.base_url)),
            "mcp_mounted": getattr(request.app.state, "mcp_mounted", False),
        },
    )


@router.post("/delivery/targets/{target_id}/test")
async def test_target(target_id: int, request: Request, session: AsyncSession = DB):
    await actions.test_target(session, target_id)
    return RedirectResponse("/delivery", status_code=SEE_OTHER)


@router.post("/delivery/{delivery_id}/retry")
async def retry_delivery(delivery_id: int, request: Request, session: AsyncSession = DB):
    await actions.retry_delivery(session, delivery_id)
    return RedirectResponse("/delivery", status_code=SEE_OTHER)


@router.get("/mcp-console")
async def mcp_console(request: Request, session: AsyncSession = DB, tool: str = "get_results"):
    """The console.

    /mcp itself is the protocol endpoint, mounted in create_app, so the page
    lives here. The response pane is rendered from the database rather than by
    calling the tool: a page view must not trigger a write tool.
    """
    settings = get_settings()
    tools = await mcp_info.tools()
    names = [t["name"] for t in tools]
    if tool not in names:
        tool = names[0] if names else tool
    chosen = next((t for t in tools if t["name"] == tool), None)

    scrapers = await repo.list_scrapers(session)
    scraper = scrapers[0] if scrapers else None
    example = mcp_info.example_arguments(tool, scraper.name if scraper else "example-products")

    response = None
    if scraper is not None and tool in ("get_results", "search_results"):
        fresh = await repo.freshness(session, scraper.id)
        rows = await repo.get_records(session, scraper_id=scraper.id, limit=3)
        active = await repo.active_version(session, scraper.id)
        response = {
            "scraper": scraper.name,
            "contract": f"v{active.version}" if active else None,
            "freshness": {
                "stale": fresh.stale,
                "reason": fresh.reason,
                "last_clean_run_at": fresh.last_clean_run_at.isoformat() if fresh.last_clean_run_at else None,
                "fallback_rows": sum(1 for r in rows if r.source != "script"),
            },
            "rows": [dict(r.data or {}, source=r.source) for r in rows],
            "returned": len(rows),
        }
    return await render(
        request,
        session,
        "mcp_console.html",
        {
            "page_title": "MCP",
            "page_sub": "inbound tools, resources and outbound mapping",
            "active": "Delivery",
            "tools": tools,
            "tool": tool,
            "chosen": chosen,
            "templates": await mcp_info.resource_templates(),
            "endpoint": mcp_info.endpoint(str(request.base_url)),
            "mounted": getattr(request.app.state, "mcp_mounted", False),
            "token_set": bool(settings.mcp_bearer_token),
            "example": example,
            "response": response,
            "targets": [t for t, _ in await queries.delivery_targets(session) if t.kind == "mcp"],
        },
    )


# -------------------------------------------------------------------- audit
@router.get("/audit")
async def audit(request: Request, session: AsyncSession = DB, actor: str | None = None):
    return await render(
        request,
        session,
        "audit.html",
        {
            "page_title": "Audit log",
            "page_sub": "who did what, and which of it was an agent",
            "active": "Settings",
            "entries": await repo.audit(session, limit=200, actor=actor),
            "actor": actor,
            "stats": await queries.audit_stats(session),
            "custom_python": await queries.custom_python_versions(session),
        },
    )


# ----------------------------------------------------------------- settings
@router.get("/settings")
async def settings_page(request: Request, session: AsyncSession = DB):
    from smartscraper import settings_store

    await settings_store.load(session)      # whatever another process saved
    s = get_settings()
    fields = settings_store.current_view()
    groups: dict[str, list] = {}
    for f in fields:
        groups.setdefault(f["group"], []).append(f)
    return await render(
        request,
        session,
        "settings.html",
        {
            "page_title": "Settings",
            "page_sub": "smartscraper 0.1.0 · python 3.12 · sqlite",
            "active": "Settings",
            "s": s,
            "groups": groups,
            "fixed": sorted(settings_store.FIXED),
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
            "spend_by_agent": await repo.spend_by_agent(session, days=30),
            "records": await repo.count_records(session),
            "audit_stats": await queries.audit_stats(session),
            "mcp_endpoint": mcp_info.endpoint(str(request.base_url)),
            "mcp_tools": len(await mcp_info.tools()),
            "mcp_resources": len(await mcp_info.resource_templates()),
        },
    )


@router.post("/settings")
async def settings_save(request: Request, session: AsyncSession = DB):
    """Persist the editable settings and apply them to this process at once.

    An empty box clears the override rather than storing an empty value, so a
    field can always be handed back to whatever the environment says.
    """
    from urllib.parse import quote_plus

    from smartscraper import settings_store

    form = await request.form()
    # An unchecked box sends nothing, so "absent" has to mean false. That is only
    # safe for boxes the form actually rendered: without this marker a partial
    # post silently switched off every flag on the page, including the budget
    # guard, which is exactly the kind of change nobody would think to look for.
    present = {n for n in str(form.get("_present", "")).split(",") if n}

    changes: dict[str, object] = {}
    bad: list[str] = []
    for name in settings_store.EDITABLE:
        if settings_store.field_type(name) == "bool":
            if name in present:
                changes[name] = name in form
            continue
        if name not in form:
            continue
        try:
            changes[name] = settings_store.coerce(name, str(form[name]))
        except ValueError:
            bad.append(f"{name}: {form[name]!r} is not a number")

    if bad:
        return RedirectResponse(f"/settings?error={quote_plus('; '.join(bad))}", status_code=SEE_OTHER)

    applied, rejected = await settings_store.save(session, changes)
    if rejected:
        return RedirectResponse(f"/settings?error={quote_plus('; '.join(rejected))}", status_code=SEE_OTHER)
    return RedirectResponse(f"/settings?saved={len(applied)}", status_code=SEE_OTHER)


# ------------------------------------------------------------ builder live
@router.get("/builder")
async def builder_index(request: Request, session: AsyncSession = DB):
    """Recent builds. A build is in memory, so this is empty after a restart."""
    from smartscraper.web import builds

    recent = builds.recent()
    if len(recent) == 1 and not recent[0].done:
        return RedirectResponse(f"/builder/{recent[0].id}", status_code=SEE_OTHER)
    return await render(
        request, session, "builder_index.html",
        {"page_title": "Builds", "page_sub": "recent", "active": "Scrapers", "jobs": recent},
    )


@router.get("/builder/{job_id}")
async def builder_live(job_id: str, request: Request, session: AsyncSession = DB):
    """Watch one build. The page polls itself until the build ends."""
    from smartscraper.web import builds

    job = builds.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no build {job_id}")
    return await render(
        request, session, "builder_live.html",
        {
            "page_title": "Building",
            "page_sub": job.name or job.url,
            "active": "Scrapers",
            "job": job,
        },
    )


@router.get("/builder/{job_id}/card")
async def builder_card(job_id: str, request: Request, session: AsyncSession = DB):
    """The same page; htmx selects the card out of it. One template, no drift."""
    return await builder_live(job_id, request, session)


@router.get("/palette")
async def command_palette(request: Request, session: AsyncSession = DB):
    """Screen 22, Command palette. Not built.

    It is a keyboard overlay, not a page, so it needs client-side work rather
    than a route. This page says so and lists what it would do.
    """
    return await render(
        request,
        session,
        "not_built.html",
        {
            "page_title": "Command palette",
            "page_sub": "screen 22 of 23",
            "active": "Settings",
            "icon": "search",
            "headline": "The command palette is not built",
            "blurb": (
                "It is an overlay opened with a keyboard shortcut rather than a page, so it is "
                "client-side work: a search index over scrapers, runs and actions, plus the key "
                "binding. Nothing is bound today, so no shortcut is advertised anywhere in the UI."
            ),
            "needs": [
                ("a search endpoint", "one query across scraper names, run ids and stored records"),
                ("an action registry", "the same verbs the buttons post to, addressable by name"),
                ("a key binding and focus trap", "so the overlay is reachable and escapable by keyboard"),
            ],
            "links": [("Records search", "/records"), ("Scrapers", "/scrapers"), ("Runs", "/runs")],
        },
    )


@router.get("/styleguide")
async def styleguide(request: Request, session: AsyncSession = DB):
    return await render(
        request,
        session,
        "styleguide.html",
        {
            "page_title": "Style guide",
            "page_sub": "dense console · dark first",
            "active": "Settings",
            "screens": await _screen_map(session),
        },
    )


async def _screen_map(session: AsyncSession) -> list[dict[str, str]]:
    """The 23 screens of UI.md section 2, with the route each one lives at.

    Detail screens need something to point at, so they link to the first
    scraper and the newest run when the database has any.
    """
    scrapers = await repo.list_scrapers(session)
    sid = scrapers[0].id if scrapers else None
    runs = await repo.list_runs(session, limit=1)
    rid = runs[0].id if runs else None

    def s(n: int, name: str, href: str | None, state: str = "built") -> dict[str, str]:
        return {"n": n, "name": name, "href": href or "", "state": state}

    return [
        s(1, "Overview", "/"),
        s(2, "Scrapers", "/scrapers"),
        s(3, "Scraper detail", f"/scrapers/{sid}" if sid else None, "built" if sid else "no data"),
        s(4, "Records", "/records"),
        s(5, "Script and versions", f"/scrapers/{sid}/script" if sid else None,
          "built" if sid else "no data"),
        s(6, "Runs", "/runs"),
        s(7, "Run detail", f"/runs/{rid}" if rid else None, "built" if rid else "no data"),
        s(8, "Run live", f"/runs/{rid}/live" if rid else None, "built" if rid else "no data"),
        s(9, "New scraper", "/scrapers/new"),
        s(10, "Builder live", "/builder", "not built"),
        s(11, "Repairs", "/repairs"),
        s(12, "Repair review", "/repairs", "via the queue"),
        s(13, "Network", "/network"),
        s(14, "Delivery", "/delivery"),
        s(15, "Settings", "/settings"),
        s(16, "Style guide", "/styleguide"),
        s(17, "Schema and field health", f"/scrapers/{sid}/schema" if sid else None,
          "built" if sid else "no data"),
        s(18, "Coverage", "/coverage"),
        s(19, "MCP console", "/mcp-console"),
        s(20, "Audit log", "/audit"),
        s(21, "First run", "/", "empty database"),
        s(22, "Command palette", "/palette", "not built"),
        s(23, "Phone triage", "/", "responsive, 390px"),
    ]
