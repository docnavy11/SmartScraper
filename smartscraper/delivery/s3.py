"""Upload a run's records to S3 or any S3-compatible endpoint.

boto3 is an optional dependency, imported inside the call so that a system
without it can still load the registry and use every other sink. boto3 is
synchronous, so the upload runs in a worker thread.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from smartscraper.contracts import DeliveryResult
from smartscraper.delivery.base import BaseSink, register
from smartscraper.delivery.file import render_csv, render_jsonl

DEFAULT_KEY_TEMPLATE = "{scraper}/{date}/run-{run_id}.{ext}"

#: Errors where trying again later is pointless.
TERMINAL_CODES = frozenset({
    "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "NoSuchBucket",
    "InvalidBucketName", "AllAccessDisabled", "EntityTooLarge", "ExpiredToken",
})


def render_key(config: dict[str, Any], *, run_id: int, scraper: str, fmt: str) -> str:
    template = str(config.get("key") or config.get("key_template") or DEFAULT_KEY_TEMPLATE)
    now = datetime.now(UTC)
    return template.format(
        scraper=scraper or "scraper",
        run_id=run_id,
        date=now.strftime("%Y-%m-%d"),
        ts=now.strftime("%Y%m%dT%H%M%SZ"),
        ext="jsonl" if fmt == "jsonl" else fmt,
        fmt=fmt,
        prefix=str(config.get("prefix", "")).strip("/"),
    ).lstrip("/")


def render_body(rows: list[dict[str, Any]], fmt: str, config: dict[str, Any]) -> tuple[bytes, str]:
    if fmt == "csv":
        return render_csv(rows, config).encode("utf-8"), "text/csv"
    if fmt == "json":
        import json

        return json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8"), "application/json"
    return render_jsonl(rows).encode("utf-8"), "application/x-ndjson"


@register
class S3Sink(BaseSink):
    kind = "s3"
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
        try:
            import boto3  # noqa: F401
        except ImportError:
            return DeliveryResult(
                ok=False, retryable=False,
                error="the s3 delivery sink needs boto3; install the 's3' extra "
                      "(pip install 'smartscraper[s3]')",
            )
        bucket = config.get("bucket")
        if not bucket:
            return DeliveryResult(ok=False, retryable=False, error="s3 target has no 'bucket'")

        fmt = str(config.get("fmt") or "jsonl").lower()
        fmt = "jsonl" if fmt in ("jsonl", "ndjson") else fmt
        if fmt not in ("jsonl", "json", "csv"):
            return DeliveryResult(ok=False, retryable=False,
                                  error=f"s3 sink cannot write {fmt!r}; use jsonl, json or csv")
        body, content_type = render_body(rows, fmt, config)
        key = render_key(config, run_id=run_id, scraper=scraper, fmt=fmt)

        try:
            await asyncio.to_thread(self._put, config, bucket, key, body, content_type)
        except Exception as exc:
            code = getattr(getattr(exc, "response", None), "get", lambda *_: None)("Error") or {}
            code = code.get("Code") if isinstance(code, dict) else None
            return DeliveryResult(
                ok=False, retryable=code not in TERMINAL_CODES,
                error=f"{type(exc).__name__}: {exc}", detail=f"s3://{bucket}/{key}",
            )
        return DeliveryResult(ok=True, rows_sent=len(rows),
                              detail=f"wrote {len(rows)} rows to s3://{bucket}/{key}")

    @staticmethod
    def _put(config: dict[str, Any], bucket: str, key: str, body: bytes, content_type: str) -> None:
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=config.get("endpoint_url"),
            region_name=config.get("region"),
            aws_access_key_id=config.get("access_key_id"),
            aws_secret_access_key=config.get("secret_access_key"),
        )
        extra: dict[str, Any] = {"ContentType": content_type}
        if config.get("acl"):
            extra["ACL"] = config["acl"]
        if config.get("storage_class"):
            extra["StorageClass"] = config["storage_class"]
        client.put_object(Bucket=bucket, Key=key, Body=body, **extra)
