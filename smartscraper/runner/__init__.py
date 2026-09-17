"""The deterministic runner: execute a `ScrapeScript`, produce rows and artifacts.

No model is called from anywhere in this package. That is the design: a build
costs tokens once, and every scheduled run after it costs nothing but bandwidth.

    from smartscraper.runner import execute
    outcome = await execute(script, run_id=17, out_dir=None, secrets={})

`execute` opens the run directory, climbs the escalation ladder, and returns a
`RunOutcome`. The scheduler does not import this module: it spawns
`python -m smartscraper.runner.cli` so a hung browser cannot take the worker
down with it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from smartscraper.contracts import RunOutcome
from smartscraper.dsl.models import ScrapeScript
from smartscraper.runner.artifacts import RunArtifacts, outcome_to_dict
from smartscraper.runner.block_detect import BlockVerdict, detect_block
from smartscraper.runner.errors import (
    Blocked,
    EngineUnavailable,
    RunnerError,
    SelectorNotFound,
    StepError,
    ValidationFailed,
)
from smartscraper.runner.escalation import LadderResult, escalate
from smartscraper.runner.locators import Selector, parse_selector, resolve_first
from smartscraper.runner.probe import probe_engine, probe_with_fallbacks
from smartscraper.runner.runlog import RunLogger, stdout_logger

__all__ = [
    "Blocked",
    "BlockVerdict",
    "EngineUnavailable",
    "LadderResult",
    "RunArtifacts",
    "RunLogger",
    "RunOutcome",
    "RunnerError",
    "SelectorNotFound",
    "Selector",
    "StepError",
    "ValidationFailed",
    "detect_block",
    "escalate",
    "execute",
    "execute_sync",
    "outcome_to_dict",
    "parse_resolve",
    "parse_selector",
    "probe_engine",
    "probe_with_fallbacks",
    "resolve_first",
]


async def execute(
    script: ScrapeScript,
    *,
    run_id: int | str,
    out_dir: Path | str | None = None,
    secrets: Mapping[str, str] | None = None,
    proxy: str | None = None,
    headed: bool = False,
    log: RunLogger | None = None,
    stream: bool = False,
) -> RunOutcome:
    """Run one script end to end and leave every artifact on disk.

    `out_dir` defaults to `data/runs/<run_id>/`. `stream=True` also writes the
    structured log to stdout, which is what the CLI wants and what an in-process
    caller usually does not.
    """
    artifacts = RunArtifacts(run_id, out_dir)
    owns_log = log is None
    logger = log or (
        stdout_logger(run_id=_as_int(run_id), path=artifacts.log_path)
        if stream
        else RunLogger(stream=None, path=artifacts.log_path, run_id=_as_int(run_id))
    )
    try:
        logger.info("run", f"starting run {run_id}", run_id=str(run_id), dir=str(artifacts.dir),
                    steps=len(script.steps), engine=str(script.engine))
        if script.has_custom_python:
            logger.warn("run", "this script contains custom_python: unsandboxed code will run")
        result = await escalate(
            script, log=logger, artifacts=artifacts, secrets=dict(secrets or {}),
            proxy=proxy, headed=headed,
        )
        outcome = result.outcome
        outcome.artifacts = {name: path for name, path in artifacts.existing().items()}
        artifacts.save_outcome(outcome)
        logger.info("run", result.summary(), rows=len(outcome.rows),
                    engine=outcome.engine_used, level=outcome.escalation_level,
                    blocked=outcome.blocked, attempts=len(result.attempts))
        return outcome
    finally:
        artifacts.close()
        if owns_log:
            logger.close()


def execute_sync(script: ScrapeScript, **kwargs: Any) -> RunOutcome:
    """`execute` for callers that are not already in an event loop."""
    return asyncio.run(execute(script, **kwargs))


async def parse_resolve(engine: Any, selector: str, fallbacks: list[str] | None = None):
    """Convenience for the builder's probe tool: which candidate matches here?"""
    return await resolve_first(selector, fallbacks, engine.count)


def _as_int(run_id: int | str) -> int | None:
    try:
        return int(run_id)
    except (TypeError, ValueError):
        return None
