"""What the MCP console and the delivery screen show about the inbound server.

The tool and resource lists are read from the live FastMCP server rather than
retyped here, so the console cannot drift from what agents actually see. If the
server cannot be introspected the screens say so instead of showing a stale
hard-coded list.
"""

from __future__ import annotations

import logging
from typing import Any

from smartscraper.config import get_settings

log = logging.getLogger("smartscraper.web.mcp")

_tools_cache: list[dict[str, Any]] | None = None
_templates_cache: list[dict[str, Any]] | None = None


def _first_sentence(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    head = text.split(". ")[0].rstrip(".")
    return head if len(head) <= limit else head[: limit - 1] + "…"


def _annotation(tool: Any, *names: str) -> Any:
    """Read a tool annotation across the MCP SDK v1/v2 field spellings.

    Names are tried in order and the first one that EXISTS wins, not the first
    non-None: `destructive_hint` is legitimately None on a read-only tool, and
    falling through to the v1 spelling there only raises a deprecation warning.
    """
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return None
    fields = getattr(type(ann), "model_fields", None)
    for n in names:
        if (fields is not None and n in fields) or n in getattr(ann, "__dict__", {}):
            return getattr(ann, n, None)
    for n in names:
        try:
            return getattr(ann, n)
        except AttributeError:
            continue
    return None


async def tools() -> list[dict[str, Any]]:
    """Every inbound tool, with the write ones marked."""
    global _tools_cache
    if _tools_cache is not None:
        return _tools_cache
    try:
        from smartscraper.mcp.server import mcp

        listed = await mcp.list_tools()
    except Exception as exc:  # the console renders a message rather than 500ing
        log.warning("could not introspect the MCP tools: %s", exc)
        return []
    out = []
    for t in listed:
        read_only = bool(_annotation(t, "read_only_hint", "readOnlyHint"))
        out.append(
            {
                "name": t.name,
                "title": _annotation(t, "title") or t.name.replace("_", " "),
                "summary": _first_sentence(t.description or ""),
                "description": " ".join((t.description or "").split()),
                "read_only": read_only,
                "destructive": bool(_annotation(t, "destructive_hint", "destructiveHint")),
                "schema": getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {},
            }
        )
    _tools_cache = out
    return out


async def resource_templates() -> list[dict[str, Any]]:
    """The URI templates an agent can read directly, without calling a tool."""
    global _templates_cache
    if _templates_cache is not None:
        return _templates_cache
    try:
        from smartscraper.mcp.server import mcp

        listed = await mcp.list_resource_templates()
    except Exception as exc:
        log.warning("could not introspect the MCP resource templates: %s", exc)
        return []
    out = []
    for r in listed:
        out.append(
            {
                "uri": getattr(r, "uri_template", None) or getattr(r, "uriTemplate", ""),
                "name": getattr(r, "name", ""),
                "mime_type": getattr(r, "mime_type", None) or getattr(r, "mimeType", ""),
                "description": " ".join((getattr(r, "description", "") or "").split()),
            }
        )
    _templates_cache = out
    return out


def endpoint(base: str | None = None) -> str:
    """The streamable-HTTP URL, with the trailing slash the transport expects."""
    s = get_settings()
    return f"{(base or f'http://{s.host}:{s.port}').rstrip('/')}/mcp/"


def example_arguments(tool_name: str, scraper_name: str) -> dict[str, Any]:
    """A plausible call for the console's request pane, per tool."""
    return {
        "list_scrapers": {"enabled_only": False},
        "get_scraper": {"scraper": scraper_name, "recent_runs": 5},
        "create_scraper": {
            "url": "https://shop.example.eu/pricing",
            "goal": "every product with its name, price and currency",
        },
        "run_scraper": {"scraper": scraper_name, "wait": False},
        "get_run": {"run_id": 1},
        "get_results": {"scraper": scraper_name, "limit": 3, "include_fallback": False},
        "search_results": {"query": "lavazza", "scraper": scraper_name},
        "get_pending_repairs": {},
        "approve_repair": {"repair_id": 1, "note": "selector fix, tested"},
    }.get(tool_name, {"scraper": scraper_name})


def reset_cache() -> None:
    """Tests call this after swapping the server out."""
    global _tools_cache, _templates_cache
    _tools_cache = None
    _templates_cache = None
