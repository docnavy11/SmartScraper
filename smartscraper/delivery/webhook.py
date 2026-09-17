"""POST a run's records to an HTTP endpoint.

Retry policy, which is the whole point of this sink:

* 2xx is success.
* 408, 425, 429 and every 5xx are **retryable** - the endpoint may recover.
* Every other 4xx is **terminal** - a 400 means the payload is wrong, and
  sending the same payload again will fail the same way forever.
* A transport error (DNS, connect, read timeout) is retryable.

The HTTP status always lands in ``DeliveryResult.detail`` so the delivery log
shows what the endpoint actually said.
"""

from __future__ import annotations

import contextlib
from typing import Any

try:  # the project pins httpx2; plain httpx is API-compatible for what we use
    import httpx2 as httpx
except ImportError:  # pragma: no cover - environment dependent
    import httpx

from smartscraper.contracts import DeliveryResult
from smartscraper.delivery.base import BaseSink, register
from smartscraper.delivery.mapping import map_records

#: Status codes worth trying again.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 507, 509, 598, 599})

DEFAULT_TIMEOUT_S = 30.0


def is_retryable_status(status: int) -> bool:
    """A 500 is retryable, a 400 is not."""
    return status in RETRYABLE_STATUS or 500 <= status <= 599


def build_payload(
    rows: list[dict[str, Any]], config: dict[str, Any], meta: dict[str, Any]
) -> Any:
    """Shape the request body.

    ``mapping`` remaps each record (see mapping.py). ``envelope: false`` posts a
    bare list; otherwise the rows arrive under ``records`` beside run metadata,
    so a receiver can tell a provisional run from a clean one.
    """
    mapping = config.get("mapping")
    data = map_records(mapping, rows, meta) if mapping else rows
    if config.get("envelope", True) is False:
        return data
    return {
        "scraper": meta.get("scraper"),
        "run_id": meta.get("run_id"),
        "status": meta.get("status"),
        "provisional": meta.get("provisional"),
        "extracted_at": meta.get("extracted_at"),
        "row_count": len(data),
        "records": data,
    }


@register
class WebhookSink(BaseSink):
    kind = "webhook"
    batched = True

    async def send(
        self,
        rows: list[dict[str, Any]],
        *,
        config: dict[str, Any],
        run_id: int,
        scraper: str,
        meta: dict[str, Any],
    ) -> DeliveryResult:
        url = config.get("url")
        if not url:
            return DeliveryResult(ok=False, retryable=False, error="webhook target has no 'url'")

        method = str(config.get("method", "POST")).upper()
        headers = {"content-type": "application/json", **(config.get("headers") or {})}
        if config.get("bearer_token"):
            headers["authorization"] = f"Bearer {config['bearer_token']}"
        timeout = float(config.get("timeout_s", DEFAULT_TIMEOUT_S))
        payload = build_payload(rows, config, meta)

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.request(method, url, json=payload, headers=headers)
        except Exception as exc:
            return DeliveryResult(ok=False, retryable=True, error=f"{type(exc).__name__}: {exc}",
                                  detail="transport error")

        status = response.status_code
        detail = f"HTTP {status}"
        if 200 <= status < 300:
            return DeliveryResult(ok=True, rows_sent=len(rows), detail=detail)
        body = ""
        with contextlib.suppress(Exception):  # a binary or already-consumed body
            body = response.text[:500]
        return DeliveryResult(
            ok=False,
            retryable=is_retryable_status(status),
            detail=detail,
            error=f"{detail}: {body}" if body else detail,
        )
