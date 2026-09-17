"""Webhook sink against a real local HTTP server.

The point of these tests is the retry classification: a 500 must be tried
again, a 400 must not, because the same payload will fail the same way forever.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from smartscraper.delivery.base import _send_with_retry
from smartscraper.delivery.webhook import WebhookSink, build_payload, is_retryable_status

ROWS = [{"name": "Widget", "price": 9.5}, {"name": "Gadget", "price": 12.0}]
META = {
    "scraper": "widgets", "run_id": 3, "status": "passed", "provisional": False,
    "extracted_at": "2026-09-17T09:00:00+00:00",
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length)
        server = self.server
        with server.lock:
            server.requests.append({
                "path": self.path,
                "headers": dict(self.headers),
                "body": json.loads(body) if body else None,
            })
            status = server.statuses.pop(0) if server.statuses else 200
        payload = json.dumps({"status": status}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:  # keep pytest output clean
        return


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.requests = []
    httpd.statuses = []
    httpd.lock = threading.Lock()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}/hook"
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _cfg(server, **extra):
    return {"url": server.url, "retry_backoff_s": 0, "max_attempts": 3, **extra}


async def test_a_single_200_is_one_request(server):
    server.statuses = [200]
    result = await WebhookSink().send(ROWS, config=_cfg(server), run_id=3, scraper="w", meta=META)
    assert result.ok
    assert result.rows_sent == 2
    assert result.detail == "HTTP 200"
    assert len(server.requests) == 1


async def test_two_500s_then_a_200_succeeds_after_retrying(server):
    server.statuses = [500, 500, 200]
    result, attempts = await _send_with_retry(
        WebhookSink(), ROWS, cfg=_cfg(server), run_id=3, scraper="w", meta=META
    )
    assert result.ok
    assert attempts == 3
    assert len(server.requests) == 3


async def test_a_500_is_retryable(server):
    server.statuses = [500]
    result = await WebhookSink().send(ROWS, config=_cfg(server), run_id=3, scraper="w", meta=META)
    assert not result.ok
    assert result.retryable is True
    assert result.detail == "HTTP 500"


async def test_a_400_is_not_retryable_and_is_sent_once(server):
    server.statuses = [400, 200]
    result, attempts = await _send_with_retry(
        WebhookSink(), ROWS, cfg=_cfg(server), run_id=3, scraper="w", meta=META
    )
    assert not result.ok
    assert result.retryable is False
    assert attempts == 1
    assert len(server.requests) == 1


async def test_retries_stop_at_max_attempts(server):
    server.statuses = [503, 503, 503, 503]
    result, attempts = await _send_with_retry(
        WebhookSink(), ROWS, cfg=_cfg(server), run_id=3, scraper="w", meta=META
    )
    assert not result.ok
    assert result.retryable is True
    assert attempts == 3
    assert len(server.requests) == 3


async def test_a_dead_endpoint_is_a_retryable_transport_error():
    cfg = {"url": "http://127.0.0.1:1/hook", "timeout_s": 2}
    result = await WebhookSink().send(ROWS, config=cfg, run_id=3, scraper="w", meta=META)
    assert not result.ok
    assert result.retryable is True
    assert result.detail == "transport error"


async def test_missing_url_is_terminal():
    result = await WebhookSink().send(ROWS, config={}, run_id=3, scraper="w", meta=META)
    assert not result.ok
    assert result.retryable is False


async def test_envelope_carries_run_metadata(server):
    server.statuses = [200]
    await WebhookSink().send(ROWS, config=_cfg(server), run_id=3, scraper="w", meta=META)
    body = server.requests[0]["body"]
    assert body["run_id"] == 3
    assert body["extracted_at"] == META["extracted_at"]
    assert body["records"] == ROWS


async def test_envelope_false_posts_a_bare_list(server):
    server.statuses = [200]
    await WebhookSink().send(ROWS, config=_cfg(server, envelope=False), run_id=3, scraper="w", meta=META)
    assert server.requests[0]["body"] == ROWS


async def test_mapping_reshapes_each_record(server):
    server.statuses = [200]
    cfg = _cfg(server, envelope=False, mapping={"title": "$row.name", "at": "$run.extracted_at"})
    await WebhookSink().send(ROWS, config=cfg, run_id=3, scraper="w", meta=META)
    assert server.requests[0]["body"] == [
        {"title": "Widget", "at": META["extracted_at"]},
        {"title": "Gadget", "at": META["extracted_at"]},
    ]


async def test_batching_splits_into_several_requests(server):
    server.statuses = [200, 200, 200]
    rows = [{"n": i} for i in range(5)]
    sink = WebhookSink()
    result, _ = await _send_with_retry(
        sink, rows, cfg=_cfg(server, batch_size=2), run_id=3, scraper="w", meta=META
    )
    assert result.ok
    assert result.rows_sent == 5
    assert len(server.requests) == 3
    assert [len(r["body"]["records"]) for r in server.requests] == [2, 2, 1]


async def test_bearer_token_becomes_an_authorization_header(server):
    server.statuses = [200]
    await WebhookSink().send(ROWS, config=_cfg(server, bearer_token="s3cret"),
                             run_id=3, scraper="w", meta=META)
    assert server.requests[0]["headers"]["authorization"] == "Bearer s3cret"


@pytest.mark.parametrize("status,expected", [
    (200, False), (400, False), (401, False), (403, False), (404, False), (422, False),
    (408, True), (429, True), (500, True), (502, True), (503, True), (504, True),
])
def test_status_classification(status, expected):
    assert is_retryable_status(status) is expected


def test_build_payload_counts_mapped_rows():
    payload = build_payload(ROWS, {"mapping": {"n": "$row.name"}}, META)
    assert payload["row_count"] == 2
    assert payload["records"] == [{"n": "Widget"}, {"n": "Gadget"}]
