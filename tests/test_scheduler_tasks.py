"""run_scraper as a subprocess: log streaming, result parsing, and the kill.

The timeout test is the important one. A hung run must cost one run, not the
worker, so the child is killed and the Run row ends as `error` within seconds.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from smartscraper import repo
from smartscraper.config import get_settings
from smartscraper.db.models import Run, RunStatus, Scraper
from smartscraper.db.session import create_all, get_session, init_engine
from smartscraper.scheduler.tasks import (
    apply_result,
    execute_subprocess,
    final_line,
    read_records,
    row_hash,
    run_scraper_now,
    runner_command,
    script_path,
)


async def dispose_engine() -> None:
    from smartscraper.db import session as session_module

    if session_module._engine is not None:
        await session_module._engine.dispose()


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SS_DB_PATH", str(tmp_path / "data" / "test.db"))
    get_settings.cache_clear()
    init_engine()
    await create_all()
    yield
    # Dispose while this test's event loop is still alive: an aiosqlite
    # connection left in the pool outlives the loop that opened it and its
    # worker thread then raises "Event loop is closed" into pytest.
    await dispose_engine()
    get_settings.cache_clear()


async def make_scraper(name: str = "widgets") -> int:
    async with get_session() as s:
        scraper = Scraper(name=name, url="https://example.test", yaml_path=f"{name}.yaml")
        s.add(scraper)
        await s.flush()
        return scraper.id


async def get_run_row(run_id: int) -> Run:
    async with get_session() as s:
        run = await repo.get_run(s, run_id)
        assert run is not None
        s.expunge(run)
        return run


def child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


GOOD_RUN = """
import json, sys
print(json.dumps({"ts": "t", "level": "info", "tag": "step", "msg": "goto"}), flush=True)
print(json.dumps({"ts": "t", "level": "info", "tag": "step", "msg": "extract_list"}), flush=True)
print(json.dumps({"ts": "t", "level": "info", "tag": "result", "msg": "done",
                  "status": "passed", "row_count": 42, "engine_used": "patchright",
                  "escalation_level": 2, "cost_usd": 0.0}), flush=True)
"""

HANGS_FOREVER = """
import json, sys, time, pathlib
print(json.dumps({{"ts": "t", "level": "info", "tag": "step", "msg": "started"}}), flush=True)
time.sleep(120)
pathlib.Path({marker!r}).write_text("the child survived its timeout")
"""

SPAWNS_A_CHILD_THEN_HANGS = """
import json, subprocess, sys, time, pathlib
sub = subprocess.Popen([sys.executable, "-c",
    "import time, pathlib; time.sleep(120); pathlib.Path({marker!r}).write_text('grandchild survived')"])
