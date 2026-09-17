"""cron.py: expression parsing, task registration, and the two fire-time gates."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smartscraper import repo
from smartscraper.config import get_settings
from smartscraper.db.models import LlmUsage, Run, RunStatus, Scraper
from smartscraper.db.session import create_all, get_session, init_engine
from smartscraper.scheduler import cron
from smartscraper.scheduler.cron import (
    CronError,
    active_run_count,
    cron_matches,
    load_schedules,
    parse_cron,
    register_scrapers,
    registered,
    should_fire,
    unregister_all,
)


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


@pytest.fixture(autouse=True)
def clean_registry():
    unregister_all()
    yield
    unregister_all()


async def make_scraper(name="widgets", **kw) -> int:
    async with get_session() as s:
        scraper = Scraper(name=name, url="https://example.test", yaml_path=f"{name}.yaml", **kw)
        s.add(scraper)
        await s.flush()
        return scraper.id


# --------------------------------------------------------------------------- parsing
@pytest.mark.parametrize("expression,when,expected", [
    ("* * * * *", datetime(2026, 9, 17, 10, 30), True),
    ("*/15 * * * *", datetime(2026, 9, 17, 10, 30), True),
    ("*/15 * * * *", datetime(2026, 9, 17, 10, 31), False),
    ("0 6 * * *", datetime(2026, 9, 17, 6, 0), True),
    ("0 6 * * *", datetime(2026, 9, 17, 7, 0), False),
    ("30 9 * * 1-5", datetime(2026, 9, 17, 9, 30), True),   # a Thursday
    ("30 9 * * 0", datetime(2026, 9, 17, 9, 30), False),
    ("0 0 1 * *", datetime(2026, 9, 1, 0, 0), True),
    ("0 0 1 * *", datetime(2026, 9, 2, 0, 0), False),
])
def test_cron_expressions(expression, when, expected):
    assert cron_matches(expression, when) is expected


@pytest.mark.parametrize("alias,when,expected", [
    ("@hourly", datetime(2026, 9, 17, 10, 0), True),
    ("@hourly", datetime(2026, 9, 17, 10, 1), False),
    ("@daily", datetime(2026, 9, 17, 0, 0), True),
    ("@weekly", datetime(2026, 9, 20, 0, 0), True),   # a Sunday
    ("@monthly", datetime(2026, 9, 1, 0, 0), True),
])
def test_aliases(alias, when, expected):
    assert cron_matches(alias, when) is expected


@pytest.mark.parametrize("expression", ["", "not a cron", "* * *", "* * * * * *", "99 * * * *"])
def test_bad_expressions_raise_cron_error(expression):
    with pytest.raises(CronError):
        parse_cron(expression)


def test_the_error_names_the_expression():
    with pytest.raises(CronError, match="banana"):
        parse_cron("banana")


# --------------------------------------------------------------------------- registration
def test_register_scrapers_registers_one_task_per_schedule():
    assert register_scrapers([(1, "a", "*/5 * * * *"), (2, "b", "0 6 * * *")]) == {
        "cron_1_a": "*/5 * * * *",
        "cron_2_b": "0 6 * * *",
    }


def test_a_removed_scraper_loses_its_task():
    register_scrapers([(1, "a", "*/5 * * * *"), (2, "b", "0 6 * * *")])
    assert register_scrapers([(1, "a", "*/5 * * * *")]) == {"cron_1_a": "*/5 * * * *"}


def test_a_changed_expression_replaces_the_task():
    register_scrapers([(1, "a", "*/5 * * * *")])
    register_scrapers([(1, "a", "0 6 * * *")])
    assert registered() == {"cron_1_a": "0 6 * * *"}
    assert len(cron._registered) == 1


def test_one_bad_expression_does_not_stop_the_others():
    result = register_scrapers([(1, "good", "*/5 * * * *"), (2, "bad", "nonsense")])
    assert result == {"cron_1_good": "*/5 * * * *"}


def test_task_names_survive_awkward_scraper_names():
    result = register_scrapers([(3, "my site/v2", "@daily")])
    assert list(result) == ["cron_3_my_site_v2"]


async def test_load_schedules_skips_disabled_and_unscheduled_scrapers(db):
    await make_scraper("scheduled", schedule="@daily")
    await make_scraper("paused", schedule="@daily", enabled=False)
    await make_scraper("manual-only")
    assert [name for _, name, _ in await load_schedules()] == ["scheduled"]


# --------------------------------------------------------------------------- gates
async def test_an_enabled_idle_scraper_fires(db):
    scraper_id = await make_scraper(schedule="@daily")
    async with get_session() as s:
        ok, reason = await should_fire(s, await repo.get_scraper(s, scraper_id))
    assert ok, reason


async def test_a_disabled_scraper_does_not_fire(db):
    scraper_id = await make_scraper(schedule="@daily", enabled=False)
    async with get_session() as s:
        ok, reason = await should_fire(s, await repo.get_scraper(s, scraper_id))
    assert not ok
    assert "disabled" in reason


async def test_a_scraper_already_running_does_not_fire_again(db):
    scraper_id = await make_scraper(schedule="@daily")
    async with get_session() as s:
        s.add(Run(scraper_id=scraper_id, status=RunStatus.RUNNING))
    async with get_session() as s:
        ok, reason = await should_fire(s, await repo.get_scraper(s, scraper_id))
    assert not ok
    assert "already queued or running" in reason


async def test_max_concurrent_runs_is_honoured(db, monkeypatch):
    monkeypatch.setenv("SS_MAX_CONCURRENT_RUNS", "2")
    get_settings.cache_clear()
    busy = await make_scraper("busy-a")
    busy_b = await make_scraper("busy-b")
    idle = await make_scraper("idle", schedule="@daily")
    async with get_session() as s:
        s.add(Run(scraper_id=busy, status=RunStatus.RUNNING))
        s.add(Run(scraper_id=busy_b, status=RunStatus.QUEUED))
    async with get_session() as s:
        assert await active_run_count(s) == 2
        ok, reason = await should_fire(s, await repo.get_scraper(s, idle))
    assert not ok
    assert "max_concurrent_runs" in reason


async def test_a_finished_run_does_not_occupy_a_slot(db, monkeypatch):
    monkeypatch.setenv("SS_MAX_CONCURRENT_RUNS", "1")
    get_settings.cache_clear()
    other = await make_scraper("other")
    idle = await make_scraper("idle", schedule="@daily")
    async with get_session() as s:
        s.add(Run(scraper_id=other, status=RunStatus.PASSED))
    async with get_session() as s:
        ok, _ = await should_fire(s, await repo.get_scraper(s, idle))
    assert ok


async def test_an_exhausted_budget_stops_the_scraper(db, monkeypatch):
    monkeypatch.setenv("SS_STOP_AT_BUDGET", "true")
    get_settings.cache_clear()
    scraper_id = await make_scraper(schedule="@daily", budget_usd_month=5.0)
    async with get_session() as s:
        s.add(LlmUsage(scraper_id=scraper_id, agent="repair", model="claude-opus-5",
                       cost_usd=5.5, created_at=datetime.now(UTC)))
    async with get_session() as s:
        ok, reason = await should_fire(s, await repo.get_scraper(s, scraper_id))
    assert not ok
    assert "budget exhausted" in reason


async def test_spend_under_the_budget_still_fires(db, monkeypatch):
    monkeypatch.setenv("SS_STOP_AT_BUDGET", "true")
    get_settings.cache_clear()
    scraper_id = await make_scraper(schedule="@daily", budget_usd_month=5.0)
    async with get_session() as s:
        s.add(LlmUsage(scraper_id=scraper_id, agent="repair", model="claude-opus-5",
                       cost_usd=1.0, created_at=datetime.now(UTC)))
    async with get_session() as s:
        ok, _ = await should_fire(s, await repo.get_scraper(s, scraper_id))
    assert ok


async def test_stop_at_budget_off_ignores_the_budget(db, monkeypatch):
    monkeypatch.setenv("SS_STOP_AT_BUDGET", "false")
    get_settings.cache_clear()
    scraper_id = await make_scraper(schedule="@daily", budget_usd_month=1.0)
    async with get_session() as s:
        s.add(LlmUsage(scraper_id=scraper_id, agent="repair", model="claude-opus-5",
                       cost_usd=99.0, created_at=datetime.now(UTC)))
    async with get_session() as s:
        ok, _ = await should_fire(s, await repo.get_scraper(s, scraper_id))
    assert ok


async def test_another_scrapers_spend_does_not_count(db, monkeypatch):
    monkeypatch.setenv("SS_STOP_AT_BUDGET", "true")
    get_settings.cache_clear()
    mine = await make_scraper("mine", schedule="@daily", budget_usd_month=5.0)
    theirs = await make_scraper("theirs", budget_usd_month=5.0)
    async with get_session() as s:
        s.add(LlmUsage(scraper_id=theirs, agent="builder", model="claude-opus-5",
                       cost_usd=99.0, created_at=datetime.now(UTC)))
    async with get_session() as s:
        ok, _ = await should_fire(s, await repo.get_scraper(s, mine))
    assert ok


async def test_fire_skips_a_gated_scraper_without_enqueueing(db, monkeypatch):
    scraper_id = await make_scraper(schedule="@daily", enabled=False)
    calls = []
    monkeypatch.setattr(cron, "run_scraper", lambda *a: calls.append(a))
    assert await cron.fire(scraper_id) is None
    assert calls == []


async def test_fire_enqueues_an_eligible_scraper(db, monkeypatch):
    scraper_id = await make_scraper(schedule="@daily")
    calls = []
    monkeypatch.setattr(cron, "run_scraper", lambda *a: calls.append(a))
    await cron.fire(scraper_id)
    assert calls == [(scraper_id, "schedule")]


async def test_fire_on_a_deleted_scraper_is_a_no_op(db):
    assert await cron.fire(404) is None
