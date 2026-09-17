"""Chat notifications (Telegram, Discord, Slack, ...) through apprise.

This is a notification sink, not a data sink: it sends a short summary and a
preview of the rows, because chat services truncate anyway. Pair it with
``only_on_failure: true`` for an alert-only target.
"""

from __future__ import annotations

import asyncio
from typing import Any

from smartscraper.contracts import DeliveryResult
from smartscraper.delivery.base import BaseSink, register
from smartscraper.delivery.mapping import MapContext, map_value

DEFAULT_TITLE = "{scraper}: run {run_id} {status}"
DEFAULT_PREVIEW_ROWS = 5
MAX_BODY_CHARS = 3500


def _urls(config: dict[str, Any]) -> list[str]:
    raw = config.get("urls") or config.get("url") or []
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return [str(u).strip() for u in raw if str(u).strip()]


def build_body(rows: list[dict[str, Any]], config: dict[str, Any], meta: dict[str, Any]) -> str:
    """Summary plus a short preview. A custom ``body`` template wins."""
    if config.get("body"):
        ctx = MapContext(row=rows[0] if rows else {}, run=meta, index=0)
        return str(map_value(config["body"], ctx))

    lines = [
        f"rows: {len(rows)}",
        f"status: {meta.get('status', '?')}",
        f"extracted at: {meta.get('extracted_at', '?')}",
    ]
    if meta.get("provisional"):
        lines.append("provisional: this run did not pass validation")
    if meta.get("error"):
        lines.append(f"error: {meta['error']}")
    n = int(config.get("preview_rows", DEFAULT_PREVIEW_ROWS))
    if n > 0 and rows:
        import json

        lines.append("")
        lines.append(f"first {min(n, len(rows))} of {len(rows)} rows:")
        for row in rows[:n]:
            lines.append(json.dumps(row, ensure_ascii=False, default=str)[:300])
    body = "\n".join(lines)
    return body[:MAX_BODY_CHARS]


@register
class AppriseSink(BaseSink):
    kind = "apprise"
    batched = False

    async def send(
        self,
        rows: list[dict[str, Any]],
        *,
        config: dict[str, Any],
        run_id: int,
        scraper: str,
        meta: dict[str, Any],
    ) -> DeliveryResult:
        urls = _urls(config)
        if not urls:
            return DeliveryResult(ok=False, retryable=False,
                                  error="apprise target has no 'urls' (e.g. tgram://…, discord://…)")
        try:
            import apprise
        except ImportError:
            return DeliveryResult(ok=False, retryable=False,
                                  error="the apprise delivery sink needs the apprise package")

        title = str(config.get("title") or DEFAULT_TITLE).format(
            scraper=scraper, run_id=run_id, status=meta.get("status", ""), row_count=len(rows),
        )
        body = build_body(rows, config, meta)
        notify_type = str(config.get("notify_type") or
                          ("failure" if meta.get("provisional") else "info"))

        client = apprise.Apprise()
        bad = [u for u in urls if not client.add(u)]
        if bad:
            return DeliveryResult(ok=False, retryable=False,
                                  error=f"apprise rejected {len(bad)} url(s): {', '.join(bad)}")

        ok = await asyncio.to_thread(client.notify, body=body, title=title, notify_type=notify_type)
        if not ok:
            # apprise reports a bare False; the service side may recover, so retry.
            return DeliveryResult(ok=False, retryable=True,
                                  error="apprise reported a failed notification",
                                  detail=f"{len(urls)} endpoint(s)")
        return DeliveryResult(ok=True, rows_sent=len(rows),
                              detail=f"notified {len(urls)} endpoint(s)")
