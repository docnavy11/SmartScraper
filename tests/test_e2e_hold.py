"""The one behaviour the whole system exists for.

A scrape can succeed technically and fail completely: every step runs, nothing
raises, and the rows come back quietly wrong. Exceptions do not catch that.
Validation does, and delivery must then refuse to ship.

This crosses runner output, validation, the pipeline seam and delivery, so it
belongs to none of them and would not be caught by any one package's own tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from smartscraper.db.models import (
    Base,
    DeliveryTarget,
    Record,
    Run,
    RunStatus,
    Scraper,
    ScriptVersion,
)
from smartscraper.delivery.base import deliver_run
from smartscraper.pipeline import validate_run

YAML = """
version: 1
engine: http
steps:
  - op: goto
    url: "https://x.test/"
  - op: extract_list
    selector: "css=.card"
    as: rows
    fields:
      name: {selector: "h3"}
      price: {selector: ".price", parse: money}
  - op: emit
    from: rows
validation:
  min_rows: 5
  max_null_rate: {price: 0.05}
"""


@pytest.fixture
async def session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        yield s
    await eng.dispose()


async def _run_with(session, tmp_path, *, priced: int, total: int) -> tuple[Run, list]:
    sc = Scraper(name="shop", url="https://x.test/", yaml_path="x.yaml")
    session.add(sc)
    await session.flush()
    session.add(ScriptVersion(scraper_id=sc.id, version=1, yaml=YAML, status="active"))
    run = Run(scraper_id=sc.id, script_version=1, status=RunStatus.RUNNING)
    session.add(run)
    await session.flush()
    for i in range(total):
        session.add(Record(run_id=run.id, scraper_id=sc.id, row_hash=f"h{i}",
                           data={"name": f"item {i}", "price": 8.95 if i < priced else None}))
    session.add(DeliveryTarget(scraper_id=sc.id, kind="file", fmt="jsonl",
                               config={"dir": str(tmp_path / "strict")}))
    session.add(DeliveryTarget(scraper_id=sc.id, kind="file", fmt="jsonl",
                               config={"dir": str(tmp_path / "provisional"), "provisional": True}))
    await session.flush()

    await validate_run(session, run.id)
    outcomes = await deliver_run(run.id, session=session)
    return run, outcomes


async def test_quietly_wrong_data_is_held_not_shipped(session, tmp_path):
    """28 of 42 rows have no price. Nothing raised. Nothing may ship."""
    run, outcomes = await _run_with(session, tmp_path, priced=14, total=42)

    assert run.status == RunStatus.VALIDATION_FAILED

    by_status = {o.status for o in outcomes}
    assert "held" in by_status, outcomes
    held = next(o for o in outcomes if o.status == "held")
    assert held.rows_sent == 0
    assert "did not opt into provisional" in held.detail

    # the strict target wrote nothing to disk
    assert not list((tmp_path / "strict").glob("*")) if (tmp_path / "strict").exists() else True


async def test_a_target_that_opted_in_still_receives_provisional_rows(session, tmp_path):
    """Downstream consumers keep getting data, flagged, while a repair is pending."""
    _, outcomes = await _run_with(session, tmp_path, priced=14, total=42)

    sent = [o for o in outcomes if o.status == "sent"]
    assert len(sent) == 1, outcomes
    assert sent[0].rows_sent == 42
    assert list((tmp_path / "provisional").glob("*.jsonl"))


async def test_clean_data_reaches_every_target(session, tmp_path):
    run, outcomes = await _run_with(session, tmp_path, priced=42, total=42)

    assert run.status == RunStatus.PASSED
    assert {o.status for o in outcomes} == {"sent"}
    assert all(o.rows_sent == 42 for o in outcomes)
    assert list((tmp_path / "strict").glob("*.jsonl"))
    assert list((tmp_path / "provisional").glob("*.jsonl"))
