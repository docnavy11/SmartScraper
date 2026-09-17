"""Huey tasks. Everything the worker actually does lives here.

A run is a **subprocess**, never an in-worker call. A hung browser or a
segfaulting engine then costs one run, not the worker, and the hard timeout is
a kill rather than a hope. The child speaks one JSON object per line on stdout
(see ``smartscraper.runner.runlog``); those lines are appended to the run's
``log.txt`` as they arrive, so the UI can tail them live, and the last
``tag: "result"`` line updates the Run row.

Agent and runner modules are imported lazily inside the tasks, so this module
imports cleanly while those subsystems are still being written.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smartscraper import repo
from smartscraper.config import get_settings
from smartscraper.db.models import Run, RunStatus, Scraper
from smartscraper.db.session import get_session
from smartscraper.scheduler.huey_app import huey

log = logging.getLogger(__name__)

#: Module executed as the run subprocess. Owned by the runner subsystem.
RUNNER_MODULE = "smartscraper.runner.cli"

#: Seconds between SIGTERM and SIGKILL when a run overruns its timeout.
KILL_GRACE_S = 5.0

#: The child gets ``run_timeout_s`` and stops itself cleanly; the parent's hard
#: kill sits this many seconds later, so a normal overrun still produces a
#: parseable outcome line and only a wedged child is killed.
KILL_MARGIN_S = 30.0

#: The runner's exit codes, mirroring smartscraper/runner/cli.py. This is the
#: process boundary contract: it is read here, not imported, because importing
#: the runner would pull Playwright into the worker process.
EXIT_STATUS = {
    0: RunStatus.PASSED,
    1: RunStatus.VALIDATION_FAILED,
    2: RunStatus.BLOCKED,
    3: RunStatus.ERROR,
}

#: Statuses a runner may name explicitly, mapped onto the RunStatus vocabulary.
_STATUS_ALIASES = {
    "ok": RunStatus.PASSED, "success": RunStatus.PASSED, "passed": RunStatus.PASSED,
    "failed": RunStatus.ERROR, "error": RunStatus.ERROR, "exception": RunStatus.ERROR,
    "blocked": RunStatus.BLOCKED, "cancelled": RunStatus.CANCELLED,
    "validation_failed": RunStatus.VALIDATION_FAILED,
}


# --------------------------------------------------------------------------- command
def script_path(scraper: Scraper) -> Path:
    """The YAML on disk, which is the source of truth for what a run executes."""
    path = Path(scraper.yaml_path)
    return path if path.is_absolute() else get_settings().scrapers_dir / path


#: How many recent row counts the row-count band rule gets. The runner cannot
#: fetch these itself: only the scheduler has the database.
HISTORY_RUNS = 5


def runner_command(
    run_id: int,
    scraper: Scraper,
    version: int | None = None,
    *,
    out_dir: Path | None = None,
    timeout_s: float | None = None,
    history: list[int] | None = None,
) -> list[str]:
    """Argv for the run subprocess.

    ``SS_RUNNER_CMD`` (a JSON list) replaces it wholesale; the tests use that to
    stand a trivial child in for the real runner.
    """
    override = os.environ.get("SS_RUNNER_CMD")
    if override:
        return [str(a) for a in json.loads(override)]
    settings = get_settings()
    cmd = [
        sys.executable, "-m", RUNNER_MODULE,
        "--script", str(script_path(scraper)),
        "--run-id", str(run_id),
    ]
    if out_dir is not None:
        cmd += ["--out", str(out_dir)]
    if timeout_s is not None:
        cmd += ["--timeout", str(timeout_s)]
    if history:
        # Most recent first. Without it the row-count band rule skips rather
        # than failing, so an empty history is passed as no flag at all.
        cmd += ["--history", ",".join(str(int(c)) for c in history)]
    if settings.headed_effective:
        cmd.append("--headed")
    return cmd


def run_dir(run_id: int) -> Path:
    path = get_settings().runs_dir / str(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- subprocess
class RunResult(dict):
    """The parsed last line of the child's output, plus how it exited."""


