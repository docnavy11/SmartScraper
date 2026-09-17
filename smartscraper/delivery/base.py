"""Sink registry and the per-run delivery orchestrator.

Rules this module enforces, so no sink has to:

* A run that did not pass validation is **held**, not delivered, unless the
  target opted in with ``provisional: true`` in its config. A target marked
  ``only_on_failure`` is the exception: failure is exactly what it exists for.
* One target failing never stops the others. Every target is attempted.
* A retryable failure is retried in-process with exponential backoff (tenacity)
  and, if it still fails, parked as a ``pending`` Delivery row with
  ``next_attempt_at`` set. ``retry_pending`` picks those up later.
* A non-retryable failure (a 400, a bad config) is terminal: status ``failed``.

Delivery status vocabulary, written to ``Delivery.status``:
``sent``, ``pending`` (will retry), ``failed`` (terminal), ``held``, ``skipped``.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tenacity import AsyncRetrying, RetryError, retry_if_exception_type, stop_after_attempt, wait_exponential

from smartscraper import repo
from smartscraper.contracts import DeliveryResult, Sink
from smartscraper.db.models import Delivery, DeliveryTarget, Run, RunStatus, Scraper
from smartscraper.db.session import get_session

log = logging.getLogger(__name__)

__all__ = [
    "BaseSink",
    "DeliveryOutcome",
    "SENT", "PENDING", "FAILED", "HELD", "SKIPPED",
    "deliver_one",
    "deliver_run",
    "get_sink",
    "known_kinds",
    "register",
    "retry_pending",
    "run_context",
]

SENT = "sent"
PENDING = "pending"
FAILED = "failed"
HELD = "held"
SKIPPED = "skipped"

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_S = 2.0
MAX_BACKOFF_S = 3600.0
DEFAULT_BATCH_SIZE = 500


# --------------------------------------------------------------------------- registry
#: kind -> "module:ClassName". Imported lazily so a missing optional dependency
#: (boto3, apprise, pyarrow) only breaks the sink that needs it.
BUILTIN_SINKS: dict[str, str] = {
    "file": "smartscraper.delivery.file:FileSink",
    "webhook": "smartscraper.delivery.webhook:WebhookSink",
    "s3": "smartscraper.delivery.s3:S3Sink",
    "email": "smartscraper.delivery.email_:EmailSink",
    "apprise": "smartscraper.delivery.apprise_:AppriseSink",
    "mcp": "smartscraper.delivery.mcp_:McpSink",
}

_REGISTRY: dict[str, Sink] = {}


def register(sink: Sink) -> Sink:
    """Register a sink instance under its ``kind``. Also usable as a decorator
    on a class, in which case the class is instantiated with no arguments."""
    if isinstance(sink, type):
        instance = sink()
        _REGISTRY[instance.kind] = instance
        return sink
    _REGISTRY[sink.kind] = sink
    return sink


def known_kinds() -> list[str]:
    return sorted(set(BUILTIN_SINKS) | set(_REGISTRY))


def get_sink(kind: str) -> Sink:
    """Return the sink for ``kind``, importing a built-in on first use.

    Raises ``LookupError`` for an unknown kind and ``RuntimeError`` when the
    sink exists but its optional dependency is not installed.
    """
    if kind in _REGISTRY:
        return _REGISTRY[kind]
    target = BUILTIN_SINKS.get(kind)
    if target is None:
        raise LookupError(f"no delivery sink named {kind!r}; known: {', '.join(known_kinds())}")
    module_name, _, class_name = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(f"delivery sink {kind!r} is unavailable: {exc}") from exc
    instance = getattr(module, class_name)()
    _REGISTRY[kind] = instance
    return instance


class BaseSink:
    """Shared behaviour. Subclasses set ``kind`` and implement ``send``."""

    kind: str = "base"
    #: True when the orchestrator may split rows into several ``send`` calls.
    batched: bool = False

    def batch_size(self, config: dict[str, Any]) -> int | None:
        if not self.batched:
            return None
        size = config.get("batch_size", DEFAULT_BATCH_SIZE)
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = DEFAULT_BATCH_SIZE
        return size if size > 0 else None

    async def send(
        self,
        rows: list[dict[str, Any]],
        *,
        config: dict[str, Any],
        run_id: int,
        scraper: str,
        meta: dict[str, Any],
    ) -> DeliveryResult:  # pragma: no cover - abstract
        raise NotImplementedError


# --------------------------------------------------------------------------- outcome
@dataclass(slots=True)
class DeliveryOutcome:
    target_id: int
    kind: str
    status: str
    rows_sent: int = 0
    attempts: int = 0
    detail: str = ""
    error: str | None = None
    delivery_id: int | None = None
    next_attempt_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.status in (SENT, SKIPPED, HELD)


class _Retryable(Exception):
    """Internal signal that tenacity should try the sink again."""

    def __init__(self, result: DeliveryResult) -> None:
        super().__init__(result.error or result.detail or "retryable delivery failure")
        self.result = result


# --------------------------------------------------------------------------- context
def run_context(run: Run, scraper: Scraper | None = None) -> dict[str, Any]:
    """The ``$run.*`` namespace handed to mapping.py and to every sink as meta.

    ``extracted_at`` is the run's finish time when it has one, else now. It is
    the timestamp a downstream consumer should treat as "when this data was
    true", which is not the same as when the delivery happens.
    """
    finished = run.finished_at or run.created_at or datetime.now(UTC)
    return {
        "id": run.id,
        "run_id": run.id,
        "scraper_id": run.scraper_id,
        "scraper": scraper.name if scraper else "",
        "url": scraper.url if scraper else "",
        "status": run.status,
        "trigger": run.trigger,
        "script_version": run.script_version,
        "row_count": run.row_count,
        "engine_used": run.engine_used,
        "escalation_level": run.escalation_level,
        "proxy_used": run.proxy_used,
        "block_reason": run.block_reason,
        "error": run.error,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "created_at": _iso(run.created_at),
        "extracted_at": _iso(finished),
        "delivered_at": _iso(datetime.now(UTC)),
        "passed": run.status == RunStatus.PASSED,
        "provisional": run.status != RunStatus.PASSED,
        "validator": (run.validator_report or {}),
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="seconds")


def backoff_delay(attempts: int, base: float = DEFAULT_BACKOFF_S) -> float:
    """Exponential backoff for the next out-of-process attempt, capped."""
    exponent = max(0, attempts - 1)
    return float(min(base * (2 ** exponent), MAX_BACKOFF_S))


# --------------------------------------------------------------------------- orchestrator
async def deliver_run(
    run_id: int,
    *,
    session: Any = None,
    rows: list[dict[str, Any]] | None = None,
) -> list[DeliveryOutcome]:
    """Deliver one run to every enabled target. Never raises for a sink failure."""
    if session is not None:
        return await _deliver_run(session, run_id, rows)
    async with get_session() as s:
        return await _deliver_run(s, run_id, rows)


async def _deliver_run(s: Any, run_id: int, rows: list[dict[str, Any]] | None) -> list[DeliveryOutcome]:
    run = await repo.get_run(s, run_id)
    if run is None:
        raise LookupError(f"run {run_id} does not exist")
    scraper = await repo.get_scraper(s, run.scraper_id)
    targets = await repo.targets_for(s, run.scraper_id)
    if not targets:
        return []
    if rows is None:
        rows = await load_rows(s, run)
    ctx = run_context(run, scraper)
    outcomes: list[DeliveryOutcome] = []
    for target in targets:
        try:
            outcomes.append(await deliver_one(s, run, target, rows, ctx=ctx, scraper=scraper))
        except Exception as exc:  # a broken target must not stop the rest
            log.exception("delivery target %s (%s) raised", target.id, target.kind)
            outcomes.append(
                DeliveryOutcome(target_id=target.id, kind=target.kind, status=FAILED, error=repr(exc))
            )
    await s.flush()
    return outcomes


async def load_rows(s: Any, run: Run) -> list[dict[str, Any]]:
    """Records for this run, oldest first. ``repo.get_records`` returns newest first."""
    records = await repo.get_records(
        s, scraper_id=run.scraper_id, run_id=run.id, limit=1_000_000, offset=0
    )
    return [r.data for r in reversed(records)]


def _config_for(target: DeliveryTarget) -> dict[str, Any]:
    cfg = dict(target.config or {})
    cfg.setdefault("fmt", target.fmt)
    return cfg


def _held(run: Run, target: DeliveryTarget, cfg: dict[str, Any]) -> bool:
    if run.status == RunStatus.PASSED:
        return False
    if target.only_on_failure:
        return False
    return not bool(cfg.get("provisional") or cfg.get("allow_provisional"))


async def deliver_one(
    s: Any,
    run: Run,
    target: DeliveryTarget,
    rows: list[dict[str, Any]],
    *,
    ctx: dict[str, Any] | None = None,
    scraper: Scraper | None = None,
    delivery: Delivery | None = None,
) -> DeliveryOutcome:
    """Attempt one target. Writes or updates its Delivery row and returns the outcome."""
    cfg = _config_for(target)
    meta = dict(ctx or run_context(run, scraper))
    name = str(meta.get("scraper") or run.scraper_id)

    if target.only_on_failure and run.status == RunStatus.PASSED:
        return DeliveryOutcome(target_id=target.id, kind=target.kind, status=SKIPPED,
                               detail="target is only_on_failure and the run passed")

    if _held(run, target, cfg):
        row = delivery or Delivery(run_id=run.id, target_id=target.id)
        row.status = HELD
        row.last_error = f"run status {run.status}; target did not opt into provisional rows"
        s.add(row)
        await s.flush()
        return DeliveryOutcome(target_id=target.id, kind=target.kind, status=HELD,
                               detail=row.last_error, delivery_id=row.id)

    try:
        sink = get_sink(target.kind)
    except (LookupError, RuntimeError) as exc:
        row = delivery or Delivery(run_id=run.id, target_id=target.id)
        row.status = FAILED
        row.attempts = (row.attempts or 0) + 1
        row.last_error = str(exc)
        s.add(row)
        await s.flush()
        return DeliveryOutcome(target_id=target.id, kind=target.kind, status=FAILED,
                               error=str(exc), attempts=row.attempts, delivery_id=row.id)

    row = delivery or Delivery(run_id=run.id, target_id=target.id)
    s.add(row)
    prior = row.attempts or 0

    result, attempts = await _send_with_retry(sink, rows, cfg=cfg, run_id=run.id, scraper=name, meta=meta)

    row.attempts = prior + attempts
    row.rows_sent = result.rows_sent
    if result.ok:
        row.status = SENT
        row.last_error = None
        row.next_attempt_at = None
        status, nxt = SENT, None
    elif result.retryable:
        row.status = PENDING
        row.last_error = result.error or result.detail
        nxt = datetime.now(UTC) + timedelta(seconds=backoff_delay(row.attempts, _backoff(cfg)))
        row.next_attempt_at = nxt
        status = PENDING
    else:
        row.status = FAILED
        row.last_error = result.error or result.detail
        row.next_attempt_at = None
        status, nxt = FAILED, None
    await s.flush()
    return DeliveryOutcome(
        target_id=target.id, kind=target.kind, status=status, rows_sent=result.rows_sent,
        attempts=row.attempts, detail=result.detail, error=result.error,
        delivery_id=row.id, next_attempt_at=nxt,
    )


def _backoff(cfg: dict[str, Any]) -> float:
    try:
        return float(cfg.get("retry_backoff_s", DEFAULT_BACKOFF_S))
    except (TypeError, ValueError):
        return DEFAULT_BACKOFF_S


async def _send_with_retry(
    sink: Sink,
    rows: list[dict[str, Any]],
    *,
    cfg: dict[str, Any],
    run_id: int,
    scraper: str,
    meta: dict[str, Any],
) -> tuple[DeliveryResult, int]:
    """Call the sink, retrying retryable failures with exponential backoff.

    Returns the last result and the number of attempts actually made.
    """
    try:
        max_attempts = max(1, int(cfg.get("max_attempts", DEFAULT_MAX_ATTEMPTS)))
    except (TypeError, ValueError):
        max_attempts = DEFAULT_MAX_ATTEMPTS
    base = _backoff(cfg)
    attempts = 0
    last: DeliveryResult | None = None

    async def once() -> DeliveryResult:
        nonlocal attempts, last
        attempts += 1
        try:
            result = await _send_batches(sink, rows, cfg=cfg, run_id=run_id, scraper=scraper, meta=meta)
        except Exception as exc:  # a sink that raises is a retryable failure
            log.warning("sink %s raised on attempt %s: %r", getattr(sink, "kind", "?"), attempts, exc)
            result = DeliveryResult(ok=False, retryable=True, error=repr(exc),
                                    detail=f"{type(exc).__name__} from sink")
        last = result
        if not result.ok and result.retryable:
            raise _Retryable(result)
        return result

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=base, min=0, max=MAX_BACKOFF_S),
            retry=retry_if_exception_type(_Retryable),
            reraise=False,
        ):
            with attempt:
                return await once(), attempts
    except RetryError:
        pass
    assert last is not None
    return last, attempts


async def _send_batches(
    sink: Sink,
    rows: list[dict[str, Any]],
    *,
    cfg: dict[str, Any],
    run_id: int,
    scraper: str,
    meta: dict[str, Any],
) -> DeliveryResult:
    """One logical send, split into batches when the sink supports it."""
    size = sink.batch_size(cfg) if hasattr(sink, "batch_size") else None
    if not size or len(rows) <= size:
        return await sink.send(rows, config=cfg, run_id=run_id, scraper=scraper, meta=meta)

    sent = 0
    details: list[str] = []
    batches = [rows[i:i + size] for i in range(0, len(rows), size)]
    for n, batch in enumerate(batches, start=1):
        bmeta = {**meta, "batch": n, "batches": len(batches), "batch_offset": (n - 1) * size}
        result = await sink.send(batch, config=cfg, run_id=run_id, scraper=scraper, meta=bmeta)
        if not result.ok:
            # Partial success: report what got through and let the caller decide.
            return DeliveryResult(
                ok=False, rows_sent=sent, retryable=result.retryable, error=result.error,
                detail=f"batch {n}/{len(batches)} failed: {result.detail}",
            )
        sent += result.rows_sent
        details.append(result.detail)
    return DeliveryResult(ok=True, rows_sent=sent, detail=f"{len(batches)} batches: " + "; ".join(details))


# --------------------------------------------------------------------------- retries
async def retry_pending(
    *, session: Any = None, now: datetime | None = None, limit: int = 100
) -> list[DeliveryOutcome]:
    """Re-attempt every ``pending`` Delivery whose ``next_attempt_at`` has passed."""
    if session is not None:
        return await _retry_pending(session, now, limit)
    async with get_session() as s:
        return await _retry_pending(s, now, limit)


async def _retry_pending(s: Any, now: datetime | None, limit: int) -> list[DeliveryOutcome]:
    from sqlalchemy import or_, select

    now = now or datetime.now(UTC)
    rows = list((await s.scalars(
        select(Delivery)
        .where(Delivery.status == PENDING)
        .where(or_(Delivery.next_attempt_at.is_(None), Delivery.next_attempt_at <= now))
        .order_by(Delivery.next_attempt_at)
        .limit(limit)
    )).all())

    outcomes: list[DeliveryOutcome] = []
    cache: dict[int, tuple[Run, Scraper | None, list[dict[str, Any]], dict[str, Any]]] = {}
    for delivery in rows:
        target = await s.get(DeliveryTarget, delivery.target_id)
        if target is None or not target.enabled:
            delivery.status = FAILED
            delivery.last_error = "delivery target is gone or disabled"
            outcomes.append(DeliveryOutcome(target_id=delivery.target_id, kind="?", status=FAILED,
                                            error=delivery.last_error, delivery_id=delivery.id))
            continue
        if delivery.run_id not in cache:
            run = await repo.get_run(s, delivery.run_id)
            if run is None:
                delivery.status = FAILED
                delivery.last_error = "run is gone"
                continue
            scraper = await repo.get_scraper(s, run.scraper_id)
            cache[delivery.run_id] = (run, scraper, await load_rows(s, run), run_context(run, scraper))
        run, scraper, data, ctx = cache[delivery.run_id]
        max_attempts = int((target.config or {}).get("max_total_attempts", 10))
        if (delivery.attempts or 0) >= max_attempts:
            delivery.status = FAILED
            delivery.last_error = f"gave up after {delivery.attempts} attempts: {delivery.last_error}"
            outcomes.append(DeliveryOutcome(target_id=target.id, kind=target.kind, status=FAILED,
                                            attempts=delivery.attempts, error=delivery.last_error,
                                            delivery_id=delivery.id))
            continue
        try:
            outcomes.append(
                await deliver_one(s, run, target, data, ctx=ctx, scraper=scraper, delivery=delivery)
            )
        except Exception as exc:
            log.exception("retry of delivery %s raised", delivery.id)
            outcomes.append(DeliveryOutcome(target_id=target.id, kind=target.kind, status=FAILED,
                                            error=repr(exc), delivery_id=delivery.id))
    await s.flush()
    return outcomes


async def _gather(*coros: Any) -> list[Any]:  # pragma: no cover - helper kept for sinks
    return await asyncio.gather(*coros, return_exceptions=True)
