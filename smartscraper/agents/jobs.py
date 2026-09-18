"""Orchestration entry points: plain identifiers in, persisted work out.

`build` and `repair` take a live gateway and a live engine because that keeps
them testable offline. Nothing else in the system wants to know how to open a
browser or which gateway backend is configured, so the wiring lives here and
only here. The scheduler calls `build_scraper` and `repair_scraper` by name.

What this module owns:

* choosing the engine rung and opening it, then closing it in a finally,
* constructing the gateway with the right budget and refusing when it is gone,
* the trial run a proposal is judged by, which for repair goes through
  `smartscraper.pipeline` so the verdict is stored the same way every other
  run's verdict is stored,
* persistence: the scraper, its script versions, the repair row, the YAML on
  disk, the audit trail and the `llm_usage` row,
* the promotion decision, including the one rule no policy can override.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select

from .. import repo
from ..config import Settings, get_settings
from ..contracts import Engine, PageSnapshot, ValidatorReport
from ..db.models import (
    Author,
    Repair,
    Run,
    RunStatus,
    Scraper,
    ScriptVersion,
    VersionStatus,
)
from ..db.session import get_session
from ..dsl.models import PromotionPolicy, ScrapeScript
from .builder import BuildRequest, BuildResult, build, slug
from .cost import usage_row
from .gateway import AgentSdkGateway, Budget
from .repair import RepairRequest, RepairResult, repair
from .tools_browser import TrialRun

log = logging.getLogger(__name__)

BUILDER = "builder"
REPAIRER = "repair"

# `auto` is a rung chooser, not an engine. The builder needs a real page to
# probe against, so an unresolved `auto` becomes the default browser rung.
DEFAULT_RUNG = "patchright"
BROWSER_RUNGS = frozenset({"playwright", "patchright", "camoufox"})

# Ordered cheapest first, the same shape as the runner's ladder in PLAN.md.
#
# `http` leads for a measured reason, not a guess. A large retailer served this host
# HTTP 403 and a bot-block page to headless Chromium, while plain curl with a
# browser User-Agent got 200 and 914 KB of product HTML from the same IP in the
# same minute. What was refused was the browser's fingerprint, so starting with
# a browser makes the cheapest rung unreachable on exactly the sites that need
# it. It is also faster and spends no browser at all when it works.
BUILD_LADDER: tuple[str, ...] = ("http", "patchright", "camoufox")

# The site refused us.
BLOCK_SIGNS: tuple[str, ...] = (
    "403", "429", "blocked", "block page", "bot", "captcha", "challenge",
    "access denied", "forbidden", "cloudflare", "datadome", "perimeterx",
)

# The engine reached the page but could not render it. Cheap rungs return a
# shell for a JavaScript site, which is a reason to climb rather than to stop.
THIN_SIGNS: tuple[str, ...] = (
    "empty", "no content", "javascript", "js-only", "blank", "no html",
    "nothing to extract", "no elements", "renders nothing",
)


def looks_blocked(text: str) -> bool:
    low = (text or "").lower()
    return any(sign in low for sign in BLOCK_SIGNS)


def engine_is_the_problem(text: str) -> bool:
    """Whether a harder engine could plausibly do better.

    A page the agent simply could not make sense of will read no better from a
    stealthier browser, and retrying it spends the tokens twice.
    """
    low = (text or "").lower()
    return looks_blocked(low) or any(sign in low for sign in THIN_SIGNS)


class BudgetRefused(RuntimeError):
    """The monthly cap is spent. Raised before anything is opened or charged."""


# --------------------------------------------------------------------------- resources
def rung_for(script: ScrapeScript | None, settings: Settings) -> str:
    """Which browser the agent drives while it explores.

    Not the same question as which rung a *run* uses: the runner escalates on
    its own. This is only about giving the model a page it can probe.
    """
    for candidate in (str(script.engine) if script else "", settings.default_engine):
        if candidate in BROWSER_RUNGS:
            return candidate
    return DEFAULT_RUNG


@asynccontextmanager
async def open_engine(
    rung: str,
    *,
    settings: Settings,
    proxy: str | None = None,
    profile: str | None = None,
    artifacts_dir: Path | None = None,
) -> AsyncIterator[Engine]:
    """Open one engine for the agent to drive, and always close it.

    `http` is a real rung here, not only in the runner. It is the cheapest way to
    look at a page, and on a site that refuses a headless browser's fingerprint
    it is the only one that works. The agent copes with it: the HTTP engine
    raises `EngineCapabilityError` from click and fill, which the tool surface
    records as a capability gap rather than a crash.
    """
    engine: Engine
    if rung == "http":
        from ..runner.engines.http import HttpEngine

        engine = HttpEngine()  # impersonates a real Chrome at the TLS layer
    else:
        from ..runner.engines.browser import BrowserEngine

        engine = BrowserEngine(engine=rung, artifacts_dir=artifacts_dir, trace=False, har=False)
    await engine.open(proxy=proxy, profile=profile, headed=settings.headed_effective)
    try:
        yield engine
    finally:
        try:
            await engine.close()
        except Exception:  # noqa: BLE001 - a failed close must not lose the result
            log.exception("closing the %s engine failed", rung)


async def remaining_budget(scraper_id: int | None, settings: Settings) -> Budget:
    """What this job may spend, from the monthly cap and what is already spent.

    Raises `BudgetRefused` when there is nothing left, so no browser is opened
    and no tokens are bought for work that cannot be paid for.
    """
    async with get_session() as s:
        month = await repo.spend_month_to_date(s)
        per_scraper_cap = settings.default_scraper_budget
        spent_here = 0.0
        if scraper_id is not None:
            scraper = await repo.get_scraper(s, scraper_id)
            if scraper is not None:
                per_scraper_cap = scraper.budget_usd_month
            spent_here = await repo.spend_month_to_date(s, scraper_id)

    if settings.stop_at_budget and month >= settings.monthly_budget:
        raise BudgetRefused(
            f"the monthly budget is spent: ${month:.2f} of ${settings.monthly_budget:.2f}"
        )
    if settings.stop_at_budget and spent_here >= per_scraper_cap:
        raise BudgetRefused(
            f"this scraper's monthly budget is spent: ${spent_here:.2f} of ${per_scraper_cap:.2f}"
        )

    headroom = settings.monthly_budget - month
    if scraper_id is not None:
        headroom = min(headroom, per_scraper_cap - spent_here)
    return Budget(limit_usd=max(headroom, 0.0))


def make_gateway(budget: Budget, *, transcript_path: Path | None = None) -> AgentSdkGateway:
    """The one place a gateway backend is chosen.

    `allow_custom_python` is never set here. An agent that wants unsandboxed
    Python has to be given it deliberately by a human, not by a worker.
    """
    return AgentSdkGateway(budget=budget, transcript_path=transcript_path)


async def record_usage(result: Any, *, agent: str, scraper_id: int | None, run_id: int | None) -> None:
    """Write the `llm_usage` row. Spend that is not recorded cannot be capped."""
    usage = getattr(result, "usage", None)
    if usage is None:
        return
    async with get_session() as s:
        s.add(usage_row(usage, agent=agent, scraper_id=scraper_id, run_id=run_id))


# --------------------------------------------------------------------------- trial runs
async def _execute(script: ScrapeScript, *, run_id: int | str, settings: Settings):
    from ..runner import execute

    return await execute(script, run_id=run_id, headed=False)


def _trial_from_report(report: ValidatorReport, rows: list[dict[str, Any]]) -> TrialRun:
    return TrialRun(
        ok=report.passed,
        row_count=report.row_count,
        rows=rows[:20],
        summary=report.summary(),
        error=None if report.passed else report.summary(),
    )


def build_trial(settings: Settings, name: str):
    """Trial runner for a build: no scraper row exists yet, so judge in memory.

    The validator is the same one the pipeline uses. What it cannot do here is
    persist a verdict, because a `run` row needs a scraper to belong to and the
    scraper is only created once a script works.
    """
    from ..validate import validate

    counter = {"n": 0}

    async def trial(script: ScrapeScript) -> TrialRun:
        counter["n"] += 1
        outcome = await _execute(script, run_id=f"build-{name}-{counter['n']}", settings=settings)
        if outcome.error or outcome.blocked:
            return TrialRun(
                ok=False,
                row_count=len(outcome.rows),
                error=outcome.error or f"blocked: {outcome.block_reason}",
            )
        report = validate(outcome.rows, script, ())
        return _trial_from_report(report, outcome.rows)

    return trial


def repair_trial(settings: Settings, scraper: Scraper, state: dict[str, Any]):
    """Trial runner for a repair: a real run row, judged and stored by the pipeline.

    The run id of the last trial is kept in `state` so the candidate version and
    the repair row can both point at the run that justified them.

    The verdict goes through `pipeline.persist_report` rather than
    `pipeline.validate_run`, and the difference matters. `validate_run` loads the
    script by looking up the `script_version` row the run names. The candidate
    being tried here has no row yet: it is written only once it passes, because a
    version that fails its trial should not be in the table at all. So the script
    is validated in hand and the verdict is persisted through the pipeline, which
    is the half that owns the run row, the metrics and the audit line.
    """
    from .. import pipeline
    from ..scheduler.tasks import persist_records
    from ..validate import validate

    async def trial(script: ScrapeScript) -> TrialRun:
        async with get_session() as s:
            run = Run(
                scraper_id=scraper.id,
                script_version=state.get("candidate_version", 0),
                status=RunStatus.RUNNING,
                trigger="repair",
                started_at=datetime.now(UTC),
            )
            s.add(run)
            await s.flush()
            run_id = run.id

        outcome = await _execute(script, run_id=run_id, settings=settings)

        async with get_session() as s:
            run = await s.get(Run, run_id)
            run.engine_used = outcome.engine_used
            run.escalation_level = outcome.escalation_level
            run.proxy_used = outcome.proxy_used
            run.finished_at = datetime.now(UTC)
            if outcome.error or outcome.blocked:
                run.status = RunStatus.BLOCKED if outcome.blocked else RunStatus.ERROR
                run.error = outcome.error or f"blocked: {outcome.block_reason}"
                state["trial_run_id"] = run_id
                return TrialRun(ok=False, row_count=0, error=run.error)
            await persist_records(s, run, outcome.rows)
            history = [
                c
                for c in await repo.recent_row_counts(s, scraper.id, n=5, exclude_run_id=run_id)
                if c is not None
            ]
            report = validate(outcome.rows, script, history)
            await pipeline.persist_report(s, run, report)

        state["trial_run_id"] = run_id
        return _trial_from_report(report, outcome.rows)

    return trial


# --------------------------------------------------------------------------- build
def script_filename(name: str) -> str:
    return f"{name}.yaml"


async def persist_build(result: BuildResult, *, url: str, goal: str, name: str) -> int:
    """Create the scraper, its first version, and the YAML that is the truth."""
    settings = get_settings()
    settings.ensure_dirs()
    filename = script_filename(name)
    (settings.scrapers_dir / filename).write_text(result.script.to_yaml(), encoding="utf-8")

    async with get_session() as s:
        scraper = Scraper(
            name=name,
            url=url,
            goal=goal,
            yaml_path=filename,
            promotion_policy=PromotionPolicy.MANUAL,
            budget_usd_month=settings.default_scraper_budget,
            respect_robots=settings.respect_robots,
        )
        s.add(scraper)
        await s.flush()
        s.add(
            ScriptVersion(
                scraper_id=scraper.id,
                version=result.script.version,
                yaml=result.script.to_yaml(),
                created_by=Author.BUILDER,
                change_summary=f"built from {url}",
                rationale=result.text[:4000],
                status=VersionStatus.ACTIVE,
                is_minor=False,
                has_custom_python=result.script.has_custom_python,
                output_schema=result.script.output_schema,
            )
        )
        detail = f"{result.row_count} rows on the build run"
        if result.capability_gaps:
            detail += f"; this engine could not {', '.join(sorted(set(result.capability_gaps)))}"
        await repo.log(
            s,
            actor=BUILDER,
            action="built scraper",
            object_type="scraper",
            object_ref=name,
            detail=detail,
            meta={"url": url, "fixture": str(result.fixture_path or "")},
        )
        return scraper.id


async def build_scraper(
    url: str,
    goal: str,
    name: str | None = None,
    output_schema: dict[str, Any] | None = None,
    on_event: Any | None = None,
) -> BuildResult:
    """Build a scraper from a URL and a goal, and persist it if it works.

    Returns a failed `BuildResult` rather than raising, because the caller is a
    queue task whose job is to report, not to crash.

    `on_event(stage, detail)` is called at each stage and forwarded to the
    builder. A build takes minutes, so a caller with a person waiting needs to
    show something other than a spinner.
    """
    settings = get_settings()
    scraper_name = slug(name or url)

    def say(stage: str, detail: str = "") -> None:
        if on_event is None:
            return
        try:
            on_event(stage, detail)
        except Exception:  # a broken listener must never fail a build
            log.exception("build listener raised on %s", stage)

    say("checking budget", f"scraper will be named {scraper_name}")
    try:
        budget = await remaining_budget(None, settings)
    except BudgetRefused as exc:
        say("refused", str(exc))
        return BuildResult(ok=False, error=str(exc))

    async with get_session() as s:
        if await repo.get_scraper(s, scraper_name) is not None:
            msg = f"a scraper named {scraper_name!r} already exists"
            say("refused", msg)
            return BuildResult(ok=False, error=msg)

    transcript = settings.runs_dir / f"build-{scraper_name}" / "agent.jsonl"
    gateway = make_gateway(budget, transcript_path=transcript)
    request = BuildRequest(
        url=url, goal=goal, name=scraper_name, target_schema=output_schema
    )

    configured = rung_for(None, settings)
    ladder = list(BUILD_LADDER)
    if configured not in ladder:
        ladder.insert(1, configured)
    result = BuildResult(ok=False, error="the build never started")
    blocked_by: str | None = None       # the first refusal, which is the real cause
    unavailable: list[str] = []         # rungs that could not even be opened

    for attempt, rung in enumerate(ladder, start=1):
        if attempt > 1:
            say("escalating", f"{rung}, because {ladder[attempt - 2]} was refused")
        say("opening a browser", rung)
        try:
            async with open_engine(
                rung, settings=settings, artifacts_dir=transcript.parent
            ) as engine:
                say("exploring the page", url)
                result = await build(
                    request,
                    gateway=gateway,
                    engine=engine,
                    settings=settings,
                    test_run=build_trial(settings, scraper_name),
                    on_event=on_event,
                )
        except Exception as exc:  # noqa: BLE001 - includes EngineUnavailable
            detail = f"{type(exc).__name__}: {exc}"
            if "Unavailable" in type(exc).__name__ or "not installed" in str(exc):
                # An engine that is not installed is not a scraping failure. Note
                # it, keep the real cause, and carry on up the ladder.
                say("unavailable", f"{rung}: {exc}")
                unavailable.append(f"{rung} ({exc})")
                if attempt < len(ladder):
                    continue
                result = BuildResult(ok=False, error=blocked_by or detail)
                break
            result = BuildResult(ok=False, error=detail)

        if result.ok:
            break
        evidence = " ".join([result.error or "", result.text or ""])
        if not engine_is_the_problem(evidence):
            break
        blocked_by = blocked_by or result.error
        if looks_blocked(evidence):
            say("blocked", f"the site refused {rung}")
        else:
            say("too thin", f"{rung} did not render enough of the page")
        if attempt == len(ladder):
            break

    if not result.ok and blocked_by:
        # Report the refusal, not whatever the last attempt happened to raise.
        parts = [f"the site refused this browser: {blocked_by}"]
        tried = [r for r in ladder if f"{r} (" not in " ".join(unavailable)]
        if len(tried) > 1:
            parts.append(f"tried {', '.join(tried)}")
        if unavailable:
            parts.append(f"could not escalate to {'; '.join(unavailable)}")
        result.error = ". ".join(parts)

    scraper_id = None
    if result.ok and result.script is not None:
        say("saving", f"{result.row_count} rows from the test run")
        scraper_id = await persist_build(result, url=url, goal=goal, name=scraper_name)
    await record_usage(result, agent=BUILDER, scraper_id=scraper_id, run_id=None)
    if result.ok:
        say("done", f"{scraper_name} is ready")
    else:
        say("failed", result.error or "the builder did not produce a working script")
    result.scraper_id = scraper_id
    return result


# --------------------------------------------------------------------------- repair
async def _failed_run(s, scraper_id: int, run_id: int | None) -> Run | None:
    if run_id is not None:
        return await s.get(Run, run_id)
    return await s.scalar(
        select(Run)
        .where(
            Run.scraper_id == scraper_id,
            Run.status.in_([RunStatus.VALIDATION_FAILED, RunStatus.ERROR, RunStatus.BLOCKED]),
        )
        .order_by(desc(Run.created_at))
    )


def snapshot_of(run: Run, url: str) -> PageSnapshot | None:
    """The page the failed run captured, if the runner kept one."""
    if not run.artifact_dir:
        return None
    page = Path(run.artifact_dir) / "page.html"
    if not page.is_file():
        return None
    return PageSnapshot(
        url=url,
        title="",
        accessibility_tree="",
        html=page.read_text(encoding="utf-8", errors="replace"),
    )


def may_auto_promote(policy: str, *, is_minor: bool, has_custom_python: bool) -> tuple[bool, str]:
    """The promotion decision, as a pure function so it can be read and tested.

    One rule outranks every policy: a version that introduces unsandboxed Python
    is never promoted without a human, because the promotion policy was set
    before anyone knew this version would contain it.
    """
    if has_custom_python:
        return False, "contains custom_python, which always needs human approval"
    if policy == PromotionPolicy.AUTO:
        return True, "policy is auto"
    if policy == PromotionPolicy.AUTO_IF_MINOR and is_minor:
        return True, "policy is auto_if_minor and the change is minor"
    if policy == PromotionPolicy.AUTO_IF_MINOR:
        return False, "policy is auto_if_minor and the change is not minor"
    return False, "policy is manual"


async def persist_repair(
    result: RepairResult,
    *,
    scraper_id: int,
    run_id: int | None,
    trial_run_id: int | None,
    report: ValidatorReport | None,
) -> tuple[int, bool]:
    """Store the candidate and the repair row, and promote it when allowed."""
    settings = get_settings()
    candidate = result.candidate
    assert candidate is not None

    reason = report.summary() if report is not None else "the run failed"
    if result.capability_gaps:
        gaps = ", ".join(sorted(set(result.capability_gaps)))
        reason += (
            f"\n\nThe repair agent could not {gaps} on this engine. "
            "The page needs a browser rung; escalate the scraper before trusting this candidate."
        )

    async with get_session() as s:
        scraper = await repo.get_scraper(s, scraper_id)
        # Read the outgoing active version BEFORE adding the candidate. Once the
        # candidate is in the session, `active_version` orders by version and
        # would hand back the candidate itself, leaving the old version active
        # alongside it and giving the runner two actives to choose from.
        previous = await repo.active_version(s, scraper_id)
        promote, why = may_auto_promote(
            scraper.promotion_policy,
            is_minor=result.is_minor,
            has_custom_python=candidate.has_custom_python,
        )
        # A capability gap means the agent was working half blind. Do not let a
        # policy promote a candidate produced under that condition.
        if promote and result.capability_gaps:
            promote, why = False, "the engine could not interact with the page"

        version = ScriptVersion(
            scraper_id=scraper_id,
            version=candidate.version,
            yaml=candidate.to_yaml(),
            created_by=Author.REPAIR,
            change_summary="; ".join(result.reasons[:6]) or "selector repair",
            rationale=result.rationale[:4000],
            status=VersionStatus.ACTIVE if promote else VersionStatus.CANDIDATE,
            is_minor=result.is_minor,
            has_custom_python=candidate.has_custom_python,
            output_schema=candidate.output_schema,
            test_run_id=trial_run_id,
        )
        s.add(version)

        repair_row = Repair(
            scraper_id=scraper_id,
            run_id=run_id,
            candidate_version=candidate.version,
            status="auto_promoted" if promote else "pending_approval",
            reason=reason,
            diff=result.diff,
            is_minor=result.is_minor,
            test_run_id=trial_run_id,
            transcript_path=str(result.transcript_path) if result.transcript_path else None,
        )
        s.add(repair_row)

        if promote:
            if previous is not None and previous.version != candidate.version:
                previous.status = VersionStatus.RETIRED
            version.approved_by = "auto"
            version.approved_at = datetime.now(UTC)
            path = Path(scraper.yaml_path)
            if not path.is_absolute():
                path = settings.scrapers_dir / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(candidate.to_yaml(), encoding="utf-8")

        await repo.log(
            s,
            actor=REPAIRER,
            action="promoted repair" if promote else "parked repair for approval",
            object_type="scraper",
            object_ref=scraper.name,
            detail=f"v{candidate.version}: {why}",
            meta={"is_minor": result.is_minor, "gaps": sorted(set(result.capability_gaps))},
        )
        await s.flush()
        return repair_row.id, promote


async def repair_scraper(scraper_id: int, run_id: int | None = None) -> RepairResult:
    """Propose a new script version for a scraper whose runs are failing."""
    settings = get_settings()

    async with get_session() as s:
        scraper = await repo.get_scraper(s, scraper_id)
        if scraper is None:
            return RepairResult(ok=False, error=f"no scraper with id {scraper_id}")
        active = await repo.active_version(s, scraper_id)
        if active is None:
            return RepairResult(ok=False, error=f"{scraper.name} has no active script version")
        failed = await _failed_run(s, scraper_id, run_id)
        if failed is None:
            return RepairResult(
                ok=False, error=f"{scraper.name} has no failed run to repair against"
            )
        failed_id = failed.id
        report = (
            _report_of(failed.validator_report)
            or ValidatorReport(passed=False, row_count=failed.row_count)
        )
        snapshot = snapshot_of(failed, scraper.url)
        sample = [r.data for r in await repo.get_records(s, scraper_id=scraper_id, limit=5)]
        script = ScrapeScript.from_yaml(active.yaml)
        scraper_name, scraper_proxy, scraper_profile = (
            scraper.name,
            scraper.proxy_pool,
            scraper.profile,
        )

    try:
        budget = await remaining_budget(scraper_id, settings)
    except BudgetRefused as exc:
        return RepairResult(ok=False, error=str(exc))

    state: dict[str, Any] = {"candidate_version": script.version + 1}
    transcript = settings.runs_dir / f"repair-{scraper_name}-{failed_id}" / "agent.jsonl"
    gateway = make_gateway(budget, transcript_path=transcript)
    request = RepairRequest(
        script=script,
        report=report,
        snapshot=snapshot,
        last_good_sample=sample,
        name=scraper_name,
    )

    try:
        async with open_engine(
            rung_for(script, settings),
            settings=settings,
            proxy=scraper_proxy,
            profile=scraper_profile,
            artifacts_dir=transcript.parent,
        ) as engine:
            result = await repair(
                request,
                gateway=gateway,
                engine=engine,
                settings=settings,
                test_run=repair_trial(settings, scraper, state),
            )
    except Exception as exc:  # noqa: BLE001 - includes EngineUnavailable
        result = RepairResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    if result.ok and result.candidate is not None:
        await persist_repair(
            result,
            scraper_id=scraper_id,
            run_id=failed_id,
            trial_run_id=state.get("trial_run_id"),
            report=report,
        )
    await record_usage(result, agent=REPAIRER, scraper_id=scraper_id, run_id=failed_id)
    return result


def _report_of(stored: Any) -> ValidatorReport | None:
    from .. import pipeline

    if isinstance(stored, dict) and stored.get("rules") is not None:
        return pipeline.report_from_dict(stored)
    return None