async def _pump(stream: asyncio.StreamReader, fh: Any, collect: list[dict[str, Any]],
                raw: list[str], *, is_stderr: bool = False) -> None:
    """Copy one child stream into the log file, parsing JSON lines as they pass."""
    while True:
        try:
            line = await stream.readline()
        except (ValueError, asyncio.LimitOverrunError):
            # An absurdly long line: drop it rather than kill the run.
            continue
        if not line:
            return
        text = line.decode("utf-8", "replace").rstrip("\n")
        if not text:
            continue
        if is_stderr:
            raw.append(text)
            record = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                      "level": "error", "tag": "stderr", "msg": text[:2000]}
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            continue
        fh.write(text + "\n")
        fh.flush()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raw.append(text)
            continue
        if isinstance(parsed, dict):
            collect.append(parsed)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the child's whole process group, then SIGKILL what is left.

    The group matters: the runner spawns a browser, and killing only the Python
    parent leaves Chromium behind holding the profile lock.
    """
    if proc.returncode is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    with contextlib.suppress(ProcessLookupError, OSError):
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_S)
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError, OSError):
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_S)


async def execute_subprocess(
    command: list[str], log_path: Path, *, timeout_s: float, cwd: Path | None = None,
) -> RunResult:
    """Run the child, stream its output into ``log_path``, enforce the timeout."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[dict[str, Any]] = []
    raw: list[str] = []
    timed_out = False

    with log_path.open("a", encoding="utf-8") as fh:
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd else None,
                start_new_session=True,  # own process group, so we can kill the tree
                limit=1 << 20,
            )
        except (OSError, ValueError) as exc:
            return RunResult(returncode=None, lines=[], raw=[f"{type(exc).__name__}: {exc}"],
                             timed_out=False, spawn_error=f"{type(exc).__name__}: {exc}")

        pumps = [
            asyncio.create_task(_pump(proc.stdout, fh, lines, raw)),
            asyncio.create_task(_pump(proc.stderr, fh, lines, raw, is_stderr=True)),
        ]
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_s)
        except TimeoutError:
            timed_out = True
            await _kill(proc)
        finally:
            # Drain whatever the child already wrote; never wait on it forever.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.gather(*pumps, return_exceptions=True), timeout=5.0)
            for task in pumps:
                task.cancel()

    return RunResult(returncode=proc.returncode, lines=lines, raw=raw, timed_out=timed_out,
                     spawn_error=None)


def final_line(lines: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The run's verdict.

    The runner's last stdout line is tagged ``outcome`` and nests the payload
    under an ``outcome`` key; a ``result`` tag and a flat payload are also
    accepted, so a simpler child (or a test double) needs no ceremony.
    """
    for record in reversed(lines):
        if record.get("tag") in ("outcome", "result"):
            payload = record.get("outcome")
            if isinstance(payload, dict):
                return {**record, **payload}
            return record
    return lines[-1] if lines else None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def apply_result(run: Run, result: RunResult, *, timeout_s: float) -> None:
    """Write the child's outcome onto the Run row. Never raises."""
    run.finished_at = datetime.now(UTC)
    if run.started_at is not None:
        started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=UTC)
        run.duration_ms = int((run.finished_at - started).total_seconds() * 1000)

    tail = " | ".join(result.get("raw", [])[-5:])[:2000]

    if result.get("spawn_error"):
        run.status = RunStatus.ERROR
        run.error = f"could not start the runner: {result['spawn_error']}"
        return

    if result.get("timed_out"):
        run.status = RunStatus.ERROR
        run.error = f"run exceeded its {timeout_s:g}s timeout and was killed"
        if tail:
            run.error += f"; last output: {tail}"
        return

    line = final_line(result.get("lines") or [])
    returncode = result.get("returncode")
    if line is None:
        run.status = RunStatus.ERROR
        run.error = (f"the runner exited {returncode} without reporting an outcome"
                     + (f"; output: {tail}" if tail else ""))
        return

    run.status = _status_of(line, returncode)
    run.row_count = _int(line.get("row_count", line.get("rows_count", run.row_count)), run.row_count)
    if isinstance(line.get("rows"), list) and not line.get("row_count"):
        run.row_count = len(line["rows"])
    for attr, key in (("engine_used", "engine_used"), ("proxy_used", "proxy_used"),
                      ("block_reason", "block_reason"), ("artifact_dir", "artifact_dir")):
        if line.get(key):
            setattr(run, attr, str(line[key]))
    if line.get("escalation_level") is not None:
        run.escalation_level = _int(line["escalation_level"], run.escalation_level)
    if line.get("cost_usd") is not None:
        with contextlib.suppress(TypeError, ValueError):
            run.cost_usd = float(line["cost_usd"])
    if line.get("duration_ms") is not None:
        run.duration_ms = _int(line["duration_ms"], run.duration_ms or 0)
    error = line.get("error")
    problems = line.get("validation_problems") or []
    if not error and problems and run.status == RunStatus.VALIDATION_FAILED:
        error = "; ".join(str(p) for p in problems)
    if not error and returncode not in (0, None):
        error = f"the runner exited {returncode}"
        if tail:
            error += f"; output: {tail}"
    run.error = str(error)[:4000] if error else None
    if run.error and run.status == RunStatus.PASSED:
        run.status = RunStatus.ERROR


