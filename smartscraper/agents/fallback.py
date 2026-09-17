"""LLM fallback extraction.

When a run fails and the repair has not landed yet, the captured HTML still
holds the data. This pulls rows straight out of it with one structured-output
call per chunk on Sonnet 5, so a consumer keeps receiving something while the
script is broken.

Rows produced this way are not script output and must never be mixed with it
silently: `FallbackResult.source` is what the `record.source` column gets, and
the caller is expected to carry it through to delivery.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from ..contracts import AgentResult, LLMGateway, Usage
from ..db.models import RecordSource
from . import prompts
from .cost import cost_of

# Bytes of HTML per call. Chosen to sit well inside the model's context with the
# schema and instructions alongside it; not tuned against a measurement.
DEFAULT_CHUNK_BYTES = 60_000
DEFAULT_MAX_CHUNKS = 20

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(slots=True)
class FallbackResult:
    ok: bool
    rows: list[dict[str, Any]] = field(default_factory=list)
    source: str = RecordSource.LLM_FALLBACK.value
    usages: list[Usage] = field(default_factory=list)
    cost_usd: float = 0.0
    chunks_sent: int = 0
    chunks_total: int = 0
    dropped: int = 0
    capped: bool = False
    error: str | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)


def strip_html(html: str) -> str:
    """Drop what no extractor needs: scripts, styles, comments, run-on whitespace."""
    text = _SCRIPT_OR_STYLE.sub(" ", html)
    text = _COMMENT.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def chunk_html(
    html: str,
    *,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    max_chunks: int | None = None,
) -> list[str]:
    """Split on tag boundaries so a record is less likely to be cut in half."""
    text = strip_html(html)
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_bytes, len(text))
        if end < len(text):
            boundary = text.rfind("><", start + chunk_bytes // 2, end)
            if boundary != -1:
                end = boundary + 1
        chunks.append(text[start:end])
        start = end
        if max_chunks is not None and len(chunks) >= max_chunks:
            break
    return chunks


def wrap_schema(output_schema: dict[str, Any]) -> dict[str, Any]:
    """Structured output needs an object at the root; the scraper's schema is a list."""
    items = output_schema
    if output_schema.get("type") == "array":
        items = output_schema.get("items", {"type": "object"})
    return {
        "type": "object",
        "properties": {"rows": {"type": "array", "items": items}},
        "required": ["rows"],
    }


def required_fields(output_schema: dict[str, Any]) -> list[str]:
    items = output_schema.get("items") if output_schema.get("type") == "array" else output_schema
    required = (items or {}).get("required") or []
    return [str(r) for r in required]


def rows_from(result: AgentResult) -> list[dict[str, Any]]:
    """Read rows out of an extract result, whether they arrived as data or as text."""
    payload = result.data
    if payload is None and result.text:
        try:
            parsed = json.loads(result.text)
        except (TypeError, ValueError):
            return []
        payload = parsed if isinstance(parsed, dict) else {"rows": parsed}
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


async def extract_rows(
    html: str,
    *,
    gateway: LLMGateway,
    output_schema: dict[str, Any],
    settings: Settings | None = None,
    model: str | None = None,
    max_cost_usd: float | None = None,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    instructions: str | None = None,
) -> FallbackResult:
    """One structured-output call per chunk, stopping at the per-run cost cap."""
    settings = settings or get_settings()
    model = model or settings.fallback_model
    cap = settings.default_scraper_budget if max_cost_usd is None else max_cost_usd
    system = instructions or prompts.load(prompts.FALLBACK)

    chunks = chunk_html(html, chunk_bytes=chunk_bytes, max_chunks=max_chunks)
    out = FallbackResult(ok=True, chunks_total=len(chunks))
    if not chunks:
        out.ok = False
        out.error = "the captured page had no extractable text"
        return out

    wrapped = wrap_schema(output_schema)
    needed = required_fields(output_schema)
    seen: set[str] = set()

    for chunk in chunks:
        if cap is not None and out.cost_usd >= cap:
            out.capped = True
            break

        result = await gateway.extract(
            text=chunk,
            output_schema=wrapped,
            model=model,
            instructions=system,
        )
        out.chunks_sent += 1
        if result.usage is not None:
            usage = result.usage
            if not usage.cost_usd:
                usage.cost_usd = cost_of(usage)
            out.usages.append(usage)
            out.cost_usd += usage.cost_usd

        if not result.ok:
            out.ok = False
            out.error = result.error or "the extractor returned an error"
            break

        for row in rows_from(result):
            if any(row.get(f) in (None, "") for f in needed):
                out.dropped += 1
                continue
            key = json.dumps(row, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            out.rows.append(row)

    if cap is not None and out.cost_usd >= cap and out.chunks_sent < out.chunks_total:
        out.capped = True
    return out