print(json.dumps({{"ts": "t", "level": "info", "tag": "step", "msg": "spawned"}}), flush=True)
time.sleep(120)
"""


# --------------------------------------------------------------------------- command
async def test_runner_command_targets_the_runner_cli(db):
    scraper_id = await make_scraper()
    async with get_session() as s:
        scraper = await repo.get_scraper(s, scraper_id)
        cmd = runner_command(7, scraper, out_dir=Path("/tmp/run7"), timeout_s=120)
    assert cmd[:3] == [sys.executable, "-m", "smartscraper.runner.cli"]
    assert cmd[cmd.index("--run-id") + 1] == "7"
    assert cmd[cmd.index("--script") + 1].endswith("widgets.yaml")
    assert cmd[cmd.index("--out") + 1] == "/tmp/run7"
    assert cmd[cmd.index("--timeout") + 1] == "120"


async def test_a_relative_yaml_path_resolves_under_the_scrapers_dir(db):
    scraper_id = await make_scraper()
    async with get_session() as s:
        scraper = await repo.get_scraper(s, scraper_id)
        path = script_path(scraper)
    assert path == get_settings().scrapers_dir / "widgets.yaml"


async def test_ss_runner_cmd_overrides_the_command(db, monkeypatch):
    monkeypatch.setenv("SS_RUNNER_CMD", json.dumps(["echo", "hi"]))
    scraper_id = await make_scraper()
    async with get_session() as s:
        scraper = await repo.get_scraper(s, scraper_id)
        assert runner_command(7, scraper) == ["echo", "hi"]


# --------------------------------------------------------------------------- happy path
async def test_a_reporting_child_updates_the_run_row(db):
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(scraper_id, "manual", command=child(GOOD_RUN), followups=False)

    run = await get_run_row(run_id)
    assert run.status == RunStatus.PASSED
    assert run.row_count == 42
    assert run.engine_used == "patchright"
    assert run.escalation_level == 2
    assert run.error is None
    assert run.finished_at is not None
    assert run.duration_ms is not None


async def test_every_stdout_line_lands_in_the_run_log(db):
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(scraper_id, "manual", command=child(GOOD_RUN), followups=False)

    run = await get_run_row(run_id)
    lines = [json.loads(line) for line in Path(run.log_path).read_text().splitlines()]
    assert [line["tag"] for line in lines] == ["step", "step", "result"]
    assert Path(run.artifact_dir).is_dir()


async def test_the_run_starts_as_running_with_a_started_at(db):
    scraper_id = await make_scraper()
    before = datetime.now(UTC)
    run_id = await run_scraper_now(scraper_id, "schedule", command=child(GOOD_RUN), followups=False)
    run = await get_run_row(run_id)
    started = run.started_at.replace(tzinfo=UTC) if run.started_at.tzinfo is None else run.started_at
    assert started >= before.replace(microsecond=0)
    assert run.trigger == "schedule"


# --------------------------------------------------------------------------- the kill
async def test_a_hanging_child_is_killed_and_the_run_ends_as_error(db, tmp_path):
    """The worker must not hang, and the child must not outlive the timeout."""
    marker = tmp_path / "survivor.txt"
    scraper_id = await make_scraper()

    started = time.monotonic()
    run_id = await run_scraper_now(
        scraper_id, "manual",
        command=child(HANGS_FOREVER.format(marker=str(marker))),
        timeout_s=1.0, followups=False,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 20, "the timeout did not return control to the worker"
    run = await get_run_row(run_id)
    assert run.status == RunStatus.ERROR
    assert "timeout" in (run.error or "")
    assert run.finished_at is not None

    time.sleep(1.0)
    assert not marker.exists(), "the child kept running after its timeout"


async def test_the_kill_reaches_the_whole_process_group(db, tmp_path):
    """The real runner spawns a browser; killing only the parent leaks it."""
    marker = tmp_path / "grandchild.txt"
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual",
        command=child(SPAWNS_A_CHILD_THEN_HANGS.format(marker=str(marker))),
        timeout_s=1.0, followups=False,
    )
    assert (await get_run_row(run_id)).status == RunStatus.ERROR
    time.sleep(1.5)
    assert not marker.exists(), "a grandchild survived the process-group kill"


async def test_the_partial_log_of_a_killed_run_is_kept(db, tmp_path):
    marker = tmp_path / "survivor.txt"
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual",
        command=child(HANGS_FOREVER.format(marker=str(marker))),
        timeout_s=1.0, followups=False,
    )
    run = await get_run_row(run_id)
    assert "started" in Path(run.log_path).read_text()


# --------------------------------------------------------------------------- failures
async def test_a_crashing_child_is_an_error_with_its_stderr(db):
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual",
        command=child("import sys; print('boom: selector gone', file=sys.stderr); sys.exit(3)"),
        followups=False,
    )
    run = await get_run_row(run_id)
    assert run.status == RunStatus.ERROR
    assert "exited 3" in (run.error or "")
    assert "boom: selector gone" in (run.error or "")


async def test_a_silent_child_is_an_error(db):
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(scraper_id, "manual", command=child("pass"), followups=False)
    run = await get_run_row(run_id)
    assert run.status == RunStatus.ERROR
    assert "without reporting an outcome" in (run.error or "")


async def test_a_reported_error_status_survives_a_zero_exit_code(db):
    line = json.dumps({"tag": "result", "status": "blocked", "block_reason": "cloudflare",
                       "row_count": 0})
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual", command=child(f"print({line!r}, flush=True)"), followups=False
    )
    run = await get_run_row(run_id)
    assert run.status == RunStatus.BLOCKED
    assert run.block_reason == "cloudflare"


async def test_a_missing_executable_is_an_error_not_a_crash(db):
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual", command=["/nonexistent/runner"], followups=False
    )
    run = await get_run_row(run_id)
    assert run.status == RunStatus.ERROR
    assert "could not start the runner" in (run.error or "")


async def test_non_json_stdout_does_not_break_the_run(db):
    code = (
        "import json;"
        "print('a warning from some library', flush=True);"
        f"print({json.dumps(json.dumps({'tag': 'result', 'status': 'passed', 'row_count': 1}))}, flush=True)"
    )
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(scraper_id, "manual", command=child(code), followups=False)
    run = await get_run_row(run_id)
    assert run.status == RunStatus.PASSED
    assert run.row_count == 1


async def test_an_unknown_scraper_raises(db):
    with pytest.raises(LookupError):
        await run_scraper_now(404, "manual", command=child("pass"), followups=False)


# --------------------------------------------------------------------------- units
async def test_execute_subprocess_collects_lines(tmp_path):
    result = await execute_subprocess(child(GOOD_RUN), tmp_path / "log.txt", timeout_s=30)
    assert result["returncode"] == 0
    assert result["timed_out"] is False
    assert len(result["lines"]) == 3


async def test_execute_subprocess_reports_a_timeout(tmp_path):
    result = await execute_subprocess(
        child("import time; time.sleep(60)"), tmp_path / "log.txt", timeout_s=1
    )
    assert result["timed_out"] is True


def test_final_line_prefers_the_result_tag():
    lines = [{"tag": "result", "status": "passed"}, {"tag": "step", "msg": "after"}]
    assert final_line(lines)["status"] == "passed"


def test_final_line_falls_back_to_the_last_line():
    assert final_line([{"tag": "step", "msg": "a"}, {"tag": "step", "msg": "b"}])["msg"] == "b"


def test_final_line_of_nothing_is_none():
    assert final_line([]) is None


def test_apply_result_downgrades_a_passed_run_that_reported_an_error():
    run = Run(scraper_id=1, started_at=datetime.now(UTC))
    apply_result(run, {"returncode": 0, "lines": [{"tag": "result", "status": "passed",
                                                   "error": "selector missing"}], "raw": [],
                       "timed_out": False}, timeout_s=10)
    assert run.status == RunStatus.ERROR
    assert run.error == "selector missing"


# --------------------------------------------------------------------------- the runner protocol
OUTCOME_SHAPE = """
import json
print(json.dumps({{"ts": "t", "level": "info", "tag": "outcome", "msg": "2 rows",
                  "outcome": {{"rows": [{{"n": 1}}, {{"n": 2}}], "engine_used": "http",
                              "escalation_level": 1, "row_count": 2, "blocked": False,
                              "error": None, "validation_problems": []}}}}), flush=True)
