"""Climb the ladder until a rung works, and say which one did.

`ScrapeScript.escalation` is an ordered list of rungs, cheapest first. A run
starts on the first one and moves up only for failures a better rung could
plausibly fix. The distinction is the whole value of this module:

* **Blocked**, **EngineUnavailable**, **InteractionUnsupported** — climb. The
  site refused us, or this rung cannot do what the script needs.
* **SelectorNotFound** / **ValidationFailed** — climb *only when leaving the
  HTTP rung*. A selector that finds nothing in static HTML very often finds
  everything once JavaScript has run, and the block detector cannot see a page
  that renders half its content. Above the HTTP rung the same failure means the
  script is wrong, and retrying it in a second browser just costs time.
* **StepError**, and anything else — stop. Nothing about the rung caused it.

The rung that succeeded is recorded on the outcome as `escalation_level`, and
every attempt is logged, so a scraper that has quietly needed a proxy for three
weeks is visible rather than merely expensive.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smartscraper.contracts import RunOutcome
from smartscraper.dsl.models import Engine as DslEngine
from smartscraper.dsl.models import Rung, ScrapeScript
from smartscraper.runner.artifacts import RunArtifacts
from smartscraper.runner.engines.base import InteractionUnsupported
from smartscraper.runner.engines.http import HttpEngine
from smartscraper.runner.errors import (
    Blocked,
    EngineUnavailable,
    SelectorNotFound,
    StepError,
    ValidationFailed,
)
from smartscraper.runner.runlog import RunLogger, null_logger
from smartscraper.runner.steps import StepContext, run_steps

#: Which rung a declared `engine:` corresponds to, so a script that names an
#: engine starts there instead of on HTTP.
ENGINE_RUNG: dict[str, Rung] = {
    DslEngine.HTTP: Rung.HTTP,
    DslEngine.PATCHRIGHT: Rung.PATCHRIGHT,
    DslEngine.PLAYWRIGHT: Rung.PATCHRIGHT,
    DslEngine.CAMOUFOX: Rung.CAMOUFOX,
}

BROWSER_RUNGS: frozenset[str] = frozenset(
    {Rung.PATCHRIGHT, Rung.PATCHRIGHT_PROXY, Rung.CAMOUFOX}
)


@dataclass(slots=True)
class Attempt:
    """One rung, tried once."""

    level: int
    rung: str
    engine: str
    ok: bool
    rows: int = 0
    blocked: bool = False
    block_reason: str | None = None
    error: str | None = None
    error_kind: str | None = None
    proxy: str | None = None
    ms: float = 0.0


@dataclass(slots=True)
class LadderResult:
    outcome: RunOutcome
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def succeeded_on(self) -> Attempt | None:
        return next((a for a in self.attempts if a.ok), None)

    def summary(self) -> str:
        won = self.succeeded_on
        if won is None:
            last = self.attempts[-1] if self.attempts else None
            reason = (last.block_reason or last.error) if last else "no rung attempted"
            return f"every rung failed ({len(self.attempts)} tried): {reason}"
        tried = " → ".join(a.rung for a in self.attempts)
        return f"passed on rung {won.level} ({won.rung}) after {tried}, {won.rows} rows"


def rungs_for(script: ScrapeScript) -> list[Rung]:
    """The ladder this script will actually climb.

    A script that names a concrete `engine:` skips every rung cheaper than that
    engine, because the builder already decided the cheap ones do not work.
    """
    ladder = list(script.escalation) or [Rung.HTTP, Rung.PATCHRIGHT]
    floor = ENGINE_RUNG.get(script.engine)
    if floor is None:
        return ladder
    if floor in ladder:
        return ladder[ladder.index(floor) :]
    return [floor, *[r for r in ladder if r != floor]]


def build_engine(
    rung: str,
    *,
    artifacts_dir: Path | None = None,
    headed: bool = False,
    persistent: bool = False,
) -> tuple[Any, str | None]:
    """`(engine, proxy_role)` for one rung. `proxy_role` is 'script' when this
    rung wants the script's configured proxy, None when it must not use one."""
    if rung == Rung.HTTP:
        return HttpEngine(), None
    if rung in (Rung.PATCHRIGHT, Rung.PATCHRIGHT_PROXY, Rung.CAMOUFOX):
        from smartscraper.runner.engines.browser import BrowserEngine

        name = "camoufox" if rung == Rung.CAMOUFOX else "patchright"
        engine = BrowserEngine(
            engine=name, artifacts_dir=artifacts_dir, persistent=persistent,
        )
        return engine, ("script" if rung != Rung.PATCHRIGHT else None)
    if rung == Rung.BYPARR:
        raise EngineUnavailable(
            "the byparr rung is not implemented; PLAN.md puts the sidecar in Phase 8"
        )
    raise EngineUnavailable(f"unknown escalation rung {rung!r}")


