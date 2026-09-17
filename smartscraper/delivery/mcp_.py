"""Deliver records by calling a tool on an external MCP server.

The target config names the server and the tool, and carries a mapping (see
``mapping.py``) that turns each record into the tool's argument object. The MCP
console screen in the web UI is an editor for exactly that mapping.

Config keys
-----------
``server_url``     streamable-HTTP MCP endpoint, or
``command``/``args``  a stdio server to spawn
``tool_name``      required, the tool to call
``mode``           ``per_row`` (one call per record) or ``batch`` (default)
``mapping``        per-record argument template, e.g.
                   ``{"values[0]": "$run.extracted_at", "values[1]": "$row.name"}``
``arguments``      call-level template for batch mode; the literal string
                   ``"$records"`` anywhere in it is replaced by the mapped rows
``rows_key``       where the mapped rows go in batch mode when no ``arguments``
                   template is given (default ``records``)

``token`` / ``headers``   auth for an HTTP server
``timeout_s``      per-call timeout, default 60

Transport
---------
``smartscraper.mcp.client.call_tool`` owns the connection, transport choice and
its own one-shot reconnect. It is imported lazily, so this sink loads even when
that package is absent; when it is, delivery fails with a clear, non-retryable
error rather than raising. Retryability comes from the client's exceptions: a
tool that rejected the arguments is terminal, a dead socket is not.
"""

from __future__ import annotations

import logging
from typing import Any

from smartscraper.contracts import DeliveryResult
from smartscraper.delivery.base import BaseSink, register
from smartscraper.delivery.mapping import map_record, map_records

log = logging.getLogger(__name__)

RECORDS_TOKEN = "$records"
DEFAULT_ROWS_KEY = "records"
DEFAULT_TIMEOUT_S = 60.0

#: The outbound MCP client. Owned by smartscraper/mcp/; imported lazily so this
#: sink loads even when that package is absent, and so a run that never delivers
#: over MCP never pays for the fastmcp import chain.
#:
#:     await client.call_tool(server_spec, tool, arguments, timeout)
#:
#: It raises MCPToolError (retryable=False: the tool rejected the arguments) and
#: MCPClientError (retryable=True: the socket died). Both carry `.retryable`,
#: which is what this sink classifies on.
CLIENT_MODULE = "smartscraper.mcp.client"


class McpUnavailable(RuntimeError):
    """No MCP client is importable."""


def server_spec(config: dict[str, Any]) -> dict[str, Any]:
    """Turn a delivery target's config into the client's server description."""
    if config.get("server_url") or config.get("url"):
        spec: dict[str, Any] = {"url": config.get("server_url") or config.get("url")}
        token = config.get("token") or config.get("bearer_token")
        if token:
            spec["token"] = token
        if config.get("headers"):
            spec["headers"] = config["headers"]
        return spec
    if config.get("command"):
        spec = {"command": config["command"], "args": list(config.get("args") or [])}
        if config.get("env"):
            spec["env"] = config["env"]
        if config.get("cwd"):
            spec["cwd"] = config["cwd"]
        return spec
    raise ValueError("mcp target needs a 'server_url' or a 'command'")


async def default_caller(*, tool_name: str, arguments: dict[str, Any], config: dict[str, Any]) -> Any:
    """Call the tool through smartscraper.mcp.client."""
    import importlib

    try:
        client = importlib.import_module(CLIENT_MODULE)
    except ImportError as exc:
        raise McpUnavailable(
            f"the mcp delivery sink needs {CLIENT_MODULE}, which is not importable: {exc}"
        ) from exc
    call_tool = getattr(client, "call_tool", None)
    if not callable(call_tool):
        raise McpUnavailable(f"{CLIENT_MODULE} has no call_tool()")
    return await call_tool(
        server_spec(config), str(tool_name), arguments, float(config.get("timeout_s", DEFAULT_TIMEOUT_S))
    )