raise SystemExit({code})
"""


async def test_the_runners_outcome_line_is_unwrapped(db):
    """The real runner nests its payload under an `outcome` key."""
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual", command=child(OUTCOME_SHAPE.format(code=0)), followups=False
    )
    run = await get_run_row(run_id)
    assert run.status == RunStatus.PASSED
    assert run.row_count == 2
    assert run.engine_used == "http"


@pytest.mark.parametrize("code,status", [
    (0, RunStatus.PASSED),
    (1, RunStatus.VALIDATION_FAILED),
    (2, RunStatus.BLOCKED),
    (3, RunStatus.ERROR),
])
async def test_the_exit_code_decides_the_status(db, code, status):
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual", command=child(OUTCOME_SHAPE.format(code=code)), followups=False
    )
    assert (await get_run_row(run_id)).status == status


async def test_validation_problems_become_the_run_error(db):
    line = json.dumps({"tag": "outcome", "outcome": {
        "rows": [], "row_count": 0, "error": None,
        "validation_problems": ["only 0 rows, min_rows is 10"]}})
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual",
        command=child(f"print({line!r}, flush=True)\nraise SystemExit(1)"), followups=False,
    )
    run = await get_run_row(run_id)
    assert run.status == RunStatus.VALIDATION_FAILED
    assert "min_rows is 10" in (run.error or "")


# --------------------------------------------------------------------------- records
async def test_records_jsonl_becomes_record_rows(db):
    """The runner writes records.jsonl; the scheduler is what puts it in the DB."""
    from smartscraper.scheduler.tasks import persist_records

    scraper_id = await make_scraper()
    run_id = await run_scraper_now(scraper_id, "manual", command=child(GOOD_RUN), followups=False)
    run = await get_run_row(run_id)
    (Path(run.artifact_dir) / "records.jsonl").write_text('{"name": "Widget"}\n{"name": "Gadget"}\n')

    rows = read_records(run.artifact_dir)
    async with get_session() as s:
        written = await persist_records(s, await repo.get_run(s, run_id), rows)
    assert written == 2

    async with get_session() as s:
        stored = await repo.get_records(s, scraper_id=scraper_id, run_id=run_id, limit=100)
    assert sorted(r.data["name"] for r in stored) == ["Gadget", "Widget"]
    assert all(r.source == "script" for r in stored)


async def test_rows_on_stdout_are_persisted_when_there_is_no_records_file(db):
    line = json.dumps({"tag": "outcome", "outcome": {
        "rows": [{"name": "Widget"}, {"name": "Gadget"}], "row_count": 2, "error": None}})
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual", command=child(f"print({line!r}, flush=True)"), followups=False
    )
    async with get_session() as s:
        stored = await repo.get_records(s, scraper_id=scraper_id, run_id=run_id, limit=100)
    assert sorted(r.data["name"] for r in stored) == ["Gadget", "Widget"]
    assert all(len(r.row_hash) == 64 for r in stored)


def test_read_records_prefers_the_file_over_stdout(tmp_path):
    (tmp_path / "records.jsonl").write_text('{"a": 1}\n\n{"a": 2}\n')
    assert read_records(tmp_path, [{"b": 9}]) == [{"a": 1}, {"a": 2}]


def test_read_records_skips_unparseable_lines(tmp_path):
    (tmp_path / "records.jsonl").write_text('{"a": 1}\nnot json\n{"a": 2}\n')
    assert read_records(tmp_path) == [{"a": 1}, {"a": 2}]


def test_read_records_falls_back_to_stdout_rows(tmp_path):
    assert read_records(tmp_path, [{"b": 9}]) == [{"b": 9}]


def test_row_hash_is_stable_and_order_independent():
    assert row_hash({"a": 1, "b": 2}) == row_hash({"b": 2, "a": 1})
    assert row_hash({"a": 1}) != row_hash({"a": 2})


# --------------------------------------------------------------------------- history
async def test_history_is_passed_to_the_runner(db):
    """Only the scheduler has the database, so it supplies the drift baseline."""
    scraper_id = await make_scraper()
    async with get_session() as s:
        for count in (120, 118, 131):
            s.add(Run(scraper_id=scraper_id, status=RunStatus.PASSED, row_count=count,
                      created_at=datetime.now(UTC)))
    async with get_session() as s:
        scraper = await repo.get_scraper(s, scraper_id)
        history = await repo.recent_row_counts(s, scraper_id, n=5)
        cmd = runner_command(7, scraper, history=history)
    assert sorted(int(c) for c in cmd[cmd.index("--history") + 1].split(",")) == [118, 120, 131]


async def test_no_history_means_no_history_flag(db):
    """An absent baseline must skip the band rule, not fail it."""
    scraper_id = await make_scraper()
    async with get_session() as s:
        scraper = await repo.get_scraper(s, scraper_id)
        assert "--history" not in runner_command(7, scraper, history=[])


# --------------------------------------------------------------------------- the runner's verdict
async def test_a_runner_supplied_report_is_persisted(db):
    """The runner validates in-process; the scheduler stores the verdict it sent."""
    report = {
        "passed": False,
        "row_count": 1,
        "summary": "1 of 2 rules failed: min_rows",
        "rules": [{"rule": "min_rows", "passed": False, "measured": "1", "expected": "at least 10"}],
        "metrics": [{"field": "name", "null_rate": 0.0, "distinct_count": 1, "sample": "Widget"}],
    }
    line = json.dumps({"tag": "outcome", "validator_report": report,
                       "outcome": {"rows": [{"name": "Widget"}], "row_count": 1, "error": None}})
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual",
        command=child(f"print({line!r}, flush=True)\nraise SystemExit(1)"), followups=False,
    )

    run = await get_run_row(run_id)
    assert run.status == RunStatus.VALIDATION_FAILED
    assert run.validator_report["passed"] is False
    assert run.validator_report["summary"]

    async with get_session() as s:
        metrics = await repo.run_metrics(s, run_id)
    assert [m.field for m in metrics] == ["name"]


async def test_a_passing_runner_report_leaves_the_run_passed(db):
    report = {"passed": True, "row_count": 2, "summary": "2 rows, 2 of 2 rules passed",
              "rules": [{"rule": "min_rows", "passed": True, "measured": "2", "expected": "at least 1"}],
              "metrics": []}
    line = json.dumps({"tag": "outcome", "validator_report": report,
                       "outcome": {"rows": [{"n": 1}, {"n": 2}], "row_count": 2, "error": None}})
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual", command=child(f"print({line!r}, flush=True)"), followups=False
    )
    run = await get_run_row(run_id)
    assert run.status == RunStatus.PASSED
    assert run.validator_report["passed"] is True


async def test_a_report_never_rescues_an_errored_run(db):
    """A run that crashed keeps `error`, however good its rows looked."""
    report = {"passed": True, "row_count": 2, "summary": "ok", "rules": [], "metrics": []}
    line = json.dumps({"tag": "outcome", "validator_report": report,
                       "outcome": {"rows": [], "row_count": 0, "error": "browser died"}})
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(
        scraper_id, "manual",
        command=child(f"print({line!r}, flush=True)\nraise SystemExit(3)"), followups=False,
    )
    run = await get_run_row(run_id)
    assert run.status == RunStatus.ERROR
    assert "browser died" in (run.error or "")


async def test_a_line_without_a_report_leaves_validation_to_the_next_step(db):
    scraper_id = await make_scraper()
    run_id = await run_scraper_now(scraper_id, "manual", command=child(GOOD_RUN), followups=False)
    assert (await get_run_row(run_id)).validator_report is None