def _status_of(line: dict[str, Any], returncode: int | None) -> str:
    """Exit code first: it is the runner's verdict, and it is unambiguous."""
    named = _STATUS_ALIASES.get(str(line.get("status") or "").lower())
    if named is not None:
        return named
    if returncode in EXIT_STATUS:
        return EXIT_STATUS[returncode]
    if line.get("error"):
        return RunStatus.ERROR
    if line.get("blocked"):
        return RunStatus.BLOCKED
    return RunStatus.PASSED if returncode == 0 else RunStatus.ERROR


# --------------------------------------------------------------------------- records
def row_hash(row: dict[str, Any]) -> str:
    """Stable hash of one record, for dedupe across runs."""
    import hashlib

    canonical = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_records(artifact_dir: Path | str | None, fallback: list[dict[str, Any]] | None = None):
    """Rows the runner wrote to ``records.jsonl``, else whatever it put on stdout."""
    if artifact_dir:
        path = Path(artifact_dir) / "records.jsonl"
        if path.exists():
            rows: list[dict[str, Any]] = []
            for raw in path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("skipping an unparseable record line in %s", path)
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
            return rows
    return list(fallback or [])


async def persist_records(s: Any, run: Run, rows: list[dict[str, Any]], *, source: str = "script") -> int:
    """Write the run's rows into the record table, which is what delivery reads."""
    from smartscraper.db.models import Record

    for row in rows:
        s.add(Record(run_id=run.id, scraper_id=run.scraper_id, data=row,
                     source=source, row_hash=row_hash(row)))
    await s.flush()
    return len(rows)


# --------------------------------------------------------------------------- run
async def run_scraper_now(
    scraper_id: int,
    trigger: str = "manual",
    *,
    version: int | None = None,
    timeout_s: float | None = None,
    command: list[str] | None = None,
    followups: bool = True,
    adopt_run_id: int | None = None,
) -> int:
    """Create a Run, execute it as a subprocess, record the outcome. Returns the run id.

    ``timeout_s`` is the child's own wall clock. The parent kills at
    ``timeout_s + KILL_MARGIN_S``, so an ordinary overrun still writes a
    parseable outcome line and only a wedged child is killed outright.
    """
    settings = get_settings()
    soft_timeout = float(timeout_s if timeout_s is not None else settings.run_timeout_s)
    hard_timeout = soft_timeout + KILL_MARGIN_S if command is None else soft_timeout

    async with get_session() as s:
        scraper = await repo.get_scraper(s, scraper_id)
        if scraper is None:
            raise LookupError(f"scraper {scraper_id} does not exist")
        active = await repo.active_version(s, scraper.id)
        # Adopt a row somebody already queued, rather than creating a second one.
        # The web UI writes a `queued` Run so the person sees it immediately; this
        # is what turns that row into a real run instead of leaving it forever.
        run = await repo.get_run(s, adopt_run_id) if adopt_run_id else None
        if run is not None:
            run.status = RunStatus.RUNNING
            run.started_at = datetime.now(UTC)
            if version:
                run.script_version = version
        else:
            run = Run(
                scraper_id=scraper.id,
                script_version=version or (active.version if active else 1),
                status=RunStatus.RUNNING,
                trigger=trigger,
                started_at=datetime.now(UTC),
            )
            s.add(run)
        await s.flush()
        run_id = run.id
        directory = run_dir(run_id)
        run.artifact_dir = str(directory)
        run.log_path = str(directory / "log.txt")
        history = [c for c in await repo.recent_row_counts(s, scraper.id, n=HISTORY_RUNS)
                   if c is not None]
        cmd = command or runner_command(run_id, scraper, version, out_dir=directory,
                                        timeout_s=soft_timeout, history=history)
        log_path = Path(run.log_path)

    log.info("run %s: %s", run_id, " ".join(cmd))
    result = await execute_subprocess(cmd, log_path, timeout_s=hard_timeout)

    async with get_session() as s:
        run = await repo.get_run(s, run_id)
        if run is None:  # pragma: no cover - only if the row was deleted mid-run
            return run_id
        apply_result(run, result, timeout_s=hard_timeout)
        line = final_line(result.get("lines") or []) or {}
        rows = read_records(run.artifact_dir, line.get("rows"))
        if rows:
            await persist_records(s, run, rows)
            if not run.row_count:
                run.row_count = len(rows)
        await _store_runner_verdict(s, run, line)
        status = run.status
        await repo.log(s, actor="scheduler", action="run_finished", object_type="run",
                       object_ref=str(run_id), detail=f"{status}: {run.error or 'ok'}"[:500])

    if followups and status != RunStatus.CANCELLED:
        _enqueue(validate_run, run_id)
    return run_id


