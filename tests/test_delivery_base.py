"""deliver_run: holds, isolation between targets, Delivery rows, retries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from smartscraper import repo
from smartscraper.config import get_settings
from smartscraper.contracts import DeliveryResult
from smartscraper.db.models import Delivery, DeliveryTarget, Record, Run, RunStatus, Scraper
from smartscraper.db.session import create_all, get_session, init_engine
from smartscraper.delivery import base
from smartscraper.delivery.base import (
    FAILED,
    HELD,
    PENDING,
    SENT,
    SKIPPED,
    BaseSink,
    backoff_delay,
    deliver_run,
    get_sink,
    known_kinds,
    register,
    retry_pending,
    run_context,
)

ROWS = [{"name": "Widget", "price": 9.5}, {"name": "Gadget", "price": 12.0}]


async def dispose_engine() -> None:
    from smartscraper.db import session as session_module

    if session_module._engine is not None:
        await session_module._engine.dispose()


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SS_DB_PATH", str(tmp_path / "data" / "test.db"))
    get_settings.cache_clear()
    init_engine()
    await create_all()
    yield
    # Dispose while this test's event loop is still alive: an aiosqlite
    # connection left in the pool outlives the loop that opened it and its
    # worker thread then raises "Event loop is closed" into pytest.
    await dispose_engine()
    get_settings.cache_clear()


class _Recorder(BaseSink):
    """A sink under test control: returns queued results and logs its calls."""

    kind = "recorder"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.results: list[DeliveryResult] = []
        self.raises: Exception | None = None

    async def send(self, rows, *, config, run_id, scraper, meta):
        self.calls.append(list(rows))
        if self.raises is not None:
            raise self.raises
        if self.results:
            return self.results.pop(0)
        return DeliveryResult(ok=True, rows_sent=len(rows), detail="recorded")


@pytest.fixture
def recorder():
    sink = _Recorder()
    register(sink)
    yield sink
    base._REGISTRY.pop("recorder", None)


async def make_run(status: str = RunStatus.PASSED, *, rows=ROWS, name="widgets") -> tuple[int, int]:
    async with get_session() as s:
        scraper = Scraper(name=name, url="https://example.test", yaml_path=f"{name}.yaml")
        s.add(scraper)
        await s.flush()
        run = Run(scraper_id=scraper.id, status=status, row_count=len(rows),
                  started_at=datetime.now(UTC), finished_at=datetime.now(UTC))
        s.add(run)
        await s.flush()
        for i, row in enumerate(rows):
            s.add(Record(run_id=run.id, scraper_id=scraper.id, data=row, row_hash=f"h{i}"))
        return run.id, scraper.id


async def add_target(scraper_id: int | None, kind: str, config: dict, **kw) -> int:
    async with get_session() as s:
        target = DeliveryTarget(scraper_id=scraper_id, kind=kind, config=config, **kw)
        s.add(target)
        await s.flush()
        return target.id


async def deliveries(run_id: int) -> list[Delivery]:
    async with get_session() as s:
        return await repo.deliveries_for_run(s, run_id)


# --------------------------------------------------------------------------- registry
def test_registry_lists_every_builtin_kind():
    assert set(known_kinds()) >= {"file", "webhook", "s3", "email", "apprise", "mcp"}


def test_unknown_kind_raises_lookup_error():
    with pytest.raises(LookupError):
        get_sink("carrier-pigeon")


def test_each_builtin_kind_resolves_to_a_sink():
    for kind in ("file", "webhook", "s3", "email", "apprise", "mcp"):
        assert get_sink(kind).kind == kind


def test_backoff_grows_exponentially_and_is_capped():
    assert backoff_delay(1, base=2) == 2
    assert backoff_delay(2, base=2) == 4
    assert backoff_delay(3, base=2) == 8
    assert backoff_delay(40, base=2) == base.MAX_BACKOFF_S


# --------------------------------------------------------------------------- happy path
async def test_a_passing_run_is_delivered(db, recorder, tmp_path):
    run_id, scraper_id = await make_run()
    await add_target(scraper_id, "recorder", {})

    outcomes = await deliver_run(run_id)
    assert [o.status for o in outcomes] == [SENT]
    assert recorder.calls == [ROWS]

    rows = await deliveries(run_id)
    assert len(rows) == 1
    assert rows[0].status == SENT
    assert rows[0].rows_sent == 2
    assert rows[0].attempts == 1


async def test_records_arrive_oldest_first(db, recorder):
    rows = [{"n": i} for i in range(5)]
    run_id, scraper_id = await make_run(rows=rows)
    await add_target(scraper_id, "recorder", {})
    await deliver_run(run_id)
    assert recorder.calls[0] == rows


async def test_a_global_target_covers_every_scraper(db, recorder):
    run_id, _ = await make_run()
    await add_target(None, "recorder", {})
    outcomes = await deliver_run(run_id)
    assert [o.status for o in outcomes] == [SENT]


async def test_a_disabled_target_is_not_attempted(db, recorder):
    run_id, scraper_id = await make_run()
    await add_target(scraper_id, "recorder", {}, enabled=False)
    assert await deliver_run(run_id) == []
    assert recorder.calls == []


async def test_run_context_exposes_extracted_at(db):
    run_id, _ = await make_run()
    async with get_session() as s:
        run = await repo.get_run(s, run_id)
        ctx = run_context(run, await repo.get_scraper(s, run.scraper_id))
    assert ctx["extracted_at"]
    assert ctx["run_id"] == run_id
    assert ctx["scraper"] == "widgets"
    assert ctx["provisional"] is False


# --------------------------------------------------------------------------- holds
async def test_a_validation_failure_holds_delivery(db, recorder):
    run_id, scraper_id = await make_run(RunStatus.VALIDATION_FAILED)
    await add_target(scraper_id, "recorder", {})

    outcomes = await deliver_run(run_id)
    assert [o.status for o in outcomes] == [HELD]
    assert recorder.calls == []
    assert (await deliveries(run_id))[0].status == HELD


async def test_a_target_can_opt_into_provisional_rows(db, recorder):
    run_id, scraper_id = await make_run(RunStatus.VALIDATION_FAILED)
    await add_target(scraper_id, "recorder", {"provisional": True})

    outcomes = await deliver_run(run_id)
    assert [o.status for o in outcomes] == [SENT]
    assert recorder.calls == [ROWS]


async def test_an_errored_run_is_also_held(db, recorder):
    run_id, scraper_id = await make_run(RunStatus.ERROR)
    await add_target(scraper_id, "recorder", {})
    assert [o.status for o in await deliver_run(run_id)] == [HELD]


async def test_only_on_failure_targets_skip_a_passing_run(db, recorder):
    run_id, scraper_id = await make_run(RunStatus.PASSED)
    await add_target(scraper_id, "recorder", {}, only_on_failure=True)

    outcomes = await deliver_run(run_id)
    assert [o.status for o in outcomes] == [SKIPPED]
    assert recorder.calls == []
    assert await deliveries(run_id) == []


async def test_only_on_failure_targets_are_not_held_on_a_failure(db, recorder):
    """An alert target exists for failures; holding it would silence the alarm."""
    run_id, scraper_id = await make_run(RunStatus.VALIDATION_FAILED)
    await add_target(scraper_id, "recorder", {}, only_on_failure=True)

    outcomes = await deliver_run(run_id)
    assert [o.status for o in outcomes] == [SENT]
    assert recorder.calls == [ROWS]


async def test_the_meta_passed_to_a_sink_flags_provisional_rows(db, recorder):
    run_id, scraper_id = await make_run(RunStatus.VALIDATION_FAILED)
    await add_target(scraper_id, "recorder", {"provisional": True})
    captured = {}

    async def send(rows, *, config, run_id, scraper, meta):
        captured.update(meta)
        return DeliveryResult(ok=True, rows_sent=len(rows))

    recorder.send = send
    await deliver_run(run_id)
    assert captured["provisional"] is True
    assert captured["status"] == RunStatus.VALIDATION_FAILED


# --------------------------------------------------------------------------- isolation
async def test_one_failing_target_does_not_block_the_others(db, recorder, tmp_path):
    run_id, scraper_id = await make_run()
    out = tmp_path / "out.jsonl"
    recorder.raises = RuntimeError("sink exploded")
    await add_target(scraper_id, "recorder", {"max_attempts": 1, "retry_backoff_s": 0})
    await add_target(scraper_id, "file", {"fmt": "jsonl", "path": str(out)})

    outcomes = await deliver_run(run_id)
    by_kind = {o.kind: o for o in outcomes}
    assert by_kind["recorder"].status == PENDING  # a raising sink is retryable
    assert by_kind["file"].status == SENT
    assert [json.loads(line) for line in out.read_text().splitlines()] == ROWS


async def test_an_unknown_kind_fails_that_target_only(db, recorder, tmp_path):
    run_id, scraper_id = await make_run()
    out = tmp_path / "out.jsonl"
    await add_target(scraper_id, "nonsense", {})
    await add_target(scraper_id, "file", {"fmt": "jsonl", "path": str(out)})

    by_kind = {o.kind: o for o in await deliver_run(run_id)}
    assert by_kind["nonsense"].status == FAILED
    assert "nonsense" in (by_kind["nonsense"].error or "")
    assert by_kind["file"].status == SENT
    assert out.exists()


async def test_a_missing_run_raises(db):
    with pytest.raises(LookupError):
        await deliver_run(999)


async def test_no_targets_is_not_an_error(db):
    run_id, _ = await make_run()
    assert await deliver_run(run_id) == []


# --------------------------------------------------------------------------- retries
async def test_a_retryable_failure_is_parked_with_a_next_attempt(db, recorder):
    run_id, scraper_id = await make_run()
    recorder.results = [
        DeliveryResult(ok=False, retryable=True, error="boom"),
        DeliveryResult(ok=False, retryable=True, error="boom"),
    ]
    await add_target(scraper_id, "recorder", {"max_attempts": 2, "retry_backoff_s": 0})

    outcomes = await deliver_run(run_id)
    assert outcomes[0].status == PENDING
    assert outcomes[0].attempts == 2
    row = (await deliveries(run_id))[0]
    assert row.status == PENDING
    assert row.next_attempt_at is not None
    assert row.last_error == "boom"


async def test_a_terminal_failure_is_not_retried(db, recorder):
    run_id, scraper_id = await make_run()
    recorder.results = [DeliveryResult(ok=False, retryable=False, error="bad payload")]
    await add_target(scraper_id, "recorder", {"max_attempts": 3, "retry_backoff_s": 0})

    outcomes = await deliver_run(run_id)
    assert outcomes[0].status == FAILED
    assert outcomes[0].attempts == 1
    assert len(recorder.calls) == 1


async def test_an_in_call_retry_can_succeed(db, recorder):
    run_id, scraper_id = await make_run()
    recorder.results = [DeliveryResult(ok=False, retryable=True, error="flaky")]
    await add_target(scraper_id, "recorder", {"max_attempts": 3, "retry_backoff_s": 0})

    outcomes = await deliver_run(run_id)
    assert outcomes[0].status == SENT
    assert outcomes[0].attempts == 2
    assert len(recorder.calls) == 2


async def test_retry_pending_reattempts_a_parked_delivery(db, recorder):
    run_id, scraper_id = await make_run()
    recorder.results = [DeliveryResult(ok=False, retryable=True, error="boom")]
    await add_target(scraper_id, "recorder", {"max_attempts": 1, "retry_backoff_s": 0})
    assert (await deliver_run(run_id))[0].status == PENDING

    outcomes = await retry_pending(now=datetime.now(UTC) + timedelta(hours=1))
    assert [o.status for o in outcomes] == [SENT]
    rows = await deliveries(run_id)
    assert len(rows) == 1  # the same row is updated, not duplicated
    assert rows[0].status == SENT
    assert rows[0].attempts == 2


async def test_retry_pending_leaves_a_delivery_whose_backoff_has_not_elapsed(db, recorder):
    run_id, scraper_id = await make_run()
    recorder.results = [DeliveryResult(ok=False, retryable=True, error="boom")]
    await add_target(scraper_id, "recorder", {"max_attempts": 1, "retry_backoff_s": 600})
    await deliver_run(run_id)

    assert await retry_pending(now=datetime.now(UTC)) == []
    assert (await deliveries(run_id))[0].status == PENDING


async def test_retry_pending_gives_up_after_max_total_attempts(db, recorder):
    run_id, scraper_id = await make_run()
    recorder.results = [DeliveryResult(ok=False, retryable=True, error="boom")]
    await add_target(scraper_id, "recorder",
                     {"max_attempts": 1, "retry_backoff_s": 0, "max_total_attempts": 1})
    await deliver_run(run_id)

    outcomes = await retry_pending(now=datetime.now(UTC) + timedelta(hours=1))
    assert [o.status for o in outcomes] == [FAILED]
    assert "gave up" in ((await deliveries(run_id))[0].last_error or "")


async def test_a_sent_delivery_is_not_retried(db, recorder):
    run_id, scraper_id = await make_run()
    await add_target(scraper_id, "recorder", {})
    await deliver_run(run_id)
    assert await retry_pending(now=datetime.now(UTC) + timedelta(hours=1)) == []


# --------------------------------------------------------------------------- batching
async def test_batched_sinks_split_and_unbatched_sinks_do_not(db):
    run_id, scraper_id = await make_run(rows=[{"n": i} for i in range(5)])

    class Batched(_Recorder):
        kind = "batched"
        batched = True

    sink = Batched()
    register(sink)
    try:
        await add_target(scraper_id, "batched", {"batch_size": 2})
        outcomes = await deliver_run(run_id)
        assert outcomes[0].status == SENT
        assert outcomes[0].rows_sent == 5
        assert [len(c) for c in sink.calls] == [2, 2, 1]
    finally:
        base._REGISTRY.pop("batched", None)
