"""Huey wiring: the queue instance, the task entry points, the consumer.

The task entry points are synchronous because Huey's workers are threads: each
one opens its own event loop with ``asyncio.run``. These tests therefore drive
them from synchronous test functions, exactly as the consumer would, and
re-initialise the SQLAlchemy engine before each call so its pool belongs to the
loop that is about to use it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from smartscraper.config import get_settings
from smartscraper.db.models import DeliveryTarget, Record, Run, RunStatus, Scraper
from smartscraper.db.session import create_all, get_session
from smartscraper.scheduler import huey_app, tasks, worker

ROWS = [{"name": "Widget"}, {"name": "Gadget"}]


def rebind() -> None:
    """Point the session factory at a fresh, unpooled engine.

    Each ``asyncio.run`` is a new event loop, and an aiosqlite connection is
    bound to the loop that opened it. NullPool means no connection outlives the
    loop that created it, which is also how a real Huey worker thread behaves.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from smartscraper.db import session as session_module

    settings = get_settings()
    settings.ensure_dirs()
    engine = create_async_engine(settings.db_url, poolclass=NullPool, future=True)
    session_module._engine = engine
    session_module._factory = async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Synchronous: sets up the schema, then hands back the rebind helper."""
    monkeypatch.setenv("SS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SS_DB_PATH", str(tmp_path / "data" / "test.db"))
    get_settings.cache_clear()

    async def setup():
        rebind()
        await create_all()

    asyncio.run(setup())
    yield rebind  # call it before each task, which opens its own loop
    get_settings.cache_clear()


def seed_run(out_path) -> int:
    async def _seed() -> int:
        rebind()
        async with get_session() as s:
            scraper = Scraper(name="widgets", url="https://example.test", yaml_path="w.yaml")
            s.add(scraper)
            await s.flush()
            run = Run(scraper_id=scraper.id, status=RunStatus.PASSED, row_count=2,
                      finished_at=datetime.now(UTC))
            s.add(run)
            await s.flush()
            for i, row in enumerate(ROWS):
                s.add(Record(run_id=run.id, scraper_id=scraper.id, data=row, row_hash=f"h{i}"))
            s.add(DeliveryTarget(scraper_id=scraper.id, kind="file",
                                 config={"fmt": "jsonl", "path": str(out_path)}))
            return run.id

    return asyncio.run(_seed())


def test_the_queue_is_a_separate_sqlite_file_from_the_metadata_db():
    settings = get_settings()
    assert huey_app.queue_path() == settings.data_dir / "huey.db"
    assert huey_app.queue_path() != settings.db_path


def test_immediate_mode_can_be_switched_on(tmp_path):
    assert huey_app.make_huey(path=tmp_path / "q.db", immediate=True).immediate is True


def test_enable_stats_never_raises_when_the_extra_is_absent():
    """Task history is a nicety; a missing optional dependency must not stop the worker."""
    assert huey_app.enable_stats() in (True, False)


def test_the_tasks_are_registered_on_the_queue():
    names = set(huey_app.huey._registry._registry)
    for task in ("run_scraper", "deliver", "retry_deliveries", "validate_run",
                 "build_scraper", "repair_scraper"):
        assert any(name.endswith(task) for name in names), task


def test_the_consumer_is_built_with_periodic_tasks_on():
    consumer = worker.build_consumer(workers=2)
    assert consumer.workers == 2
    assert consumer.periodic is True


def test_the_deliver_task_writes_a_file(db, tmp_path):
    out = tmp_path / "delivered.jsonl"
    run_id = seed_run(out)

    db()  # rebind the engine to the loop the task is about to open
    outcomes = tasks.deliver.call_local(run_id)

    assert [o["status"] for o in outcomes] == ["sent"]
    assert [o["rows_sent"] for o in outcomes] == [2]
    assert len(out.read_text().strip().splitlines()) == 2


def test_retry_deliveries_is_a_no_op_when_nothing_is_parked(db):
    db()
    assert tasks.retry_deliveries.call_local() == []


def test_build_scraper_reaches_the_agents_entry_point(db, monkeypatch):
    """The task resolves smartscraper.agents.jobs.build_scraper and calls it by keyword."""
    from smartscraper.agents import jobs

    seen = {}

    async def fake(url, goal, name=None, output_schema=None):
        seen.update(url=url, goal=goal, name=name, output_schema=output_schema)
        return {"built": True}

    monkeypatch.setattr(jobs, "build_scraper", fake)
    db()
    result = tasks.build_scraper.call_local("https://example.test", "get the widgets", "widgets")

    assert result == {"ok": True, "result": {"built": True}}
    assert seen == {"url": "https://example.test", "goal": "get the widgets",
                    "name": "widgets", "output_schema": None}


def test_repair_scraper_reaches_the_agents_entry_point(db, monkeypatch):
    from smartscraper.agents import jobs

    seen = {}

    async def fake(scraper_id, run_id=None):
        seen.update(scraper_id=scraper_id, run_id=run_id)
        return {"repaired": True}

    monkeypatch.setattr(jobs, "repair_scraper", fake)
    db()
    result = tasks.repair_scraper.call_local(7, 42)

    assert result == {"ok": True, "result": {"repaired": True}}
    assert seen == {"scraper_id": 7, "run_id": 42}


def test_an_agent_that_raises_is_reported_not_propagated(db, monkeypatch):
    """A failed build must not kill the worker thread."""
    from smartscraper.agents import jobs

    async def boom(url, goal, name=None, output_schema=None):
        raise RuntimeError("the browser would not start")

    monkeypatch.setattr(jobs, "build_scraper", boom)
    db()
    result = tasks.build_scraper.call_local("https://example.test", "get the widgets")

    assert result["ok"] is False
    assert "the browser would not start" in result["error"]


def test_validating_a_run_that_does_not_exist_is_reported_not_raised(db):
    db()
    assert asyncio.run(tasks.validate_run_now(404)) == "missing"


def test_run_scraper_task_runs_a_child_process(db, monkeypatch, tmp_path):
    """The Huey entry point end to end, minus the real runner."""
    import json
    import sys

    async def _seed() -> int:
        rebind()
        async with get_session() as s:
            scraper = Scraper(name="widgets", url="https://example.test", yaml_path="w.yaml")
            s.add(scraper)
            await s.flush()
            return scraper.id

    scraper_id = asyncio.run(_seed())
    line = json.dumps({"tag": "result", "status": "passed", "row_count": 5})
    monkeypatch.setenv("SS_RUNNER_CMD", json.dumps([sys.executable, "-c", f"print({line!r})"]))

    db()
    run_id = tasks.run_scraper.call_local(scraper_id, "manual")

    async def _check() -> tuple[str, int]:
        rebind()
        async with get_session() as s:
            run = await s.get(Run, run_id)
            return run.status, run.row_count

    assert asyncio.run(_check()) == (RunStatus.PASSED, 5)


def test_the_full_pipeline_run_validate_deliver(db, monkeypatch, tmp_path):
    """One pass through the three tasks a scheduled scrape goes through."""
    import json
    import sys

    import yaml

    from smartscraper.db.models import ScriptVersion, VersionStatus

    script = {
        "version": 1,
        "engine": "http",
        "steps": [
            {"op": "goto", "url": "https://example.test"},
            {"op": "extract_list", "selector": ".card", "as": "p",
             "fields": {"name": {"selector": "h3", "attr": "text"}}},
            {"op": "emit", "from": "p"},
        ],
        "validation": {"min_rows": 2},
    }
    out = tmp_path / "pipeline.jsonl"

    async def _seed() -> int:
        rebind()
        async with get_session() as s:
            scraper = Scraper(name="widgets", url="https://example.test", yaml_path="widgets.yaml")
            s.add(scraper)
            await s.flush()
            s.add(ScriptVersion(scraper_id=scraper.id, version=1, yaml=yaml.safe_dump(script),
                                status=VersionStatus.ACTIVE))
            s.add(DeliveryTarget(scraper_id=scraper.id, kind="file",
                                 config={"fmt": "jsonl", "path": str(out)}))
            return scraper.id

    scraper_id = asyncio.run(_seed())
    outcome = json.dumps({"tag": "outcome", "outcome": {
        "rows": [{"name": "Widget"}, {"name": "Gadget"}], "row_count": 2,
        "engine_used": "http", "escalation_level": 1, "error": None}})
    monkeypatch.setenv("SS_RUNNER_CMD", json.dumps([sys.executable, "-c", f"print({outcome!r})"]))

    db()
    run_id = tasks.run_scraper.call_local(scraper_id, "schedule")
    db()
    assert tasks.validate_run.call_local(run_id) == "passed"
    db()
    assert [o["status"] for o in tasks.deliver.call_local(run_id)] == ["sent"]

    assert [json.loads(line)["name"] for line in out.read_text().splitlines()] == ["Widget", "Gadget"]

    async def _status() -> str:
        rebind()
        async with get_session() as s:
            return (await s.get(Run, run_id)).status

    assert asyncio.run(_status()) == RunStatus.PASSED


def test_a_validation_failure_holds_delivery(db, monkeypatch, tmp_path):
    """The whole point of validation: bad data does not reach a clean target."""
    import json
    import sys

    import yaml

    from smartscraper.db.models import ScriptVersion, VersionStatus

    script = {
        "version": 1,
        "engine": "http",
        "steps": [
            {"op": "goto", "url": "https://example.test"},
            {"op": "extract_list", "selector": ".card", "as": "p",
             "fields": {"name": {"selector": "h3", "attr": "text"}}},
            {"op": "emit", "from": "p"},
        ],
        "validation": {"min_rows": 10},
    }
    out = tmp_path / "held.jsonl"

    async def _seed() -> int:
        rebind()
        async with get_session() as s:
            scraper = Scraper(name="widgets", url="https://example.test", yaml_path="widgets.yaml")
            s.add(scraper)
            await s.flush()
            s.add(ScriptVersion(scraper_id=scraper.id, version=1, yaml=yaml.safe_dump(script),
                                status=VersionStatus.ACTIVE))
            s.add(DeliveryTarget(scraper_id=scraper.id, kind="file",
                                 config={"fmt": "jsonl", "path": str(out)}))
            return scraper.id

    scraper_id = asyncio.run(_seed())
    outcome = json.dumps({"tag": "outcome", "outcome": {
        "rows": [{"name": "Widget"}], "row_count": 1, "error": None}})
    monkeypatch.setenv("SS_RUNNER_CMD", json.dumps([sys.executable, "-c", f"print({outcome!r})"]))

    db()
    run_id = tasks.run_scraper.call_local(scraper_id, "schedule")
    db()
    assert tasks.validate_run.call_local(run_id) == "failed"
    db()
    assert [o["status"] for o in tasks.deliver.call_local(run_id)] == ["held"]
    assert not out.exists()