def build_arguments(
    rows: list[dict[str, Any]], config: dict[str, Any], meta: dict[str, Any]
) -> list[dict[str, Any]]:
    """One argument object per tool call."""
    mapping = config.get("mapping") or {}
    mode = str(config.get("mode", "batch")).lower()

    if mode in ("per_row", "per_record", "row"):
        if not mapping:
            return [dict(r) for r in rows]
        return map_records(mapping, rows, meta)

    mapped = map_records(mapping, rows, meta) if mapping else [dict(r) for r in rows]
    template = config.get("arguments")
    if not template:
        return [{str(config.get("rows_key") or DEFAULT_ROWS_KEY): mapped}]
    resolved = map_record(template, {}, meta)
    return [_inject(resolved, mapped)]


def _inject(node: Any, records: list[dict[str, Any]]) -> Any:
    """Replace every literal ``"$records"`` with the mapped row list."""
    if isinstance(node, str):
        return records if node == RECORDS_TOKEN else node
    if isinstance(node, dict):
        return {k: _inject(v, records) for k, v in node.items()}
    if isinstance(node, list):
        return [_inject(v, records) for v in node]
    return node


def _is_error(result: Any) -> str | None:
    """Pull an error message out of whatever shape the client returned."""
    if result is None:
        return None
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        return str(getattr(result, "content", result))[:500]
    if isinstance(result, dict) and (result.get("isError") or result.get("is_error")):
        return str(result.get("content") or result)[:500]
    return None


@register
class McpSink(BaseSink):
    kind = "mcp"
    batched = True

    def __init__(self, caller: Any = None) -> None:
        self._caller = caller  # injectable for tests

    async def send(
        self,
        rows: list[dict[str, Any]],
        *,
        config: dict[str, Any],
        run_id: int,
        scraper: str,
        meta: dict[str, Any],
    ) -> DeliveryResult:
        tool_name = config.get("tool_name") or config.get("tool")
        if not tool_name:
            return DeliveryResult(ok=False, retryable=False, error="mcp target has no 'tool_name'")
        try:
            server_spec(config)
        except ValueError as exc:
            return DeliveryResult(ok=False, retryable=False, error=str(exc))

        caller = self._caller or default_caller
        payloads = build_arguments(rows, config, meta)
        retry_tool_errors = bool(config.get("retry_on_tool_error", False))

        for n, arguments in enumerate(payloads, start=1):
            try:
                result = await caller(tool_name=str(tool_name), arguments=arguments, config=config)
            except McpUnavailable as exc:
                return DeliveryResult(ok=False, retryable=False, error=str(exc))
            except ValueError as exc:
                return DeliveryResult(ok=False, retryable=False, error=f"bad mcp config: {exc}")
            except Exception as exc:
                # The client marks a tool rejection non-retryable and a dead
                # socket retryable; anything else is assumed worth another try.
                retryable = bool(getattr(exc, "retryable", True))
                if not retryable and retry_tool_errors:
                    retryable = True
                return DeliveryResult(
                    ok=False, retryable=retryable, rows_sent=_rows_done(n - 1, payloads, rows),
                    error=f"{type(exc).__name__}: {exc}",
                    detail=f"call {n}/{len(payloads)} to {tool_name} failed",
                )
            err = _is_error(result)
            if err:
                return DeliveryResult(
                    ok=False, retryable=retry_tool_errors, rows_sent=_rows_done(n - 1, payloads, rows),
                    error=err, detail=f"tool {tool_name} returned an error on call {n}/{len(payloads)}",
                )
        return DeliveryResult(ok=True, rows_sent=len(rows),
                              detail=f"{len(payloads)} call(s) to {tool_name} with {len(rows)} rows")


def _rows_done(completed_calls: int, payloads: list[Any], rows: list[Any]) -> int:
    """Rows confirmed delivered: per-row mode counts calls, batch mode is all-or-nothing."""
    return completed_calls if len(payloads) == len(rows) else 0
