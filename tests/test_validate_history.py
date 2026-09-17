"""The one place validation touches a database: loading row-count history.

Everything else in `smartscraper.validate` is a pure function. This test wires
`load_history` to a real migrated SQLite file through `smartscraper.repo`, so a
change to the query or to the band window shows up here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from smartscraper.db.models import Run, RunStatus, Scraper
from smartscraper.dsl.models import RowCountBand
from smartscraper.validate import validate
from smartscraper.validate.drift import load_history
from tests.test_migrations import alembic_config
from tests.test_validate_rules import rows, script


@pytest.fixture
async def session_factory(tmp_path: Path):
    db = tmp_path / "history.db"
    command.upgrade(alembic_config(db), "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(factory, counts: list[int], *, status: str = RunStatus.PASSED) -> int:
    """Insert one scraper and a run per count, oldest first."""
    async with factory() as s:
        scraper = Scraper(name="products", url="https://example.com", yaml_path="scrapers/p.yaml")
        s.add(scraper)
        await s.flush()
        base = datetime.now(UTC) - timedelta(days=len(counts))
        for i, count in enumerate(counts):
            s.add(
                Run(
                    scraper_id=scraper.id,
                    status=status,
                    row_count=count,
                    created_at=base + timedelta(days=i),
                )
            )
        await s.commit()
        return scraper.id


async def test_load_history_returns_recent_counts_newest_first(session_factory):
    scraper_id = await _seed(session_factory, [100, 101, 102, 103, 104, 105])
    async with session_factory() as s:
        history = await load_history(s, scraper_id, RowCountBand(relative_to="last_5_runs"))
    assert history == [105, 104, 103, 102, 101]


async def test_load_history_window_follows_the_band(session_factory):
    scraper_id = await _seed(session_factory, list(range(1, 13)))
    async with session_factory() as s:
        assert len(await load_history(s, scraper_id, RowCountBand(relative_to="last_run"))) == 1
        assert len(await load_history(s, scraper_id, RowCountBand(relative_to="last_10_runs"))) == 10


async def test_failed_runs_are_not_part_of_the_baseline(session_factory):
    scraper_id = await _seed(session_factory, [120, 120, 120], status=RunStatus.VALIDATION_FAILED)
    async with session_factory() as s:
        assert await load_history(s, scraper_id, RowCountBand()) == []


async def test_history_from_the_db_fails_a_halved_run(session_factory):
    """End to end: five passing runs of ~120 rows, then a run that returns 60."""
    scraper_id = await _seed(session_factory, [120, 118, 122, 119, 121])
    s_script = script(min_rows=1, row_count_band={"relative_to": "last_5_runs", "tolerance": 0.5})
    async with session_factory() as s:
        history = await load_history(s, scraper_id, s_script.validation.row_count_band)
    report = validate(rows(60), s_script, history=history)
    assert report.passed is False
    assert report.failures[0].rule == "row_count_band"
    assert report.failures[0].measured == "60 rows, baseline 120.0 (-50%)"
