"""The streamable HTTP transport, mounted on a FastAPI app exactly as the web
subsystem will mount it, served by a real uvicorn on an ephemeral port.

This is the test that proves the symbol the web agent mounts actually works and
that the bearer token is enforced, rather than only that `build_auth` returns an
object.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

import pytest
import uvicorn
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from smartscraper.config import get_settings
from smartscraper.db import session as dbsession
from smartscraper.db.models import Base
from smartscraper.mcp import server as srv

TOKEN = "test-bearer-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def http_server(monkeypatch):
    """A FastAPI app with the MCP ASGI app mounted at /mcp, bearer auth on."""
    monkeypatch.setenv("SS_MCP_BEARER_TOKEN", TOKEN)
    get_settings.cache_clear()
    srv.http_app.cache_clear()

    engine = dbsession.init_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    mcp_asgi = srv.http_app()
    app = FastAPI(lifespan=mcp_asgi.lifespan)
    app.mount("/mcp", mcp_asgi)

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "uvicorn did not start"

    yield f"http://127.0.0.1:{port}/mcp/"

    server.should_exit = True
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=10)
    await engine.dispose()
    dbsession._engine = None
    dbsession._factory = None
    srv.mcp.auth = None
    srv.http_app.cache_clear()
    get_settings.cache_clear()


async def test_mounted_app_serves_tools_with_a_valid_token(http_server):
    async with Client(StreamableHttpTransport(url=http_server, auth=TOKEN)) as c:
        names = {t.name for t in await c.list_tools()}
    assert "get_results" in names


async def test_our_own_outbound_client_can_talk_to_it(http_server):
    """The inbound server and the outbound client meet: proof both halves speak
    the same protocol over HTTP."""
    from smartscraper.mcp.client import call_tool

    out = await call_tool({"url": http_server, "token": TOKEN}, "list_scrapers", {})
    assert out == {"scrapers": [], "count": 0}


async def test_a_wrong_token_is_rejected(http_server):
    from smartscraper.mcp.client import MCPClientError, call_tool

    with pytest.raises(MCPClientError):
        await call_tool({"url": http_server, "token": "wrong"}, "list_scrapers", {}, timeout=5)


async def test_no_token_is_rejected(http_server):
    from smartscraper.mcp.client import MCPClientError, call_tool

    with pytest.raises(MCPClientError):
        await call_tool({"url": http_server}, "list_scrapers", {}, timeout=5)
