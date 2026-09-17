"""FastAPI application factory.

    uvicorn smartscraper.web.app:app --reload

`create_app()` takes an optional engine so tests can hand it their own SQLite.
With no engine it builds one from smartscraper.config, creates the schema if it
is missing, and serves an empty database without erroring.

The inbound MCP server is mounted at /mcp by default. Its ASGI app carries the
lifespan that starts the streamable-HTTP session manager, so this app's own
lifespan enters it rather than replacing it: without that every request to /mcp
fails at runtime.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from smartscraper.config import get_settings
from smartscraper.db.models import Base
from smartscraper.db.session import init_engine
from smartscraper.web import auth
from smartscraper.web.deps import STATIC_DIR, render, templates
from smartscraper.web.routes import ROUTERS

log = logging.getLogger("smartscraper.web")

MCP_PATH = "/mcp"


def create_app(
    engine: AsyncEngine | None = None,
    *,
    create_schema: bool = True,
    mount_mcp: bool = True,
) -> FastAPI:
    """Build the app.

    `mount_mcp=False` leaves the MCP server out. FastMCP's session manager can
    only be run once per process, so a test suite that builds many apps must
    opt out; tests/test_web.py has one test that mounts it for real.
    """
    mcp_asgi = None
    if mount_mcp:
        from smartscraper.mcp.server import ensure_bind_allowed, http_app

        # Refuses an unauthenticated endpoint on anything but loopback. Let it
        # propagate: a startup crash naming the missing token is the point.
        ensure_bind_allowed(get_settings().host)
        mcp_asgi = http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        eng = engine or init_engine()
        app.state.engine = eng
        app.state.sessionmaker = async_sessionmaker(eng, expire_on_commit=False)
        app.state.mcp_mounted = mcp_asgi is not None
        if create_schema:
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        if mcp_asgi is None:
            yield
            return
        async with mcp_asgi.lifespan(app):
            yield

    app = FastAPI(
        title="SmartScraper", lifespan=lifespan,
        docs_url="/api/docs", openapi_url="/api/openapi.json",
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    for router in ROUTERS:
        app.include_router(router)

    # Mounted last so it cannot shadow a page route. The console page lives at
    # /mcp-console; this path belongs to the protocol endpoint.
    if mcp_asgi is not None:
        app.mount(MCP_PATH, mcp_asgi)
        log.info("MCP streamable HTTP mounted at %s/", MCP_PATH)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse(
            {"ok": True, "app": "smartscraper", "mcp": mcp_asgi is not None}
        )

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception) -> HTMLResponse:
        factory = getattr(request.app.state, "sessionmaker", None)
        if factory is None:
            return HTMLResponse("<h1>404</h1>", status_code=404)
        async with factory() as session:
            return await render(
                request,
                session,
                "error.html",
                {
                    "page_title": "Not found",
                    "page_sub": request.url.path,
                    "active": "",
                    "code": 404,
                    "message": "That page does not exist.",
                },
                status_code=404,
            )

    if auth.install(app):
        log.info("web UI is behind HTTP Basic auth as %r", get_settings().web_user)
    else:
        log.warning("web UI has no password; it must stay on loopback")

    return app


app = create_app()

__all__ = ["app", "create_app", "templates", "MCP_PATH"]
