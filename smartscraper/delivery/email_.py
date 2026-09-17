"""Email a run summary with the records attached as CSV.

Uses aiosmtplib when it is installed; otherwise stdlib smtplib in a worker
thread, which is why aiosmtplib is not a hard dependency. Module name has a
trailing underscore so it never shadows the stdlib ``email`` package.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
from typing import Any

from smartscraper.contracts import DeliveryResult
from smartscraper.delivery.base import BaseSink, register
from smartscraper.delivery.file import render_csv, render_jsonl

DEFAULT_SUBJECT = "[SmartScraper] {scraper} run {run_id}: {status} ({row_count} rows)"

#: SMTP reply codes that mean "this message will never be accepted".
TERMINAL_SMTP = frozenset({500, 501, 502, 503, 504, 521, 530, 534, 535, 538, 550, 551, 552, 553, 554})


def _addresses(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def build_message(
    rows: list[dict[str, Any]], config: dict[str, Any], *, run_id: int, scraper: str,
    meta: dict[str, Any],
) -> EmailMessage:
    """Build the outgoing message, attachment included."""
    fmt = str(config.get("attach_format") or config.get("fmt") or "csv").lower()
    fmt = "csv" if fmt not in ("csv", "jsonl", "ndjson") else ("jsonl" if fmt != "csv" else "csv")

    subject = str(config.get("subject") or DEFAULT_SUBJECT).format(
        scraper=scraper, run_id=run_id, status=meta.get("status", ""),
        row_count=len(rows), extracted_at=meta.get("extracted_at", ""),
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.get("from") or config.get("sender") or "smartscraper@localhost"
    to = _addresses(config.get("to"))
    msg["To"] = ", ".join(to)
    if _addresses(config.get("cc")):
        msg["Cc"] = ", ".join(_addresses(config["cc"]))

    lines = [
        f"Scraper: {scraper}",
        f"Run: {run_id} ({meta.get('status', '?')})",
        f"Extracted at: {meta.get('extracted_at', '?')}",
        f"Rows: {len(rows)}",
    ]
    if meta.get("provisional"):
        lines.append("")
        lines.append("This run did not pass validation. The attached rows are provisional.")
    if meta.get("error"):
        lines.append(f"Error: {meta['error']}")
    if config.get("body"):
        lines = [str(config["body"]), "", *lines]
    msg.set_content("\n".join(lines) + "\n")

    if rows and config.get("attach", True):
        if fmt == "csv":
            payload, subtype, ext = render_csv(rows, config), "csv", "csv"
        else:
            payload, subtype, ext = render_jsonl(rows), "x-ndjson", "jsonl"
        name = str(config.get("attachment_name") or f"{scraper or 'records'}-{run_id}.{ext}")
        msg.add_attachment(payload.encode("utf-8"), maintype="text", subtype=subtype, filename=name)
    return msg


@register
class EmailSink(BaseSink):
    kind = "email"
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
        recipients = (_addresses(config.get("to")) + _addresses(config.get("cc"))
                      + _addresses(config.get("bcc")))
        if not recipients:
            return DeliveryResult(ok=False, retryable=False, error="email target has no 'to' address")

        msg = build_message(rows, config, run_id=run_id, scraper=scraper, meta=meta)
        host = str(config.get("host", "localhost"))
        port = int(config.get("port", 25))

        try:
            await self._deliver(msg, config, host, port, recipients)
        except Exception as exc:
            code = getattr(exc, "code", None) or getattr(exc, "smtp_code", None)
            retryable = not (isinstance(code, int) and code in TERMINAL_SMTP)
            return DeliveryResult(ok=False, retryable=retryable, detail=f"smtp {host}:{port}",
                                  error=f"{type(exc).__name__}: {exc}")
        return DeliveryResult(ok=True, rows_sent=len(rows),
                              detail=f"emailed {len(rows)} rows to {', '.join(recipients)}")

    async def _deliver(
        self, msg: EmailMessage, config: dict[str, Any], host: str, port: int, recipients: list[str]
    ) -> None:
        try:
            import aiosmtplib
        except ImportError:
            await asyncio.to_thread(self._send_sync, msg, config, host, port, recipients)
            return
        await aiosmtplib.send(
            msg,
            hostname=host,
            port=port,
            username=config.get("username"),
            password=config.get("password"),
            use_tls=bool(config.get("use_tls", port == 465)),
            start_tls=bool(config.get("start_tls", port == 587)) or None,
            recipients=recipients,
            timeout=float(config.get("timeout_s", 30)),
        )

    @staticmethod
    def _send_sync(
        msg: EmailMessage, config: dict[str, Any], host: str, port: int, recipients: list[str]
    ) -> None:
        import smtplib

        timeout = float(config.get("timeout_s", 30))
        if config.get("use_tls", port == 465):
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            if config.get("start_tls", port == 587):
                server.starttls()
        try:
            if config.get("username"):
                server.login(str(config["username"]), str(config.get("password", "")))
            server.send_message(msg, to_addrs=recipients)
        finally:
            server.quit()