async def _store_runner_verdict(s: Any, run: Run, line: dict[str, Any]) -> bool:
    """Persist a ValidatorReport the runner computed itself, if it sent one.

    Today's runner only performs the cheap checks it can make without run
    history, so it sends ``validation_problems`` and no report, and this is a
    no-op. It is wired up so that a runner which does compute the full verdict
    does not have it recomputed, and so the metrics land either way.
    """
    from smartscraper.pipeline import parse_runner_report, persist_report

    if not line:
        return False
    report = parse_runner_report(json.dumps(line, default=str))
    if report is None:
        return False
    await persist_report(s, run, report)
    return True


def _enqueue(task: Any, *args: Any) -> Any:
    """Enqueue a Huey task, but do not let a queue problem lose the run."""
    try:
        return task(*args)
    except Exception:  # pragma: no cover - queue storage failure
        log.exception("could not enqueue %s%r", getattr(task, "name", task), args)
        return None


@huey.task(retries=0)
def run_scraper(scraper_id: int, trigger: str = "schedule") -> int:
    """Execute one scraper. The Huey entry point; the work is in run_scraper_now."""
    return asyncio.run(run_scraper_now(scraper_id, trigger))


# --------------------------------------------------------------------------- validate
async def validate_run_now(run_id: int) -> str:
    """Judge one finished run.

    The join between a run's rows and the validator's verdict lives in
    ``smartscraper.pipeline``, which owns loading the script, fetching the row
    count history, writing the run_metric rows and setting the status. This
    task only translates the outcome into a word for the queue's result log.
    """
    from smartscraper.pipeline import UNJUDGEABLE
    from smartscraper.pipeline import validate_run as judge

    async with get_session() as s:
        try:
            report = await judge(s, run_id)
        except Exception:
            # A script version that does not parse reaches here. The worker must
            # survive it; the run keeps whatever status the runner gave it.
            log.exception("validation of run %s raised", run_id)
            return "error"
        if report is not None:
            return "passed" if report.passed else "failed"
        run = await repo.get_run(s, run_id)
        if run is None:
            return "missing"
        if run.status in UNJUDGEABLE:
            # A blocked or errored run never produced rows worth judging.
            return "not_applicable"
        log.warning("run %s could not be judged: no script version to validate against", run_id)
        return "skipped"


@huey.task(retries=0)
def validate_run(run_id: int) -> str:
    """Validate, then hand the run to delivery whatever the verdict was.

    Delivery is queued even on a failure: a target marked ``only_on_failure``
    is the alert, and a target that opted into provisional rows still wants
    them. What a failure changes is that every other target is held.
    """
    outcome = asyncio.run(validate_run_now(run_id))
    _enqueue(deliver, run_id)
    return outcome