def _retryable(exc: BaseException, *, rung: str, has_next: bool) -> bool:
    if not has_next:
        return False
    if isinstance(exc, (Blocked, EngineUnavailable, InteractionUnsupported)):
        return True
    return isinstance(exc, (SelectorNotFound, ValidationFailed)) and rung == Rung.HTTP


async def escalate(
    script: ScrapeScript,
    *,
    log: RunLogger | None = None,
    artifacts: RunArtifacts | None = None,
    secrets: dict[str, str] | None = None,
    proxy: str | None = None,
    headed: bool = False,
    engine_factory: Any | None = None,
) -> LadderResult:
    """Run the script, climbing rungs on retryable failures.

    `engine_factory(rung, artifacts_dir=..., headed=...) -> (engine, proxy_role)`
    can be injected, which is how the tests drive the whole ladder without a
    browser binary.
    """
    log = log or null_logger()
    secrets = secrets or {}
    factory = engine_factory or build_engine
    ladder = rungs_for(script)
    attempts: list[Attempt] = []
    log.info("escalation", f"ladder: {' -> '.join(ladder)}", rungs=[str(r) for r in ladder])

    last_outcome: RunOutcome | None = None
    for level, rung in enumerate(ladder):
        has_next = level + 1 < len(ladder)
        started = time.monotonic()
        engine: Any = None
        failure: BaseException | None = None
        fatal = False
        attempt = Attempt(level=level, rung=str(rung), engine="?", ok=False)
        try:
            engine, proxy_role = factory(
                rung, artifacts_dir=(artifacts.dir if artifacts else None), headed=headed
            )
            attempt.engine = getattr(engine, "name", str(rung))
            use_proxy = proxy if proxy_role == "script" else None
            attempt.proxy = use_proxy
            log.info("escalation", f"rung {level}: {rung}", level=level, rung=str(rung),
                     engine=attempt.engine, proxy=bool(use_proxy))
            await engine.open(proxy=use_proxy, profile=script.profile, headed=headed)

            ctx = StepContext(script=script, engine=engine, log=log, artifacts=artifacts,
                              secrets=secrets)
            rows = await run_steps(script, ctx)
            attempt.ok = True
            attempt.rows = len(rows)
            attempt.ms = round((time.monotonic() - started) * 1000, 1)
            attempts.append(attempt)
            last_outcome = RunOutcome(
                rows=rows, engine_used=attempt.engine, escalation_level=level,
                proxy_used=use_proxy, steps=ctx.step_log,
            )
            await _capture(engine, artifacts, failed=False)
            log.info("escalation", f"rung {level} ({rung}) passed with {len(rows)} rows",
                     level=level, rung=str(rung), rows=len(rows))
            return LadderResult(outcome=last_outcome, attempts=attempts)
        except Blocked as exc:
            failure = exc
            attempt.blocked = True
            attempt.block_reason = exc.reason
        except (StepError, ValidationFailed, EngineUnavailable, InteractionUnsupported) as exc:
            failure = exc
        except Exception as exc:  # unexpected: record it and stop, do not climb
            failure = exc
            fatal = True

        attempt.error = str(failure)
        attempt.error_kind = type(failure).__name__
        attempt.ms = round((time.monotonic() - started) * 1000, 1)
        attempts.append(attempt)
        last_outcome = RunOutcome(
            rows=[], engine_used=attempt.engine, escalation_level=level,
            proxy_used=attempt.proxy, blocked=attempt.blocked,
            block_reason=attempt.block_reason,
            error=f"{attempt.error_kind}: {attempt.error}",
        )
        await _capture(engine, artifacts, failed=True)
        level_of = log.error if fatal else log.warn
        level_of("escalation", f"rung {level} ({rung}) failed: {attempt.error}",
                 level=level, rung=str(rung), kind=attempt.error_kind, blocked=attempt.blocked)
        if fatal or not _retryable(failure, rung=str(rung), has_next=has_next):
            break

    outcome = last_outcome or RunOutcome(
        rows=[], engine_used="", escalation_level=0, error="no rung was attempted"
    )
    return LadderResult(outcome=outcome, attempts=attempts)


async def _capture(engine: Any, artifacts: RunArtifacts | None, *, failed: bool) -> None:
    """Save the page, a screenshot, and on failure the trace and HAR."""
    if engine is None:
        return
    try:
        if artifacts is not None:
            with contextlib.suppress(Exception):
                artifacts.save_html(await engine.content())
            with contextlib.suppress(Exception):
                await engine.screenshot(artifacts.screenshot_path, full_page=True)
        finisher = getattr(engine, "finish", None)
        if finisher is not None:
            kept = await finisher(failed=failed)
            if artifacts is not None:
                for name, path in (kept or {}).items():
                    artifacts.adopt(Path(path), "trace.zip" if name == "trace" else "network.har")
        else:
            await engine.close()
    except Exception:
        pass
