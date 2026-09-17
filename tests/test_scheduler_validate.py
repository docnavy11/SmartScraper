"""The scheduler's validation step, over the `smartscraper.pipeline` seam.

The join itself (load the script, load the rows, fetch history, judge, persist)
belongs to pipeline.py. What is tested here is the scheduler's part: that every
outcome is turned into a verdict the queue can log, and that nothing reaches
delivery with a status the hold rule would misread.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import yaml

from smartscraper import repo
from smartscraper.config import get_settings
from smartscraper.db.models import Record, Run, RunStatus, Scraper, ScriptVersion, VersionStatus
from smartscraper.db.session import create_all, get_session, init_engine
from smartscraper.scheduler.tasks import validate_run_now

SCRIPT = {
    "version": 1,
    "engine": "http",
    "steps": [
        {"op": "goto", "url": "https://example.test"},
        {"op": "extract_list", "selector": ".card", "as": "products",
         "fields": {"name": {"selector": "h3", "attr": "text"},
                    "price": {"selector": ".p", "attr": "text"}}},
        {"op": "emit", "from": "products"},
    ],
    "validation": {"min_rows": 2, "max_null_rate": {"price": 0.5}},
}


async def dispose_engine() -> None:
    from smartscraper.db import session as session_module

    if session_module._engine is not None:
        await session_module._engine.dispose()


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SS_DB_PATH", str(tmp_path / "data" / "test.db"))
    monkeypatch.setenv("SS_SCRAPERS_DIR", str(tmp_path / "scrapers"))
    get_settings.cache_clear()
    init_engine()
    await create_all()
    yield
    # Dispose while this test's event loop is still alive: an aiosqlite
    # connection left in the pool outlives the loop that opened it and its
    # worker thread then raises "Event loop is closed" into pytest.
    await dispose_engine()
    get_settings.cache_clear()


async def seed(rows: list[dict], *, status=RunStatus.PASSED, script=None, with_version=True) -> int:
    async with get_session() as s:
        scraper = Scraper(name="widgets", url="https://example.test", yaml_path="widgets.yaml")
        s.add(scraper)
        await s.flush()
        if with_version:
            s.add(ScriptVersion(scraper_id=scraper.id, version=1,
                                yaml=yaml.safe_dump(script or SCRIPT),
                                status=VersionStatus.ACTIVE))
        run = Run(scraper_id=scraper.id, script_version=1, status=status, row_count=len(rows),
                  started_at=datetime.now(UTC), finished_at=datetime.now(UTC))
        s.add(run)
        await s.flush()
        for i, row in enumerate(rows):
            s.add(Record(run_id=run.id, scraper_id=scraper.id, data=row, row_hash=f"h{i}"))
        return run.id


async def get_run(run_id: int) -> Run:
    async with get_session() as s:
        run = await repo.get_run(s, run_id)
        s.expunge(run)
        return run


async def test_good_rows_pass(db):
    run_id = await seed([{"name": "a", "price": "1"}, {"name": "b", "price": "2"}])
    assert await validate_run_now(run_id) == "passed"

    run = await get_run(run_id)
    assert run.status == RunStatus.PASSED
    assert run.validator_report["passed"] is True
    assert run.validator_report["row_count"] == 2


async def test_too_few_rows_fail_and_downgrade_the_status(db):
    run_id = await seed([{"name": "a", "price": "1"}])
    assert await validate_run_now(run_id) == "failed"

    run = await get_run(run_id)
    assert run.status == RunStatus.VALIDATION_FAILED
    assert run.validator_report["passed"] is False
    assert "min_rows" in run.validator_report["summary"]


async def test_the_report_carries_a_summary(db):
    run_id = await seed([{"name": "a", "price": "1"}])
    await validate_run_now(run_id)
    assert (await get_run(run_id)).validator_report["summary"]


async def test_metrics_are_written_per_field(db):
    run_id = await seed([{"name": "a", "price": "1"}, {"name": "b", "price": None}])
    await validate_run_now(run_id)
    async with get_session() as s:
        metrics = {m.field: m for m in await repo.run_metrics(s, run_id)}
    assert set(metrics) >= {"name", "price"}
    assert metrics["price"].null_rate == 0.5


async def test_metrics_are_replaced_not_duplicated_on_a_second_pass(db):
    run_id = await seed([{"name": "a", "price": "1"}, {"name": "b", "price": "2"}])
    await validate_run_now(run_id)
    await validate_run_now(run_id)
    async with get_session() as s:
        metrics = await repo.run_metrics(s, run_id)
    assert len(metrics) == len({m.field for m in metrics})


async def test_an_errored_run_keeps_its_own_status(db):
    """The validator may still report on it, but `error` is the more useful answer."""
    run_id = await seed([], status=RunStatus.ERROR)
    await validate_run_now(run_id)
    assert (await get_run(run_id)).status == RunStatus.ERROR


async def test_a_blocked_run_keeps_its_own_status(db):
    run_id = await seed([], status=RunStatus.BLOCKED)
    await validate_run_now(run_id)
    assert (await get_run(run_id)).status == RunStatus.BLOCKED


@pytest.mark.parametrize("status", [RunStatus.ERROR, RunStatus.BLOCKED, RunStatus.CANCELLED])
async def test_an_unjudgeable_run_is_never_promoted_to_passed(db, status):
    """Good-looking rows must not turn a blocked run into a clean one."""
    run_id = await seed([{"name": "a", "price": "1"}, {"name": "b", "price": "2"}], status=status)
    await validate_run_now(run_id)
    assert (await get_run(run_id)).status == status


async def test_a_missing_run_is_reported_not_raised(db):
    assert await validate_run_now(404) == "missing"


async def test_a_run_with_no_script_anywhere_is_skipped(db):
    run_id = await seed([{"name": "a"}], with_version=False)
    assert await validate_run_now(run_id) == "skipped"


async def test_a_run_with_no_recoverable_script_is_skipped_not_failed(db):
    """Unjudgeable is not the same as failed, and must not be reported as one."""
    run_id = await seed([{"name": "a", "price": "1"}, {"name": "b", "price": "2"}],
                        with_version=False)

    assert await validate_run_now(run_id) == "skipped"
    run = await get_run(run_id)
    assert run.status == RunStatus.PASSED
    assert run.validator_report is None


async def test_an_unparseable_script_version_does_not_kill_the_worker(db):
    """The YAML in the version row no longer parses and no file can stand in."""
    run_id = await seed([{"name": "a"}])
    async with get_session() as s:
        version = await repo.active_version(s, (await repo.get_run(s, run_id)).scraper_id)
        version.yaml = "steps: [not a step]"

    assert await validate_run_now(run_id) == "skipped"
    assert (await get_run(run_id)).status == RunStatus.PASSED


async def test_a_failed_run_is_left_in_the_state_the_hold_rule_reads(db):
    """Delivery holds on exactly this string; nothing else sets it."""
    from smartscraper.db.models import DeliveryTarget
    from smartscraper.delivery.base import _held

    run_id = await seed([{"name": "a", "price": "1"}])
    await validate_run_now(run_id)
    run = await get_run(run_id)

    assert run.status == RunStatus.VALIDATION_FAILED
    assert _held(run, DeliveryTarget(kind="file", config={}, only_on_failure=False), {}) is True
    assert _held(run, DeliveryTarget(kind="file", config={}, only_on_failure=False),
                 {"provisional": True}) is False