def _load(module_name: str, names: tuple[str, ...]) -> Any:
    """First attribute from ``names`` found on ``module_name``, or None."""
    import importlib

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        log.info("%s is not available yet: %s", module_name, exc)
        return None
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    log.info("%s has none of %s yet", module_name, ", ".join(names))
    return None


# --------------------------------------------------------------------------- delivery
async def deliver_now(run_id: int) -> list[dict[str, Any]]:
    from smartscraper.delivery import deliver_run

    outcomes = await deliver_run(run_id)
    return [
        {"target_id": o.target_id, "kind": o.kind, "status": o.status,
         "rows_sent": o.rows_sent, "error": o.error}
        for o in outcomes
    ]


@huey.task(retries=0)
def deliver(run_id: int) -> list[dict[str, Any]]:
    """Send one run to every configured target."""
    return asyncio.run(deliver_now(run_id))


async def retry_deliveries_now() -> list[dict[str, Any]]:
    from smartscraper.delivery import retry_pending

    outcomes = await retry_pending()
    return [{"delivery_id": o.delivery_id, "kind": o.kind, "status": o.status} for o in outcomes]


@huey.task(retries=0)
def retry_deliveries() -> list[dict[str, Any]]:
    """Re-attempt every parked delivery whose backoff has elapsed."""
    return asyncio.run(retry_deliveries_now())


# --------------------------------------------------------------------------- agents
# `agents.build` and `agents.repair` take a live LLMGateway and a live browser
# Engine as keyword arguments. Constructing those belongs to the agents package,
# not to the queue: the worker would otherwise have to know how to open a
# browser and which gateway backend is configured. So these tasks look for an
# orchestration entry point that takes plain ids, and report honestly when there
# is none rather than half-wiring one here.
BUILD_ENTRY_POINTS = (
    ("smartscraper.agents.jobs", ("build_scraper", "run_build")),
    ("smartscraper.agents", ("build_scraper", "run_build")),
)
REPAIR_ENTRY_POINTS = (
    ("smartscraper.agents.jobs", ("repair_scraper", "run_repair")),
    ("smartscraper.agents", ("repair_scraper", "run_repair")),
)

MISSING_ORCHESTRATION = (
    "{what} needs an orchestration entry point that takes plain ids and opens "
    "its own gateway and engine; smartscraper.agents exposes only the low-level "
    "{low}(request, *, gateway, engine). Add {names} and this task will use it."
)


def _entry_point(candidates) -> Any:
    for module_name, names in candidates:
        fn = _load(module_name, names)
        if fn is not None:
            return fn
    return None


async def _call(fn: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        result = fn(**kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return {"ok": True, "result": result}
    except Exception as exc:
        log.exception("%s failed", getattr(fn, "__name__", fn))
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def build_scraper_now(
    url: str, goal: str, *, name: str | None = None, output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fn = _entry_point(BUILD_ENTRY_POINTS)
    if fn is None:
        return {"ok": False, "error": MISSING_ORCHESTRATION.format(
            what="the builder agent", low="agents.build",
            names="smartscraper.agents.jobs.build_scraper(url, goal, name, output_schema)")}
    return await _call(fn, url=url, goal=goal, name=name, output_schema=output_schema)


@huey.task(retries=0)
def build_scraper(url: str, goal: str, name: str | None = None,
                  output_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a new scraper from a URL and a goal. Enqueued by the MCP create tool."""
    return asyncio.run(build_scraper_now(url, goal, name=name, output_schema=output_schema))


async def repair_scraper_now(scraper_id: int, run_id: int | None = None) -> dict[str, Any]:
    fn = _entry_point(REPAIR_ENTRY_POINTS)
    if fn is None:
        return {"ok": False, "error": MISSING_ORCHESTRATION.format(
            what="the repair agent", low="agents.repair",
            names="smartscraper.agents.jobs.repair_scraper(scraper_id, run_id)")}
    return await _call(fn, scraper_id=scraper_id, run_id=run_id)


@huey.task(retries=0)
def repair_scraper(scraper_id: int, run_id: int | None = None) -> dict[str, Any]:
    """Propose a new script version for a broken scraper."""
    return asyncio.run(repair_scraper_now(scraper_id, run_id))
