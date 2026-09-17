"""LLM fallback extraction: chunking, tagging, and the per-run cost cap."""

from __future__ import annotations

import pytest

from smartscraper.agents.fake import FakeGateway
from smartscraper.agents.fallback import (
    chunk_html,
    extract_rows,
    required_fields,
    strip_html,
    wrap_schema,
)
from smartscraper.agents.gateway import Budget
from smartscraper.contracts import AgentResult, Usage
from smartscraper.db.models import RecordSource
from tests.test_agents_support import PAGE_HTML, settings_for

SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "price": {"type": "string"}},
        "required": ["name"],
    },
}


def an_extraction(rows, *, tokens=10_000, model="claude-sonnet-5") -> AgentResult:
    return AgentResult(
        ok=True,
        data={"rows": rows},
        usage=Usage(model=model, input_tokens=tokens, output_tokens=500),
    )


# --------------------------------------------------------------------------- text prep
def test_scripts_styles_and_comments_are_dropped():
    cleaned = strip_html(PAGE_HTML + "<!-- a note -->")
    assert "var a=1" not in cleaned
    assert ".x{}" not in cleaned
    assert "a note" not in cleaned
    assert "Alpha" in cleaned


def test_chunking_splits_on_tag_boundaries():
    html = "".join(f"<div class=row>{i:04d}</div>" for i in range(400))
    chunks = chunk_html(html, chunk_bytes=1_000)
    assert len(chunks) > 5
    assert sum(len(c) for c in chunks) == len(strip_html(html))
    assert all(not c.startswith(">") for c in chunks)


def test_chunking_respects_the_chunk_ceiling():
    html = "<div>" + ("x" * 50_000) + "</div>"
    assert len(chunk_html(html, chunk_bytes=1_000, max_chunks=3)) == 3


def test_an_empty_page_produces_no_chunks():
    assert chunk_html("") == []


def test_the_schema_is_wrapped_into_an_object_root():
    wrapped = wrap_schema(SCHEMA)
    assert wrapped["type"] == "object"
    assert wrapped["properties"]["rows"]["items"] == SCHEMA["items"]
    assert wrapped["required"] == ["rows"]
    assert required_fields(SCHEMA) == ["name"]


# --------------------------------------------------------------------------- extraction
async def test_rows_come_back_tagged_as_llm_fallback(tmp_path):
    gateway = FakeGateway(extractions=[an_extraction([{"name": "Alpha", "price": "$10.00"}])])
    result = await extract_rows(
        PAGE_HTML, gateway=gateway, output_schema=SCHEMA, settings=settings_for(tmp_path)
    )
    assert result.ok
    assert result.rows == [{"name": "Alpha", "price": "$10.00"}]
    assert result.source == RecordSource.LLM_FALLBACK.value == "llm_fallback"


async def test_the_configured_fallback_model_and_prompt_are_used(tmp_path):
    gateway = FakeGateway(extractions=[an_extraction([{"name": "Alpha"}])])
    await extract_rows(
        PAGE_HTML,
        gateway=gateway,
        output_schema=SCHEMA,
        settings=settings_for(tmp_path, fallback_model="claude-sonnet-5"),
    )
    call = gateway.extracts[0]
    assert call["model"] == "claude-sonnet-5"
    assert call["instructions"].startswith("Extract records from the HTML")
    assert "var a=1" not in call["text"]


async def test_every_chunk_is_sent_and_the_rows_are_merged(tmp_path):
    html = "".join(f"<div class=row>{i:04d}</div>" for i in range(400))
    gateway = FakeGateway(
        extractions=[an_extraction([{"name": f"row-{i}"}]) for i in range(20)]
    )
    result = await extract_rows(
        html,
        gateway=gateway,
        output_schema=SCHEMA,
        settings=settings_for(tmp_path),
        chunk_bytes=1_000,
        max_cost_usd=10.0,
    )
    assert result.chunks_sent == result.chunks_total
    assert result.row_count == result.chunks_total


