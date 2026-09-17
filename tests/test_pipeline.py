"""The join between a finished run, its verdict, and the database.

The case that matters: a run that raises nothing, completes every step, and
returns rows that are quietly wrong. Exceptions do not catch that. Validation
does, and this proves the verdict actually reaches the run row.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from smartscraper.db.models import Base, Record, Run, RunStatus, Scraper, ScriptVersion
from smartscraper.pipeline import (
    UNJUDGEABLE,
    parse_runner_report,
    persist_report,
    report_to_dict,
    validate_run,
)

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


async def _seed(s: AsyncSession, *, priced: int, total: int, status=RunStatus.RUNNING) -> Run:
    sc = Scraper(name="shop", url="https://x.test/", yaml_path="scrapers/shop.yaml")
    s.add(sc)
    await s.flush()
    s.add(ScriptVersion(scraper_id=sc.id, version=1, yaml=YAML, status="active"))
    run = Run(scraper_id=sc.id, script_version=1, status=status)
    s.add(run)
    await s.flush()
    for i in range(total):
        s.add(Record(run_id=run.id, scraper_id=sc.id, row_hash=f"h{i}",
                     data={"name": f"item {i}", "price": 8.95 if i < priced else None}))
    await s.flush()
    return run


async def test_clean_run_that_returns_wrong_data_is_marked_validation_failed(session):
    """28 of 42 rows carry no price. Nothing raised; the run still failed."""
    run = await _seed(session, priced=14, total=42)
    report = await validate_run(session, run.id)

    assert report is not None and report.passed is False
    assert run.status == RunStatus.VALIDATION_FAILED
    failed = [r.rule for r in report.failures]
    assert any("null_rate" in r for r in failed), failed
    rule = next(r for r in report.failures if "null_rate" in r.rule)
    assert rule.measured.startswith("0.67"), rule.measured


async def test_a_good_run_passes_and_persists_metrics(session):
    run = await _seed(session, priced=42, total=42)
    report = await validate_run(session, run.id)

    assert report is not None and report.passed is True
    assert run.status == RunStatus.PASSED
    assert run.row_count == 42
    assert run.validator_report["passed"] is True
    assert {m["field"] for m in run.validator_report["metrics"]} == {"name", "price"}


@pytest.mark.parametrize("status", sorted(UNJUDGEABLE))
async def test_a_blocked_or_errored_run_keeps_its_own_status(session, status):
    """A run that never reached the page has a more useful status than 'failed
    validation'. Validation must not overwrite it."""
    run = await _seed(session, priced=0, total=0, status=status)
    await validate_run(session, run.id)
    assert run.status == status


async def test_report_survives_the_runner_subprocess_boundary(session):
    run = await _seed(session, priced=14, total=42)
    report = await validate_run(session, run.id)

    import json
    line = json.dumps({"rows": 42, "validator_report": report_to_dict(report)})
    back = parse_runner_report(line)

    assert back is not None
    assert back.passed is False
    assert back.row_count == report.row_count
    assert [r.rule for r in back.failures] == [r.rule for r in report.failures]


async def test_garbage_from_the_runner_does_not_crash_the_scheduler(session):
    assert parse_runner_report("not json at all") is None
    assert parse_runner_report('{"rows": 3}') is None


async def test_persist_report_is_idempotent_and_does_not_duplicate_metrics(session):
    from smartscraper import repo

    run = await _seed(session, priced=14, total=42)
    report = await validate_run(session, run.id)
    await persist_report(session, run, report)
    await persist_report(session, run, report)

    assert len(await repo.run_metrics(session, run.id)) == 2


async def test_an_unjudgeable_run_returns_no_report_at_all(session):
    """Blocked is not the same as failed. Returning a report would send a repair
    agent after a selector that is fine."""
    run = await _seed(session, priced=0, total=0, status=RunStatus.BLOCKED)
    assert await validate_run(session, run.id) is None
    assert run.status == RunStatus.BLOCKED


