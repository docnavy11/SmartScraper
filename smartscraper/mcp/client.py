"""Outbound MCP client.

Delivery uses this to push rows to somebody else's MCP server. The surface is
deliberately tiny: describe the server, name a tool, pass arguments, get the
result back. Everything else (transport choice, connection, retry) is decided
here so no caller has to know fastmcp.

    rows_sent = await call_tool(
        {"url": "https://agent.example/mcp", "token": "..."},
        "ingest_rows",
        {"rows": [...]},
    )

A server is described by a dict:
    {"url": "https://host/mcp", "token": "...", "headers": {...}}   # HTTP
    {"command": "python", "args": ["-m", "srv"], "env": {...}, "cwd": "..."}  # stdio
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
#: One retry, after this pause. Long enough for a restarting server, short
#: enough that a delivery job does not sit on it.
RETRY_DELAY_S = 1.0

ServerSpec = dict[str, Any]


class MCPClientError(RuntimeError):
    """Anything that went wrong talking to an external MCP server.

    Carries `retryable` so the delivery scheduler can tell a dead socket from a
    tool that rejected the arguments; re-sending the latter will fail forever.
    """

    def __init__(self, message: str, *, retryable: bool = True, tool: str = "") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.tool = tool


class MCPToolError(MCPClientError):
    """The server was reached and the tool ran, but reported an error."""

    def __init__(self, message: str, *, tool: str = "") -> None:
        super().__init__(message, retryable=False, tool=tool)


def _build_client(server: ServerSpec, timeout: float):
    """Turn a server spec into a fastmcp Client. Imported lazily: the fastmcp
    client import chain is not cheap and a run that never delivers over MCP
    should not pay for it."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

    if not isinstance(server, dict):
        raise MCPClientError(f"server spec must be a dict, got {type(server).__name__}", retryable=False)

    url = server.get("url") or server.get("server_url")
    command = server.get("command")

    if url and command:
        raise MCPClientError("server spec has both 'url' and 'command'; pick one", retryable=False)

    if url:
        headers = dict(server.get("headers") or {})
        token = server.get("token") or server.get("bearer_token")
        transport = StreamableHttpTransport(url=str(url), headers=headers, auth=token or None)
    elif command:
        args = server.get("args") or []
        if isinstance(args, str):
            args = [args]
        transport = StdioTransport(
            command=str(command),
            args=[str(a) for a in args],
            env=server.get("env"),
            cwd=server.get("cwd"),
            keep_alive=False,
        )
    else:
        raise MCPClientError("server spec needs either 'url' or 'command'", retryable=False)

    return Client(transport, timeout=timeout, init_timeout=timeout)


def _is_retryable(exc: BaseException) -> bool:
    """A tool that raised is a bad request; a transport that died is worth one
    more try."""
    from fastmcp.exceptions import ToolError

    return not isinstance(exc, ToolError | ValueError | TypeError)


async def list_tools(server: ServerSpec, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    """What can this server do? Returns name/description/input schema per tool,
    which is what a human configuring a delivery target needs to see."""
    client = _build_client(server, timeout)
    try:
        async with asyncio.timeout(timeout + 5):
            async with client:
                tools = await client.list_tools()
    except MCPClientError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalised below
        raise MCPClientError(f"could not list tools: {exc}") from exc
    return [
        {
            "name": t.name,
            "description": getattr(t, "description", None) or "",
            # MCP SDK v2 renamed inputSchema -> input_schema; accept either.
            "input_schema": getattr(t, "input_schema", None) or {},
        }
        for t in tools
    ]


async def call_tool(
    server: ServerSpec,
    tool: str,
    arguments: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Call one tool on an external MCP server and return its result.

    Returns the tool's structured output when it has one, otherwise the joined
    text content. Raises MCPToolError if the tool reported an error (not worth
    retrying) and MCPClientError for transport failures (retryable). Connection
    and transport failures are retried exactly once.
    """
    args = dict(arguments or {})
    last: Exception | None = None

    for attempt in (1, 2):
        client = _build_client(server, timeout)
        try:
            async with asyncio.timeout(timeout + 5):
                async with client:
                    result = await client.call_tool(tool, args, timeout=timeout, raise_on_error=True)
            return _unwrap(result)
        except MCPClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalised into our own errors
            last = exc
            if not _is_retryable(exc):
                raise MCPToolError(f"tool {tool!r} failed: {exc}", tool=tool) from exc
            if attempt == 1:
                log.warning("MCP call to %r failed (%s); retrying once", tool, exc)
                await asyncio.sleep(RETRY_DELAY_S)

    raise MCPClientError(f"tool {tool!r} failed after 2 attempts: {last}", tool=tool) from last


def _unwrap(result: Any) -> Any:
    """Prefer structured output, fall back to text, never return the fastmcp
    wrapper: callers should not have to import fastmcp to read a result."""
    data = getattr(result, "data", None)
    if data is not None:
        return data
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    blocks = getattr(result, "content", None) or []
    texts = [b.text for b in blocks if getattr(b, "text", None) is not None]
    if texts:
        return "\n".join(texts)
    return None
