"""Outbound MCP client tests.

The stdio tests start a real subprocess running a small fastmcp server, so the
transport, the handshake and the result unwrapping are all exercised rather than
mocked.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from smartscraper.mcp import client as mc

REMOTE_SERVER = textwrap.dedent(
    """
    from fastmcp import FastMCP

    srv = FastMCP(name="remote")

    @srv.tool(description="Accept a batch of scraped rows and report how many landed.")
    def ingest_rows(rows: list[dict], dataset: str = "default") -> dict:
        return {"accepted": len(rows), "dataset": dataset}

    @srv.tool(description="Return a plain string, no structured output.")
    def echo(text: str) -> str:
        return text.upper()

    @srv.tool(description="Always fails, to exercise error mapping.")
    def explode() -> str:
        raise ValueError("no thanks")

    srv.run(transport="stdio", show_banner=False)
    """
)


@pytest.fixture
def stdio_server(tmp_path):
    path = tmp_path / "remote_server.py"
    path.write_text(REMOTE_SERVER)
    return {"command": sys.executable, "args": [str(path)]}


# --------------------------------------------------------------------------- happy path
async def test_list_tools_returns_names_descriptions_and_schemas(stdio_server):
    tools = await mc.list_tools(stdio_server)
    by_name = {t["name"]: t for t in tools}
    assert set(by_name) == {"ingest_rows", "echo", "explode"}
    assert "batch of scraped rows" in by_name["ingest_rows"]["description"]
    assert "rows" in by_name["ingest_rows"]["input_schema"]["properties"]


async def test_call_tool_returns_structured_output(stdio_server):
    out = await mc.call_tool(
        stdio_server, "ingest_rows", {"rows": [{"a": 1}, {"a": 2}], "dataset": "shop"}
    )
    assert out == {"accepted": 2, "dataset": "shop"}


async def test_call_tool_returns_text_when_there_is_no_structure(stdio_server):
    assert await mc.call_tool(stdio_server, "echo", {"text": "hi"}) == "HI"


async def test_call_tool_with_no_arguments_is_allowed(stdio_server):
    out = await mc.call_tool(stdio_server, "ingest_rows", {"rows": []})
    assert out["accepted"] == 0


# --------------------------------------------------------------------------- errors
async def test_a_failing_tool_is_not_retryable(stdio_server):
    with pytest.raises(mc.MCPToolError) as exc:
        await mc.call_tool(stdio_server, "explode", {})
    assert exc.value.retryable is False
    assert exc.value.tool == "explode"


async def test_an_unknown_tool_is_reported_as_a_tool_error(stdio_server):
    with pytest.raises(mc.MCPToolError, match="missing"):
        await mc.call_tool(stdio_server, "missing", {})


async def test_unreachable_server_is_retried_once_then_reported_retryable(monkeypatch):
    monkeypatch.setattr(mc, "RETRY_DELAY_S", 0.0)
    attempts = {"n": 0}
    real = mc._build_client

    def counting(server, timeout):
        attempts["n"] += 1
        return real(server, timeout)

    monkeypatch.setattr(mc, "_build_client", counting)

    with pytest.raises(mc.MCPClientError) as exc:
        await mc.call_tool({"url": "http://127.0.0.1:1/mcp"}, "anything", {}, timeout=2)

    assert attempts["n"] == 2
    assert exc.value.retryable is True
    assert "after 2 attempts" in str(exc.value)


# --------------------------------------------------------------------------- spec validation
async def test_spec_without_url_or_command_is_rejected_without_retry():
    with pytest.raises(mc.MCPClientError, match="either 'url' or 'command'") as exc:
        await mc.call_tool({}, "x", {})
    assert exc.value.retryable is False


async def test_spec_with_both_url_and_command_is_rejected():
    with pytest.raises(mc.MCPClientError, match="both"):
        await mc.call_tool({"url": "http://x/mcp", "command": "python"}, "x", {})


async def test_spec_must_be_a_dict():
    with pytest.raises(mc.MCPClientError, match="must be a dict"):
        await mc.call_tool("http://x/mcp", "x", {})  # type: ignore[arg-type]


def test_url_aliases_are_accepted():
    built = mc._build_client({"server_url": "http://x/mcp", "bearer_token": "t"}, 5)
    assert built.transport.url == "http://x/mcp"


def test_stdio_spec_accepts_a_string_arg():
    built = mc._build_client({"command": "python", "args": "-V"}, 5)
    assert built.transport.args == ["-V"]


# --------------------------------------------------------------------------- unwrapping
def test_unwrap_prefers_structured_then_text_then_none():
    class R:
        data = None
        structured_content = {"a": 1}
        content: list = []

    assert mc._unwrap(R()) == {"a": 1}

    class Block:
        text = "hello"

    class T:
        data = None
        structured_content = None
        content = [Block()]

    assert mc._unwrap(T()) == "hello"

    class E:
        data = None
        structured_content = None
        content: list = []

    assert mc._unwrap(E()) is None