async def test_duplicate_rows_across_chunks_are_kept_once(tmp_path):
    gateway = FakeGateway(
        extractions=[
            an_extraction([{"name": "Alpha"}]),
            an_extraction([{"name": "Alpha"}, {"name": "Beta"}]),
        ]
    )
    html = "<div>" + ("a" * 3_000) + "</div>"
    result = await extract_rows(
        html,
        gateway=gateway,
        output_schema=SCHEMA,
        settings=settings_for(tmp_path),
        chunk_bytes=1_600,
        max_cost_usd=10.0,
    )
    assert result.rows == [{"name": "Alpha"}, {"name": "Beta"}]


async def test_rows_missing_a_required_field_are_dropped_and_counted(tmp_path):
    gateway = FakeGateway(
        extractions=[an_extraction([{"name": "Alpha"}, {"price": "$1"}, {"name": ""}])]
    )
    result = await extract_rows(
        PAGE_HTML, gateway=gateway, output_schema=SCHEMA, settings=settings_for(tmp_path)
    )
    assert result.rows == [{"name": "Alpha"}]
    assert result.dropped == 2


async def test_the_cost_cap_stops_the_run_partway_through(tmp_path):
    html = "".join(f"<div class=row>{i:04d}</div>" for i in range(400))
    # Each call bills 1M sonnet input tokens: $2.00 exactly.
    gateway = FakeGateway(
        extractions=[
            an_extraction([{"name": f"row-{i}"}], tokens=1_000_000) for i in range(20)
        ]
    )
    result = await extract_rows(
        html,
        gateway=gateway,
        output_schema=SCHEMA,
        settings=settings_for(tmp_path),
        chunk_bytes=1_000,
        max_cost_usd=5.0,
    )
    assert result.capped
    assert result.chunks_sent == 3
    assert result.chunks_sent < result.chunks_total
    assert result.cost_usd == pytest.approx(3 * (2.00 + 500 * 10 / 1_000_000))


async def test_a_gateway_budget_refusal_surfaces_as_a_failed_extraction(tmp_path):
    gateway = FakeGateway(
        extractions=[an_extraction([{"name": "Alpha"}])],
        budget=Budget(limit_usd=1.0, spent_usd=1.0),
    )
    result = await extract_rows(
        PAGE_HTML, gateway=gateway, output_schema=SCHEMA, settings=settings_for(tmp_path)
    )
    assert not result.ok
    assert "budget exhausted" in result.error
    assert result.rows == []


async def test_an_extractor_error_stops_the_loop(tmp_path):
    gateway = FakeGateway(
        extractions=[AgentResult(ok=False, error="rate limited"), an_extraction([{"name": "X"}])]
    )
    result = await extract_rows(
        PAGE_HTML, gateway=gateway, output_schema=SCHEMA, settings=settings_for(tmp_path)
    )
    assert not result.ok
    assert result.error == "rate limited"
    assert result.chunks_sent == 1


async def test_rows_delivered_as_json_text_are_read_too(tmp_path):
    gateway = FakeGateway(
        extractions=[
            AgentResult(
                ok=True,
                text='{"rows": [{"name": "Alpha"}]}',
                usage=Usage(model="claude-sonnet-5", input_tokens=100),
            )
        ]
    )
    result = await extract_rows(
        PAGE_HTML, gateway=gateway, output_schema=SCHEMA, settings=settings_for(tmp_path)
    )
    assert result.rows == [{"name": "Alpha"}]


async def test_an_empty_page_is_reported_rather_than_sent(tmp_path):
    gateway = FakeGateway(extractions=[])
    result = await extract_rows(
        "", gateway=gateway, output_schema=SCHEMA, settings=settings_for(tmp_path)
    )
    assert not result.ok
    assert result.chunks_sent == 0
    assert gateway.extracts == []
