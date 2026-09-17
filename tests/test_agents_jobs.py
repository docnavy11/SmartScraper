"""The orchestration entry points the scheduler calls.

Offline throughout: the browser is a FakeEngine, the gateway is scripted, and
the runner's `execute` is replaced by a canned outcome. What is exercised for
real is the database work, the promotion decision and the resource lifecycle.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from smartscraper.agents import jobs
from smartscraper.agents.fake import FakeGateway, ToolCall, Turn
from smartscraper.contracts import RunOutcome, Usage
from smartscraper.db import session as db_session
from smartscraper.db.models import (
    Author,
    LlmUsage,
    Repair,
    Run,
    RunStatus,
    Scraper,
    ScriptVersion,
    VersionStatus,
)
from smartscraper.dsl.models import PromotionPolicy, ScrapeScript
from tests.test_agents_support import ROWS, FakeEngine, good_probes, script_dict, settings_for

pytestmark = pytest.mark.usefixtures("db")


# --------------------------------------------------------------------------- fixtures
@pytest.fixture
async def settings(tmp_path, monkeypatch):
    s = settings_for(tmp_path)
    s.ensure_dirs()
    monkeypatch.setattr(jobs, "get_settings", lambda: s)
    return s


@pytest.fixture
async def db(tmp_path, settings):
    """A real database on disk, and the global factory restored afterwards.

    `jobs` reaches for `get_session()`, which resolves a module-global factory.
    Leaving a disposed engine in that global would break whatever test runs next.
    """
    previous = (db_session._engine, db_session._factory)
    engine = db_session.init_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    await db_session.create_all()
    try:
        yield
    finally:
        await engine.dispose()
        db_session._engine, db_session._factory = previous


@pytest.fixture
def engine(monkeypatch):
    """Replace the real browser with a fake, and record that it was closed."""
    fake = FakeEngine(probes=good_probes())

    @asynccontextmanager
    async def open_engine(rung, **kwargs):
        fake.opened = True
        fake.rung = rung
        fake.open_kwargs = kwargs
        try:
            yield fake
        finally:
            fake.closed = True

    monkeypatch.setattr(jobs, "open_engine", open_engine)
    return fake


@pytest.fixture
def outcome(monkeypatch):
    """Replace the runner with a canned successful outcome."""
    box = {"outcome": RunOutcome(rows=list(ROWS), engine_used="patchright", escalation_level=1)}

    async def execute(script, *, run_id, settings):
        box["run_ids"] = [*box.get("run_ids", []), run_id]
        box["scripts"] = [*box.get("scripts", []), script]
        return box["outcome"]

    monkeypatch.setattr(jobs, "_execute", execute)
    return box


def scripted(monkeypatch, turns, **kwargs) -> FakeGateway:
    gateway = FakeGateway(turns, **kwargs)
    monkeypatch.setattr(jobs, "make_gateway", lambda budget, **kw: gateway)
    return gateway


def a_build_turn(script=None) -> Turn:
    return Turn(
        calls=[
            ToolCall("navigate", {"url": "https://example.com/products"}),
            ToolCall("snapshot", {}),
            ToolCall("extract_probe", {"selector": "css=.product"}),
            ToolCall("propose_script", {"script": script or script_dict()}),
            ToolCall("finish", {}),
        ],
        text="Three product cards, probed each field.",
        usage=Usage(model="claude-opus-5", input_tokens=20_000, output_tokens=2_000, turns=5),
    )


async def seed_scraper(settings, *, policy=PromotionPolicy.MANUAL, status=RunStatus.VALIDATION_FAILED):
    """A scraper with an active v1 and one failed run, which is what repair needs."""
    script = ScrapeScript.model_validate(script_dict())
    (settings.scrapers_dir / "widgets.yaml").write_text(script.to_yaml(), encoding="utf-8")
    async with db_session.get_session() as s:
        scraper = Scraper(
            name="widgets",
            url="https://example.com/products",
            goal="every product",
            yaml_path="widgets.yaml",
            promotion_policy=policy,
        )
        s.add(scraper)
        await s.flush()
        s.add(
            ScriptVersion(
                scraper_id=scraper.id,
                version=1,
                yaml=script.to_yaml(),
                created_by=Author.BUILDER,
                status=VersionStatus.ACTIVE,
            )
        )
        run = Run(
            scraper_id=scraper.id,
            script_version=1,
            status=status,
            row_count=0,
            validator_report={
                "passed": False,
                "row_count": 0,
                "rules": [
                    {"rule": "min_rows", "passed": False, "measured": "0", "expected": ">= 1"}
                ],
                "metrics": [],
            },
        )
        s.add(run)
        await s.flush()
        return scraper.id, run.id


def a_repair_turn(new_script, text="The container class changed.") -> Turn:
    return Turn(
        calls=[
            ToolCall("snapshot", {}),
            ToolCall("extract_probe", {"selector": "css=.item"}),
            ToolCall("propose_script", {"script": new_script}),
            ToolCall("finish", {}),
        ],
        text=text,
        usage=Usage(model="claude-opus-5", input_tokens=30_000, output_tokens=1_000, turns=4),
    )


def selector_repair() -> dict:
    fixed = script_dict()
    fixed["steps"][1]["selector"] = "css=.item"
    fixed["steps"][2]["selector"] = "css=.item"
    return fixed


# --------------------------------------------------------------------------- pure decisions
@pytest.mark.parametrize(
    ("policy", "is_minor", "expected"),
    [
        (PromotionPolicy.AUTO, True, True),
        (PromotionPolicy.AUTO, False, True),
        (PromotionPolicy.AUTO_IF_MINOR, True, True),
        (PromotionPolicy.AUTO_IF_MINOR, False, False),
        (PromotionPolicy.MANUAL, True, False),
        (PromotionPolicy.MANUAL, False, False),
    ],
)
def test_the_promotion_matrix(policy, is_minor, expected):
    promote, why = jobs.may_auto_promote(policy, is_minor=is_minor, has_custom_python=False)
    assert promote is expected
    assert why


@pytest.mark.parametrize(
    "policy", [PromotionPolicy.AUTO, PromotionPolicy.AUTO_IF_MINOR, PromotionPolicy.MANUAL]
)
def test_custom_python_is_never_auto_promoted_under_any_policy(policy):
    promote, why = jobs.may_auto_promote(policy, is_minor=True, has_custom_python=True)
    assert promote is False
    assert "custom_python" in why


def test_the_agent_gets_a_browser_rung_even_when_the_script_says_auto(settings):
    assert jobs.rung_for(None, settings) == "patchright"
    assert jobs.rung_for(ScrapeScript.model_validate(script_dict(engine="auto")), settings) == "patchright"
    assert jobs.rung_for(ScrapeScript.model_validate(script_dict(engine="camoufox")), settings) == "camoufox"


# --------------------------------------------------------------------------- budget
async def test_the_monthly_cap_refuses_before_anything_is_opened(settings, monkeypatch, engine):
    async with db_session.get_session() as s:
        s.add(LlmUsage(agent="builder", model="claude-opus-5", cost_usd=settings.monthly_budget))

    result = await jobs.build_scraper("https://example.com/p", "products", "widgets")
    assert not result.ok
    assert "monthly budget is spent" in result.error
    assert engine.opened is False


async def test_the_remaining_headroom_is_what_the_gateway_may_spend(settings):
    async with db_session.get_session() as s:
        s.add(LlmUsage(agent="builder", model="claude-opus-5", cost_usd=10.0))
    budget = await jobs.remaining_budget(None, settings)
    assert budget.limit_usd == pytest.approx(settings.monthly_budget - 10.0)


async def test_a_scrapers_own_cap_is_the_tighter_of_the_two(settings):
    scraper_id, _ = await seed_scraper(settings)
    async with db_session.get_session() as s:
        sc = await s.get(Scraper, scraper_id)
        sc.budget_usd_month = 2.0
    budget = await jobs.remaining_budget(scraper_id, settings)
    assert budget.limit_usd == pytest.approx(2.0)


# --------------------------------------------------------------------------- build
async def test_a_successful_build_persists_the_scraper_and_its_first_version(
    settings, engine, outcome, monkeypatch
):
    scripted(monkeypatch, [a_build_turn()])
    result = await jobs.build_scraper("https://example.com/products", "every product", "widgets")

    assert result.ok
    async with db_session.get_session() as s:
        scraper = await s.scalar(select(Scraper).where(Scraper.name == "widgets"))
        assert scraper is not None
        assert scraper.url == "https://example.com/products"
        assert scraper.yaml_path == "widgets.yaml"
        version = await s.scalar(select(ScriptVersion).where(ScriptVersion.scraper_id == scraper.id))
        assert version.status == VersionStatus.ACTIVE
        assert version.created_by == Author.BUILDER
        assert version.version == 1


async def test_the_yaml_on_disk_is_the_script_that_was_built(settings, engine, outcome, monkeypatch):
    scripted(monkeypatch, [a_build_turn()])
    result = await jobs.build_scraper("https://example.com/products", "products", "widgets")

    path = settings.scrapers_dir / "widgets.yaml"
    assert path.is_file()
    assert ScrapeScript.from_yaml(path.read_text()).field_names() == ["name", "price"]
    assert path.read_text() == result.script.to_yaml()


async def test_the_build_writes_its_usage_row(settings, engine, outcome, monkeypatch):
    scripted(monkeypatch, [a_build_turn()])
    await jobs.build_scraper("https://example.com/products", "products", "widgets")

    async with db_session.get_session() as s:
        usage = await s.scalar(select(LlmUsage))
        assert usage.agent == "builder"
        assert usage.model == "claude-opus-5"
        assert usage.cost_usd == pytest.approx((20_000 * 5 + 2_000 * 25) / 1_000_000)
        assert usage.scraper_id is not None


async def test_the_browser_is_closed_even_though_the_build_succeeded(
    settings, engine, outcome, monkeypatch
):
    scripted(monkeypatch, [a_build_turn()])
    await jobs.build_scraper("https://example.com/products", "products", "widgets")
    assert engine.opened and engine.closed


async def test_a_failed_build_persists_nothing_but_still_bills(settings, engine, outcome, monkeypatch):
    broken = script_dict()
    broken["steps"][2]["fields"] = "not a mapping"
    scripted(monkeypatch, [a_build_turn(broken)])

    result = await jobs.build_scraper("https://example.com/products", "products", "widgets")
    assert not result.ok
    async with db_session.get_session() as s:
        assert await s.scalar(select(Scraper)) is None
        assert (await s.scalar(select(LlmUsage))).agent == "builder"
    assert engine.closed


async def test_a_build_refuses_a_name_that_already_exists(settings, engine, outcome, monkeypatch):
    await seed_scraper(settings)
    scripted(monkeypatch, [a_build_turn()])

    result = await jobs.build_scraper("https://example.com/products", "products", "widgets")
    assert not result.ok
    assert "already exists" in result.error
    assert engine.opened is False


async def test_a_browser_that_will_not_open_is_reported_not_raised(settings, outcome, monkeypatch):
    from smartscraper.runner.errors import EngineUnavailable

    @asynccontextmanager
    async def refuses(rung, **kwargs):
        raise EngineUnavailable("playwright is not installed")
        yield  # pragma: no cover

    monkeypatch.setattr(jobs, "open_engine", refuses)
    scripted(monkeypatch, [a_build_turn()])

    result = await jobs.build_scraper("https://example.com/products", "products", "widgets")
    assert not result.ok
    assert "EngineUnavailable" in result.error


async def test_a_build_whose_trial_run_fails_validation_is_not_persisted(
    settings, engine, outcome, monkeypatch
):
    outcome["outcome"] = RunOutcome(rows=[], engine_used="patchright", escalation_level=1)
    scripted(monkeypatch, [a_build_turn()])

    result = await jobs.build_scraper("https://example.com/products", "products", "widgets")
    assert not result.ok
    async with db_session.get_session() as s:
        assert await s.scalar(select(Scraper)) is None


# --------------------------------------------------------------------------- repair
async def test_a_manual_policy_parks_the_candidate_for_approval(
    settings, engine, outcome, monkeypatch
):
    scraper_id, run_id = await seed_scraper(settings)
    scripted(monkeypatch, [a_repair_turn(selector_repair())])

    result = await jobs.repair_scraper(scraper_id, run_id)
    assert result.ok
    assert result.is_minor

    async with db_session.get_session() as s:
        version = await s.scalar(select(ScriptVersion).where(ScriptVersion.version == 2))
        assert version.status == VersionStatus.CANDIDATE
        assert version.created_by == Author.REPAIR
        assert version.is_minor is True

        row = await s.scalar(select(Repair))
        assert row.status == "pending_approval"
        assert row.candidate_version == 2
        assert row.is_minor is True
        assert "css=.item" in row.diff
        assert row.run_id == run_id

        active = await s.scalar(
            select(ScriptVersion).where(ScriptVersion.status == VersionStatus.ACTIVE)
        )
        assert active.version == 1


async def test_auto_if_minor_promotes_a_selector_repair(settings, engine, outcome, monkeypatch):
    scraper_id, run_id = await seed_scraper(settings, policy=PromotionPolicy.AUTO_IF_MINOR)
    scripted(monkeypatch, [a_repair_turn(selector_repair())])

    result = await jobs.repair_scraper(scraper_id, run_id)
    assert result.ok

    async with db_session.get_session() as s:
        versions = {v.version: v.status for v in (await s.scalars(select(ScriptVersion))).all()}
        assert versions == {1: VersionStatus.RETIRED, 2: VersionStatus.ACTIVE}
        assert (await s.scalar(select(Repair))).status == "auto_promoted"

    on_disk = ScrapeScript.from_yaml((settings.scrapers_dir / "widgets.yaml").read_text())
    assert on_disk.version == 2


async def test_auto_if_minor_parks_a_structural_repair(settings, engine, outcome, monkeypatch):
    bigger = selector_repair()
    bigger["steps"].insert(1, {"op": "scroll", "to": "bottom", "times": 3})
    scraper_id, run_id = await seed_scraper(settings, policy=PromotionPolicy.AUTO_IF_MINOR)
    scripted(monkeypatch, [a_repair_turn(bigger)])

    result = await jobs.repair_scraper(scraper_id, run_id)
    assert not result.is_minor
    async with db_session.get_session() as s:
        assert (await s.scalar(select(Repair))).status == "pending_approval"
        active = await s.scalar(
            select(ScriptVersion).where(ScriptVersion.status == VersionStatus.ACTIVE)
        )
        assert active.version == 1
    on_disk = ScrapeScript.from_yaml((settings.scrapers_dir / "widgets.yaml").read_text())
    assert on_disk.version == 1


async def test_an_auto_policy_promotes_a_structural_repair(settings, engine, outcome, monkeypatch):
    bigger = selector_repair()
    bigger["steps"].insert(1, {"op": "scroll", "to": "bottom", "times": 3})
    scraper_id, run_id = await seed_scraper(settings, policy=PromotionPolicy.AUTO)
    scripted(monkeypatch, [a_repair_turn(bigger)])

    await jobs.repair_scraper(scraper_id, run_id)
    async with db_session.get_session() as s:
        assert (await s.scalar(select(Repair))).status == "auto_promoted"


async def test_custom_python_is_refused_before_it_can_reach_a_policy(
    settings, engine, outcome, monkeypatch
):
    sneaky = selector_repair()
    sneaky["steps"].insert(1, {"op": "custom_python", "code": "import os"})
    scraper_id, run_id = await seed_scraper(settings, policy=PromotionPolicy.AUTO)
    gateway = scripted(monkeypatch, [a_repair_turn(sneaky)])

    result = await jobs.repair_scraper(scraper_id, run_id)
    assert not result.ok
    assert gateway.denials == ["propose_script"]
    async with db_session.get_session() as s:
        assert await s.scalar(select(Repair)) is None


async def test_the_trial_run_is_stored_and_linked_from_the_candidate(
    settings, engine, outcome, monkeypatch
):
    scraper_id, run_id = await seed_scraper(settings)
    scripted(monkeypatch, [a_repair_turn(selector_repair())])

    await jobs.repair_scraper(scraper_id, run_id)
    async with db_session.get_session() as s:
        trial = await s.scalar(select(Run).where(Run.trigger == "repair"))
        assert trial.status == RunStatus.PASSED
        assert trial.row_count == 3
        assert trial.validator_report["passed"] is True

        version = await s.scalar(select(ScriptVersion).where(ScriptVersion.version == 2))
        assert version.test_run_id == trial.id
        assert (await s.scalar(select(Repair))).test_run_id == trial.id


async def test_a_candidate_whose_trial_fails_is_not_stored(settings, engine, outcome, monkeypatch):
    outcome["outcome"] = RunOutcome(rows=[], engine_used="patchright", escalation_level=1)
    scraper_id, run_id = await seed_scraper(settings)
    scripted(monkeypatch, [a_repair_turn(selector_repair())])

    result = await jobs.repair_scraper(scraper_id, run_id)
    assert not result.ok
    async with db_session.get_session() as s:
        assert await s.scalar(select(Repair)) is None
        assert await s.scalar(select(ScriptVersion).where(ScriptVersion.version == 2)) is None
        # The failed trial is still on record.
        assert (await s.scalar(select(Run).where(Run.trigger == "repair"))).status == (
            RunStatus.VALIDATION_FAILED
        )


async def test_a_capability_gap_blocks_promotion_and_reaches_the_human(
    settings, monkeypatch, outcome
):
    from tests.test_agents_support import HttpEngine

    http = HttpEngine(probes=good_probes())

    @asynccontextmanager
    async def open_http(rung, **kwargs):
        yield http

    monkeypatch.setattr(jobs, "open_engine", open_http)

    turn = a_repair_turn(selector_repair())
    turn.calls.insert(0, ToolCall("click", {"selector": "#accept"}))
    scraper_id, run_id = await seed_scraper(settings, policy=PromotionPolicy.AUTO_IF_MINOR)
    scripted(monkeypatch, [turn])

    result = await jobs.repair_scraper(scraper_id, run_id)
    assert result.ok
    assert result.is_minor
    assert result.capability_gaps == ["click"]

    async with db_session.get_session() as s:
        row = await s.scalar(select(Repair))
        # Minor under an auto_if_minor policy, and still parked.
        assert row.status == "pending_approval"
        assert "could not click" in row.reason
        assert "browser rung" in row.reason


async def test_repair_falls_back_to_the_latest_failed_run(settings, engine, outcome, monkeypatch):
    scraper_id, run_id = await seed_scraper(settings)
    scripted(monkeypatch, [a_repair_turn(selector_repair())])

    result = await jobs.repair_scraper(scraper_id, None)
    assert result.ok
    async with db_session.get_session() as s:
        assert (await s.scalar(select(Repair))).run_id == run_id


async def test_repair_reports_a_missing_scraper_rather_than_raising(settings, engine, outcome):
    result = await jobs.repair_scraper(999, None)
    assert not result.ok
    assert "no scraper with id 999" in result.error


async def test_repair_reports_a_scraper_with_nothing_to_repair(settings, engine, outcome):
    scraper_id, _ = await seed_scraper(settings, status=RunStatus.PASSED)
    result = await jobs.repair_scraper(scraper_id, None)
    assert not result.ok
    assert "no failed run" in result.error


async def test_the_repair_agent_is_given_the_failed_runs_report(
    settings, engine, outcome, monkeypatch
):
    scraper_id, run_id = await seed_scraper(settings)
    gateway = scripted(monkeypatch, [a_repair_turn(selector_repair())])

    await jobs.repair_scraper(scraper_id, run_id)
    prompt = gateway.runs[0]["prompt"]
    assert "min_rows" in prompt
    assert "css=.product" in prompt
    assert gateway.runs[0]["model"] == "claude-opus-5"


async def test_the_repair_writes_its_usage_row_against_the_failed_run(
    settings, engine, outcome, monkeypatch
):
    scraper_id, run_id = await seed_scraper(settings)
    scripted(monkeypatch, [a_repair_turn(selector_repair())])

    await jobs.repair_scraper(scraper_id, run_id)
    async with db_session.get_session() as s:
        usage = await s.scalar(select(LlmUsage))
        assert usage.agent == "repair"
        assert usage.scraper_id == scraper_id
        assert usage.run_id == run_id


# --------------------------------------------------------------------------- wiring
def test_the_scheduler_finds_both_entry_points():
    from smartscraper.scheduler.tasks import (
        BUILD_ENTRY_POINTS,
        REPAIR_ENTRY_POINTS,
        _entry_point,
    )

    assert _entry_point(BUILD_ENTRY_POINTS) is jobs.build_scraper
    assert _entry_point(REPAIR_ENTRY_POINTS) is jobs.repair_scraper
