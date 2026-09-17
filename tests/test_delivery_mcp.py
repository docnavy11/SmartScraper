"""MCP sink: argument building and failure classification, with a fake client."""

from __future__ import annotations

from typing import Any

import pytest

from smartscraper.contracts import DeliveryResult  # noqa: F401  (documents the return type)
from smartscraper.delivery.mcp_ import McpSink, build_arguments, server_spec

ROWS = [{"name": "Widget", "price": 9.5}, {"name": "Gadget", "price": 12.0}]
META = {"scraper": "widgets", "run_id": 3, "extracted_at": "2026-09-17T09:00:00+00:00"}
SHEET_MAPPING = {"values[0]": "$run.extracted_at", "values[1]": "$row.name", "values[2]": "$row.price"}


class FakeClient:
    def __init__(self, result: Any = None, raises: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result
        self.raises = raises

    async def __call__(self, *, tool_name: str, arguments: dict, config: dict):
        self.calls.append({"tool": tool_name, "arguments": arguments, "config": config})
        if self.raises is not None:
            raise self.raises
        return self.result


def _cfg(**extra):
    return {"server_url": "http://127.0.0.1:9/mcp", "tool_name": "append_row", **extra}


def test_batch_mode_sends_one_call_with_every_row():
    payloads = build_arguments(ROWS, {}, META)
    assert payloads == [{"records": ROWS}]


def test_rows_key_names_the_argument():
    assert build_arguments(ROWS, {"rows_key": "items"}, META) == [{"items": ROWS}]


def test_an_arguments_template_injects_the_records():
    payloads = build_arguments(ROWS, {"arguments": {"table": "t", "rows": "$records"}}, META)
    assert payloads == [{"table": "t", "rows": ROWS}]


def test_per_row_mode_builds_a_values_array_per_record():
    payloads = build_arguments(ROWS, {"mode": "per_row", "mapping": SHEET_MAPPING}, META)
    assert payloads == [
        {"values": ["2026-09-17T09:00:00+00:00", "Widget", 9.5]},
        {"values": ["2026-09-17T09:00:00+00:00", "Gadget", 12.0]},
    ]


def test_per_row_mode_without_a_mapping_sends_the_raw_records():
    assert build_arguments(ROWS, {"mode": "per_row"}, META) == ROWS


async def test_a_successful_call_reports_every_row():
    client = FakeClient()
    result = await McpSink(client).send(ROWS, config=_cfg(), run_id=3, scraper="w", meta=META)
    assert result.ok
    assert result.rows_sent == 2
    assert len(client.calls) == 1
    assert client.calls[0]["tool"] == "append_row"


async def test_per_row_mode_makes_one_call_per_record():
    client = FakeClient()
    cfg = _cfg(mode="per_row", mapping=SHEET_MAPPING)
    result = await McpSink(client).send(ROWS, config=cfg, run_id=3, scraper="w", meta=META)
    assert result.ok
    assert len(client.calls) == 2
    assert client.calls[0]["arguments"]["values"][1] == "Widget"


def test_server_spec_for_an_http_target():
    spec = server_spec({"server_url": "http://127.0.0.1:9/mcp", "token": "t"})
    assert spec == {"url": "http://127.0.0.1:9/mcp", "token": "t"}


def test_server_spec_for_a_stdio_target():
    spec = server_spec({"command": "python", "args": ["-m", "srv"], "env": {"K": "v"}})
    assert spec == {"command": "python", "args": ["-m", "srv"], "env": {"K": "v"}}


def test_server_spec_needs_a_url_or_a_command():
    with pytest.raises(ValueError):
        server_spec({})


async def test_the_config_reaches_the_client():
    client = FakeClient()
    cfg = _cfg(headers={"authorization": "Bearer t"}, timeout_s=5)
    await McpSink(client).send(ROWS, config=cfg, run_id=3, scraper="w", meta=META)
    assert client.calls[0]["config"]["timeout_s"] == 5


async def test_a_transport_exception_is_retryable():
    client = FakeClient(raises=ConnectionError("refused"))
    result = await McpSink(client).send(ROWS, config=_cfg(), run_id=3, scraper="w", meta=META)
    assert not result.ok
    assert result.retryable is True
    assert "ConnectionError" in (result.error or "")


async def test_the_clients_retryable_flag_is_honoured():
    """MCPToolError carries retryable=False: the same arguments will fail forever."""
    from smartscraper.mcp.client import MCPClientError, MCPToolError

    tool_error = await McpSink(FakeClient(raises=MCPToolError("bad args", tool="t"))).send(
        ROWS, config=_cfg(), run_id=3, scraper="w", meta=META
    )
    assert tool_error.retryable is False

    socket_error = await McpSink(FakeClient(raises=MCPClientError("socket died"))).send(
        ROWS, config=_cfg(), run_id=3, scraper="w", meta=META
    )
    assert socket_error.retryable is True


async def test_retry_on_tool_error_overrides_a_terminal_client_error():
    from smartscraper.mcp.client import MCPToolError

    result = await McpSink(FakeClient(raises=MCPToolError("busy", tool="t"))).send(
        ROWS, config=_cfg(retry_on_tool_error=True), run_id=3, scraper="w", meta=META
    )
    assert result.retryable is True


async def test_a_tool_error_is_terminal_by_default():
    class ErrorResult:
        isError = True
        content = "unknown column"

    result = await McpSink(FakeClient(ErrorResult())).send(
        ROWS, config=_cfg(), run_id=3, scraper="w", meta=META
    )
    assert not result.ok
    assert result.retryable is False
    assert "unknown column" in (result.error or "")


async def test_retry_on_tool_error_can_be_opted_into():
    result = await McpSink(FakeClient({"isError": True, "content": "busy"})).send(
        ROWS, config=_cfg(retry_on_tool_error=True), run_id=3, scraper="w", meta=META
    )
    assert result.retryable is True


async def test_per_row_mode_reports_how_many_rows_got_through():
    calls = {"n": 0}

    async def flaky(*, tool_name, arguments, config):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("dropped")
        return None

    result = await McpSink(flaky).send(
        ROWS, config=_cfg(mode="per_row"), run_id=3, scraper="w", meta=META
    )
    assert not result.ok
    assert result.rows_sent == 1


@pytest.mark.parametrize("config", [{}, {"server_url": "http://x/mcp"}, {"tool_name": "t"}])
async def test_incomplete_config_is_terminal(config):
    result = await McpSink(FakeClient()).send(ROWS, config=config, run_id=3, scraper="w", meta=META)
    assert not result.ok
    assert result.retryable is False
