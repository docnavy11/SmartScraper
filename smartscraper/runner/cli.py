"""`python -m smartscraper.runner.cli --script s.yaml --run-id 12 --out DIR`

The scheduler spawns this as a subprocess and reads stdout. That is the whole
interface, so two rules are absolute.

**Every stdout line is one JSON object.** `{"ts", "level", "tag", "msg", ...}`.
The web UI parses them one at a time and pushes them over SSE, so a stray
`print` anywhere in the runner breaks a live log tail. Tracebacks and warnings
go to stderr.

**The last line is the outcome.** `{"tag": "outcome", ...}` carrying the
serialised `RunOutcome`, so the parent never has to guess from the exit code
alone.

Exit codes:

===  ===============================================================
 0   passed
 1   validation failed: the run completed but the rows are not usable
 2   blocked: the site refused us on every rung
 3   error: anything else, including a bad script file
===  ===============================================================

Validation is `smartscraper.validate.validate`, called here rather than in the
parent so the verdict travels with the run that produced it. The report goes out
as a top-level `validator_report` key on the final line, which is where
`pipeline.parse_runner_report` reads it. Drift needs history the runner does not
have, so the scheduler passes recent row counts in with `--history`; without
them the row-count band rule skips instead of failing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from smartscraper.contracts import RunOutcome, ValidatorReport
from smartscraper.dsl.models import ScrapeScript
from smartscraper.runner import execute
from smartscraper.runner.artifacts import RunArtifacts, outcome_to_dict
from smartscraper.runner.runlog import stdout_logger
from smartscraper.validate import validate

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_BLOCKED = 2
EXIT_ERROR = 3

SECRET_PREFIX = "SS_SECRET_"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m smartscraper.runner.cli",
        description="Execute one scrape script and stream a structured log to stdout.",
    )
    p.add_argument("--script", required=True, type=Path, help="path to the script YAML")
    p.add_argument("--run-id", required=True, help="run id; names the artifact directory")
    p.add_argument("--out", type=Path, default=None,
                   help="artifact directory (default: data/runs/<run-id>)")
    p.add_argument("--proxy", default=None, help="proxy URL for the rungs that use one")
    p.add_argument("--secrets", type=Path, default=None,
                   help="JSON file of {name: value}; SS_SECRET_* env vars are merged in")
    p.add_argument("--headed", action="store_true", help="run browser rungs headed")
    p.add_argument("--timeout", type=float, default=None,
                   help="wall-clock limit in seconds (default: config run_timeout_s)")
    p.add_argument("--history", default=None,
                   help="recent row counts, most recent first (e.g. 120,118,131); "
                        "feeds the row_count_band rule, and skips it when absent")
    p.add_argument("--log-level", default="info", choices=["debug", "info", "warn", "error"])
    return p


def load_secrets(path: Path | None, env: dict[str, str] | None = None) -> dict[str, str]:
    """Secrets from a JSON file, overlaid by `SS_SECRET_<NAME>` env vars.

    The env form wins so the scheduler can pass a one-off credential without
    writing it to disk. Values are never logged; `fill` logs the name only.
    """
    out: dict[str, str] = {}
    if path is not None and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a JSON object of name -> value")
        out.update({str(k): str(v) for k, v in data.items()})
    for key, value in (env if env is not None else os.environ).items():
        if key.startswith(SECRET_PREFIX) and len(key) > len(SECRET_PREFIX):
            out[key[len(SECRET_PREFIX) :].lower()] = value
    return out


def judge(outcome: RunOutcome, script: ScrapeScript,
          history: Sequence[int] = ()) -> ValidatorReport | None:
    """Run the validator over a finished run's rows.

    Returns None when the run cannot be judged. A blocked or errored run never
    produced rows, so calling it a validation failure would blame the script for
    the site's refusal. This mirrors `pipeline.UNJUDGEABLE`.
    """
    if outcome.blocked or outcome.error:
        return None
    return validate(outcome.rows, script, history)


def report_to_dict(report: ValidatorReport) -> dict[str, Any]:
    """The wire shape `pipeline.report_from_dict` reads back.

    Deliberately not imported from `pipeline`: that module pulls in SQLAlchemy
    and the db models, and the runner subprocess must not carry a database
    dependency just to serialise a verdict. `test_runner_cli` pins the two
    together by feeding this output through `pipeline.parse_runner_report`.
    """
    return {
        "passed": report.passed,
        "row_count": report.row_count,
        "summary": report.summary(),
        "rules": [asdict(r) for r in report.rules],
        "metrics": [asdict(m) for m in report.metrics],
    }


def parse_history(raw: str | None) -> list[int]:
    """Recent row counts, most recent first, as `--history 120,118,131`.

    Also accepts a JSON array. An empty or unparseable value yields no history,
    which makes the row-count band rule skip rather than fail: a first run has
    nothing to drift from.
    """
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    try:
        if text.startswith("["):
            data = json.loads(text)
            return [int(x) for x in data if x is not None]
        return [int(part) for part in text.split(",") if part.strip()]
    except (ValueError, TypeError):
        return []


def exit_code_for(outcome: RunOutcome, report: ValidatorReport | None) -> int:
    """Blocked outranks error outranks validation.

    A run that raised nothing and still fails a rule exits 1. That is the case
    the whole project exists to catch, so it must never collapse into 0.
    """
    if outcome.blocked:
        return EXIT_BLOCKED
    if outcome.error:
        return EXIT_ERROR
    if report is not None and not report.passed:
        return EXIT_VALIDATION_FAILED
    return EXIT_OK


async def _run(args: argparse.Namespace) -> int:
    artifacts = RunArtifacts(args.run_id, args.out)
    log = stdout_logger(run_id=_as_int(args.run_id), path=artifacts.log_path)
    log.min_level = args.log_level
    outcome: RunOutcome
    script: ScrapeScript | None = None
    report: ValidatorReport | None = None
    try:
        try:
            script = ScrapeScript.from_yaml(Path(args.script).read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.error("script", f"no such script: {args.script}", path=str(args.script))
            _emit_outcome(log, RunOutcome(rows=[], engine_used="", escalation_level=0,
                                          error=f"script not found: {args.script}"), None)
            return EXIT_ERROR
        except Exception as exc:
            log.error("script", f"script did not validate: {exc}", path=str(args.script),
                      kind=type(exc).__name__)
            _emit_outcome(log, RunOutcome(rows=[], engine_used="", escalation_level=0,
                                          error=f"invalid script: {exc}"), None)
            return EXIT_ERROR

        secrets = load_secrets(args.secrets)
        log.info("script", f"loaded {args.script}", path=str(args.script),
                 version=script.version, steps=len(script.steps),
                 engine=str(script.engine), secrets=sorted(secrets))

        timeout = args.timeout if args.timeout is not None else _default_timeout()
        coro = execute(script, run_id=args.run_id, out_dir=artifacts.dir, secrets=secrets,
                       proxy=args.proxy, headed=args.headed, log=log)
        try:
            outcome = await asyncio.wait_for(coro, timeout=timeout) if timeout else await coro
        except TimeoutError:
            log.error("run", f"wall-clock timeout after {timeout}s", timeout_s=timeout)
            outcome = RunOutcome(rows=artifacts.read_records(), engine_used="",
                                 escalation_level=0, error=f"timeout after {timeout}s")

        history = parse_history(args.history)
        report = judge(outcome, script, history)
        if report is None:
            log.info("validation", "not judged: the run produced no rows to judge",
                     blocked=outcome.blocked, errored=bool(outcome.error))
        else:
            for rule in report.rules:
                emit = log.info if rule.passed else log.error
                emit("validation", f"{rule.rule}: {rule.measured} (expected {rule.expected})",
                     rule=rule.rule, passed=rule.passed,
                     measured=rule.measured, expected=rule.expected)
            log.info("validation", report.summary(), passed=report.passed,
                     rules=len(report.rules), failed=len(report.failures),
                     history=history)
        code = exit_code_for(outcome, report)
        log.info("run", f"finished: {_verdict(code)}", exit_code=code, rows=len(outcome.rows),
                 blocked=outcome.blocked, validated=report is not None)
        _emit_outcome(log, outcome, report)
        return code
    except Exception as exc:  # last resort: never die without a parseable last line
        traceback.print_exc(file=sys.stderr)
        log.error("run", f"unhandled {type(exc).__name__}: {exc}", kind=type(exc).__name__)
        _emit_outcome(log, RunOutcome(rows=[], engine_used="", escalation_level=0,
                                      error=f"{type(exc).__name__}: {exc}"), report)
        return EXIT_ERROR
    finally:
        artifacts.close()
        log.close()


def _emit_outcome(log: Any, outcome: RunOutcome, report: ValidatorReport | None) -> None:
    """The last stdout line. Always emitted, whatever went wrong.

    `validator_report` sits at the top level of the line, not inside `outcome`,
    because that is exactly where `pipeline.parse_runner_report` looks for it.
    """
    payload = outcome_to_dict(outcome)
    serialised = report_to_dict(report) if report is not None else None
    payload["validation_problems"] = (
        [f"{r.rule}: {r.measured} (expected {r.expected})" for r in report.failures]
        if report is not None else []
    )
    log.emit("info", "outcome", f"{len(outcome.rows)} rows",
             outcome=payload, validator_report=serialised)


def _verdict(code: int) -> str:
    return {
        EXIT_OK: "passed",
        EXIT_VALIDATION_FAILED: "validation failed",
        EXIT_BLOCKED: "blocked",
        EXIT_ERROR: "error",
    }.get(code, "unknown")


def _default_timeout() -> float | None:
    try:
        from smartscraper.config import get_settings

        return float(get_settings().run_timeout_s)
    except Exception:
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
