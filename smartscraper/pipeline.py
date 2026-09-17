"""The seam between a finished run and its verdict.

This exists because three subsystems each held half of it: the runner produces
rows, the validator judges them, and the scheduler owns the database. Nobody
owned the join, so the scheduler was resolving `validate` by name and calling it
with the wrong arguments, which silently marked every validated run `error`.

Callers should import from here explicitly. Do not look functions up by name.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper import repo
from smartscraper.config import ROOT, get_settings
from smartscraper.contracts import ValidatorReport
from smartscraper.db.models import (
    Record,
    Run,
    RunMetric,
    RunStatus,
    Scraper,
    ScriptVersion,
    VersionStatus,
)
from smartscraper.dsl.models import ScrapeScript

log = logging.getLogger(__name__)

# Statuses the validator must not overwrite: the run never got far enough to be
# judged, so its own failure is the more useful answer. `running` is NOT here on
# purpose: a run is still `running` at the moment the runner hands back its rows,
# and that is precisely the state validation transitions out of.
UNJUDGEABLE: frozenset[str] = frozenset(
    {RunStatus.ERROR, RunStatus.BLOCKED, RunStatus.CANCELLED}
)


def _resolve_script_path(yaml_path: str) -> Path | None:
    """Turn a stored `Scraper.yaml_path` into a real file, or None.

    `yaml_path` is written as a bare filename relative to `settings.scrapers_dir`
    (see agents/jobs.py), so resolving it against the project root silently misses
    every time and defeats the fallback this exists for. Both roots are tried, in
    the order of what is actually written, so a path stored either way resolves.
    """
    p = Path(yaml_path)
    if p.is_absolute():
        return p if p.exists() else None
    for base in (get_settings().scrapers_dir, ROOT):
        candidate = base / p
        if candidate.exists():
            return candidate
    return None


def report_to_dict(report: ValidatorReport) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "row_count": report.row_count,
        "summary": report.summary(),
        "rules": [asdict(r) for r in report.rules],
        "metrics": [asdict(m) for m in report.metrics],
    }


def report_from_dict(d: dict[str, Any]) -> ValidatorReport:
    from smartscraper.contracts import FieldMetric, RuleResult

    return ValidatorReport(
        passed=bool(d.get("passed")),
        row_count=int(d.get("row_count", 0)),
        rules=[RuleResult(**r) for r in d.get("rules", [])],
        metrics=[FieldMetric(**m) for m in d.get("metrics", [])],
    )


async def load_script(s: AsyncSession, run: Run) -> ScrapeScript | None:
    """The script a run executed, or None when it cannot be recovered.

    Never raises. A version row holding YAML that no longer parses, or a missing
    version row, must not take the worker down: the run simply cannot be judged,
    which is a different outcome from failing validation.

    Falls back to the YAML on disk, because `scrapers/*.yaml` is the source of
    truth and the database only mirrors it. A version row can be missing after a
    restore or a hand edit while the file is still right there.
    """
    sv = await s.scalar(
        select(ScriptVersion).where(
            ScriptVersion.scraper_id == run.scraper_id,
            ScriptVersion.version == run.script_version,
        )
    )
    if sv is not None:
        try:
            return ScrapeScript.from_yaml(sv.yaml)
        except Exception:
            log.warning(
                "script_version %s of scraper %s does not parse; trying the file on disk",
                run.script_version, run.scraper_id,
            )

    scraper = await s.get(Scraper, run.scraper_id)
    if scraper is None or not scraper.yaml_path:
        return None
    path = _resolve_script_path(scraper.yaml_path)
    if path is None:
        return None
    try:
        return ScrapeScript.from_yaml(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("%s does not parse either; this run cannot be judged", path)
        return None


async def persist_report(s: AsyncSession, run: Run, report: ValidatorReport) -> None:
    """Write the verdict onto the run. Safe to call with a report the runner computed."""
    run.validator_report = report_to_dict(report)
    run.row_count = report.row_count

    for old in await repo.run_metrics(s, run.id):
        await s.delete(old)
    for m in report.metrics:
        s.add(
            RunMetric(
                run_id=run.id, field=m.field, null_rate=m.null_rate,
                distinct_count=m.distinct_count, sample=m.sample,
            )
        )

    if run.status not in UNJUDGEABLE:
        run.status = RunStatus.PASSED if report.passed else RunStatus.VALIDATION_FAILED
    if run.finished_at is None:
        run.finished_at = datetime.now(UTC)

    await repo.log(
        s, actor="system",
        action="passed validation" if report.passed else "failed validation",
        object_type="run", object_ref=f"#{run.id}", detail=report.summary(),
    )


async def validate_run(s: AsyncSession, run_id: int) -> ValidatorReport | None:
    """Judge a stored run: load its rows and script, validate, persist.

    Returns None when the run cannot be judged, which is not the same as a
    failure: a blocked or errored run never produced rows to judge.
    """
    from smartscraper.validate import validate

    run = await s.get(Run, run_id)
    if run is None:
        return None

    # A run that was blocked or errored never produced rows worth judging. Calling
    # that a validation failure would send a repair agent after a selector that is
    # fine, so say "not applicable" rather than "failed".
    if run.status in UNJUDGEABLE:
        return None

    if isinstance(run.validator_report, dict) and run.validator_report.get("rules"):
        return report_from_dict(run.validator_report)

    script = await load_script(s, run)
    if script is None:
        return None

    rows = [
        r.data for r in (await s.scalars(
            select(Record).where(Record.run_id == run_id).order_by(Record.id)
        )).all()
    ]
    history = [
        c for c in await repo.recent_row_counts(
            s, run.scraper_id, n=5, exclude_run_id=run_id
        ) if c is not None
    ]

    report = validate(rows, script, history)
    await persist_report(s, run, report)
    return report


async def promote_version(
    s: AsyncSession, scraper_id: int, version: int, *, approved_by: str = "you"
) -> Path | None:
    """Make one script version active, everywhere.

    Two stores hold the script: the `script_version` table and `scrapers/*.yaml`,
    which PLAN.md calls the source of truth. Promoting in only one leaves them
    disagreeing, and the disagreement is silent. This does both, so approving a
    repair from the web UI, from MCP, or from the repair agent itself all land in
    the same state.

    Returns the file written, or None when the scraper has no yaml_path.
    """
    target = await s.scalar(
        select(ScriptVersion).where(
            ScriptVersion.scraper_id == scraper_id, ScriptVersion.version == version
        )
    )
    if target is None:
        raise LookupError(f"scraper {scraper_id} has no version {version}")

    previous = (await s.scalars(
        select(ScriptVersion).where(
            ScriptVersion.scraper_id == scraper_id,
            ScriptVersion.status == VersionStatus.ACTIVE,
            ScriptVersion.version != version,
        )
    )).all()
    for old in previous:
        old.status = VersionStatus.RETIRED

    target.status = VersionStatus.ACTIVE
    target.approved_by = approved_by
    target.approved_at = datetime.now(UTC)

    scraper = await s.get(Scraper, scraper_id)
    written: Path | None = None
    if scraper is not None and scraper.yaml_path:
        path = _resolve_script_path(scraper.yaml_path)
        if path is None:
            base = get_settings().scrapers_dir
            base.mkdir(parents=True, exist_ok=True)
            path = base / Path(scraper.yaml_path).name
        path.write_text(target.yaml, encoding="utf-8")
        written = path

    await repo.log(
        s, actor=approved_by, action="promoted version",
        object_type="scraper", object_ref=scraper.name if scraper else str(scraper_id),
        detail=f"v{version} active; {len(previous)} retired; file {written or 'not written'}",
    )
    return written


def parse_runner_report(last_json_line: str) -> ValidatorReport | None:
    """Pull the verdict out of the runner subprocess's final stdout line."""
    try:
        payload = json.loads(last_json_line)
    except (ValueError, TypeError):
        return None
    d = payload.get("validator_report") or payload.get("report")
    return report_from_dict(d) if isinstance(d, dict) and d.get("rules") is not None else None