async def test_unparseable_yaml_in_the_version_row_does_not_raise(session, tmp_path):
    """A corrupt version row must not take the worker down."""
    from sqlalchemy import select

    run = await _seed(session, priced=42, total=42)
    sv = await session.scalar(select(ScriptVersion).where(ScriptVersion.scraper_id == run.scraper_id))
    sv.yaml = "steps: [this is not: valid: yaml: at all"
    scraper = await session.get(Scraper, run.scraper_id)
    scraper.yaml_path = str(tmp_path / "missing.yaml")
    await session.flush()

    assert await validate_run(session, run.id) is None
    assert run.status == RunStatus.RUNNING  # untouched; it was never judged


async def test_it_falls_back_to_the_yaml_on_disk(session, tmp_path):
    """scrapers/*.yaml is the source of truth; the table only mirrors it."""
    from sqlalchemy import delete, select

    run = await _seed(session, priced=14, total=42)
    path = tmp_path / "shop.yaml"
    path.write_text(YAML, encoding="utf-8")
    scraper = await session.get(Scraper, run.scraper_id)
    scraper.yaml_path = str(path)
    await session.execute(delete(ScriptVersion).where(ScriptVersion.scraper_id == run.scraper_id))
    await session.flush()
    assert await session.scalar(select(ScriptVersion)) is None

    report = await validate_run(session, run.id)
    assert report is not None and report.passed is False
    assert run.status == RunStatus.VALIDATION_FAILED


async def test_the_disk_fallback_looks_where_the_file_is_actually_written(session, monkeypatch, tmp_path):
    """`yaml_path` is a bare filename relative to the scrapers directory, not the
    project root. Resolving it against the root misses every time, which defeats
    the fallback entirely."""
    from types import SimpleNamespace

    from sqlalchemy import delete

    from smartscraper import pipeline

    scrapers = tmp_path / "scrapers"
    scrapers.mkdir()
    (scrapers / "widgets.yaml").write_text(YAML, encoding="utf-8")
    monkeypatch.setattr(pipeline, "get_settings", lambda: SimpleNamespace(scrapers_dir=scrapers))

    run = await _seed(session, priced=14, total=42)
    scraper = await session.get(Scraper, run.scraper_id)
    scraper.yaml_path = "widgets.yaml"        # exactly what agents/jobs.py stores
    await session.execute(delete(ScriptVersion).where(ScriptVersion.scraper_id == run.scraper_id))
    await session.flush()

    report = await validate_run(session, run.id)
    assert report is not None, "the fallback looked in the wrong directory"
    assert report.passed is False
    assert run.status == RunStatus.VALIDATION_FAILED


async def test_promoting_a_version_updates_the_table_and_the_file_together(session, monkeypatch, tmp_path):
    """The table and `scrapers/*.yaml` are two stores of one script. Promoting in
    only one leaves them disagreeing, silently."""
    from types import SimpleNamespace

    from smartscraper import pipeline
    from smartscraper.db.models import VersionStatus

    scrapers = tmp_path / "scrapers"
    scrapers.mkdir()
    (scrapers / "shop.yaml").write_text(YAML, encoding="utf-8")
    monkeypatch.setattr(pipeline, "get_settings", lambda: SimpleNamespace(scrapers_dir=scrapers))

    run = await _seed(session, priced=42, total=42)
    scraper = await session.get(Scraper, run.scraper_id)
    scraper.yaml_path = "shop.yaml"
    candidate_yaml = YAML.replace("version: 1", "version: 2").replace('"h3"', '"h3.title"')
    session.add(ScriptVersion(scraper_id=scraper.id, version=2, yaml=candidate_yaml,
                              status=VersionStatus.CANDIDATE, created_by="repair"))
    await session.flush()

    written = await pipeline.promote_version(session, scraper.id, 2, approved_by="you")

    versions = {v.version: v.status for v in await repo_versions(session, scraper.id)}
    assert versions == {1: VersionStatus.RETIRED, 2: VersionStatus.ACTIVE}
    assert written == scrapers / "shop.yaml"
    assert "h3.title" in written.read_text(), "the file still holds the old script"


async def repo_versions(session, scraper_id):
    from smartscraper import repo

    return await repo.versions(session, scraper_id)
