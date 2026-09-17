"""The subprocess contract: stdout is a JSON stream, and the exit code is a verdict.

Half of these run the CLI as a real subprocess, because the scheduler will, and
because an import-time `print` anywhere in the runner would break a live log tail
without any in-process test noticing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from smartscraper.contracts import RunOutcome
from smartscraper.dsl.models import ScrapeScript
from smartscraper.runner.artifacts import RunArtifacts
from smartscraper.runner.cli import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_VALIDATION_FAILED,
    exit_code_for,
    judge,
    load_secrets,
    main,
    parse_history,
    report_to_dict,
)

from .fixtures.serve import fixture_server

ROOT = Path(__file__).resolve().parent.parent

HEAD = """
rate_limit: {min_delay_s: 0, max_delay_s: 0}
escalation: [http]
engine: http
"""

GRID = HEAD + """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - op: extract_list
    selector: "css=.product-card"
    as: products
    fields:
      name: {selector: "css=.product-name", attr: text}
      url:  {selector: "css=.product-link", attr: href, absolute: true}
  - {op: emit, from: products}
validation: {min_rows: 4, unique: [url]}
"""

BLOCKED = HEAD + """
steps:
  - {op: goto, url: "HOST/blocked/cloudflare_challenge.html"}
  - {op: extract_list, selector: "css=.product-card", as: p,
     fields: {name: {selector: "css=h3"}}}
  - {op: emit, from: p}
"""

TOO_FEW = HEAD + """
steps:
  - {op: goto, url: "HOST/products_page3.html"}
  - {op: extract_list, selector: "css=.product-card", as: p,
     fields: {name: {selector: "css=.product-name"}}}
  - {op: emit, from: p}
validation: {min_rows: 50}
"""


@pytest.fixture(scope="module")
def base_url():
    with fixture_server() as url:
        yield url


def write(tmp_path: Path, yaml_text: str, name: str = "s.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml_text, encoding="utf-8")
    return path


def run_cli(tmp_path: Path, yaml_text: str, run_id: int = 1, *extra: str):
    """Run the CLI as a real subprocess and parse its stdout stream."""
    path = write(tmp_path, yaml_text)
    proc = subprocess.run(
        [sys.executable, "-m", "smartscraper.runner.cli",
         "--script", str(path), "--run-id", str(run_id),
         "--out", str(tmp_path / f"run{run_id}"), *extra],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    lines = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    return proc, lines


# ------------------------------------------------------------------ the stream
def test_every_stdout_line_is_a_json_object(base_url, tmp_path):
    proc, lines = run_cli(tmp_path, GRID.replace("HOST", base_url))
    assert proc.returncode == EXIT_OK
    assert lines, "the CLI must always emit something"
    assert all({"ts", "level", "tag", "msg"} <= set(x) for x in lines)


def test_the_last_line_is_the_outcome(base_url, tmp_path):
    _, lines = run_cli(tmp_path, GRID.replace("HOST", base_url))
    last = lines[-1]
    assert last["tag"] == "outcome"
    assert last["outcome"]["row_count"] == 4
    assert last["outcome"]["engine_used"] == "http"
    assert last["outcome"]["validation_problems"] == []


def test_the_outcome_deserialises_back_into_a_run_outcome(base_url, tmp_path):
    from smartscraper.runner.artifacts import outcome_from_dict

    _, lines = run_cli(tmp_path, GRID.replace("HOST", base_url))
    outcome = outcome_from_dict(lines[-1]["outcome"])
    assert isinstance(outcome, RunOutcome)
    assert len(outcome.rows) == 4


def test_nothing_but_json_reaches_stdout(base_url, tmp_path):
    proc, _ = run_cli(tmp_path, GRID.replace("HOST", base_url))
    for line in proc.stdout.splitlines():
        if line.strip():
            json.loads(line)   # raises if a stray print sneaked in


def test_the_log_file_mirrors_the_stream(base_url, tmp_path):
    run_cli(tmp_path, GRID.replace("HOST", base_url), 5)
    mirrored = RunArtifacts(5, tmp_path / "run5").read_log()
    assert any(x["tag"] == "outcome" for x in mirrored)


def test_log_level_filters_the_stream(base_url, tmp_path):
    _, quiet = run_cli(tmp_path, GRID.replace("HOST", base_url), 6, "--log-level", "warn")
    assert not any(x["level"] in ("debug", "info") for x in quiet[:-1])


# ------------------------------------------------------------------ exit codes
def test_a_passing_run_exits_zero(base_url, tmp_path):
    proc, _ = run_cli(tmp_path, GRID.replace("HOST", base_url))
    assert proc.returncode == EXIT_OK


def test_a_blocked_run_exits_two(base_url, tmp_path):
    proc, lines = run_cli(tmp_path, BLOCKED.replace("HOST", base_url), 2)
    assert proc.returncode == EXIT_BLOCKED
    assert lines[-1]["outcome"]["block_reason"] == "cloudflare"


def test_a_run_that_misses_min_rows_exits_one(base_url, tmp_path):
    proc, lines = run_cli(tmp_path, TOO_FEW.replace("HOST", base_url), 3)
    assert proc.returncode == EXIT_VALIDATION_FAILED
    assert any("min_rows" in p for p in lines[-1]["outcome"]["validation_problems"])


def test_a_missing_script_file_exits_three(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "smartscraper.runner.cli",
         "--script", str(tmp_path / "nope.yaml"), "--run-id", "9",
         "--out", str(tmp_path / "run9")],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    lines = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    assert proc.returncode == EXIT_ERROR
    assert lines[-1]["tag"] == "outcome"
    assert "not found" in lines[-1]["outcome"]["error"]


def test_an_invalid_script_exits_three_with_the_reason(tmp_path):
    proc, lines = run_cli(tmp_path, "steps: [{op: not_a_real_op}]\n", 10)
    assert proc.returncode == EXIT_ERROR
    assert "invalid script" in lines[-1]["outcome"]["error"]


def test_a_script_with_no_emit_is_rejected_by_the_dsl(tmp_path):
    proc, lines = run_cli(tmp_path, HEAD + 'steps: [{op: sleep, ms: 1}]\n', 11)
    assert proc.returncode == EXIT_ERROR
    assert "emit" in lines[-1]["outcome"]["error"]


def test_a_broken_selector_exits_three_not_one(base_url, tmp_path):
    body = HEAD + """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - {op: extract_list, selector: "css=.gone", as: p, min_items: 1,
     fields: {name: {selector: "css=h3"}}}
  - {op: emit, from: p}
""".replace("HOST", base_url)
    proc, lines = run_cli(tmp_path, body, 12)
    # An exception during the run is an error, not a validation failure: the
    # rows were never produced, so there is nothing to judge.
    assert proc.returncode == EXIT_ERROR
    assert ".gone" in lines[-1]["outcome"]["error"]


# ------------------------------------------------------------------- artifacts
def test_the_run_directory_holds_the_expected_files(base_url, tmp_path):
    run_cli(tmp_path, GRID.replace("HOST", base_url), 4)
    names = {p.name for p in (tmp_path / "run4").iterdir()}
    assert {"log.txt", "page.html", "records.jsonl", "outcome.json"} <= names


def test_records_jsonl_holds_one_object_per_row(base_url, tmp_path):
    run_cli(tmp_path, GRID.replace("HOST", base_url), 14)
    rows = RunArtifacts(14, tmp_path / "run14").read_records()
    assert len(rows) == 4 and rows[0]["name"] == "Aeron Chair"


# --------------------------------------------------------------------- secrets
def test_secrets_load_from_a_json_file(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"shop_user": "me", "shop_pw": "pw"}), encoding="utf-8")
    assert load_secrets(path, env={}) == {"shop_user": "me", "shop_pw": "pw"}


def test_env_secrets_are_merged_and_lowercased(tmp_path):
    got = load_secrets(None, env={"SS_SECRET_SHOP_PW": "pw", "PATH": "/bin"})
    assert got == {"shop_pw": "pw"}


def test_env_secrets_win_over_the_file(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"shop_pw": "from-file"}), encoding="utf-8")
    assert load_secrets(path, env={"SS_SECRET_SHOP_PW": "from-env"})["shop_pw"] == "from-env"


def test_a_non_object_secrets_file_is_rejected(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_secrets(path, env={})


def test_missing_secrets_file_is_not_an_error(tmp_path):
    assert load_secrets(tmp_path / "absent.json", env={}) == {}


# ------------------------------------------------------------------ validation
def outcome(rows, **kw):
    return RunOutcome(rows=rows, engine_used="http", escalation_level=0, **kw)


def parse(body: str) -> ScrapeScript:
    return ScrapeScript.from_yaml(body)


def test_judge_delegates_to_the_validate_subsystem():
    script = parse(HEAD + "steps: [{op: emit, from: x}]\nvalidation: {min_rows: 5}\n")
    report = judge(outcome([{"a": 1}]), script)
    assert report is not None and not report.passed
    assert [r.rule for r in report.failures] == ["min_rows"]


def test_judge_passes_a_healthy_run():
    script = parse(HEAD + "steps: [{op: emit, from: x}]\nvalidation: {min_rows: 1}\n")
    report = judge(outcome([{"a": 1}, {"a": 2}]), script)
    assert report is not None and report.passed


def test_a_blocked_run_is_not_judged():
    # Blaming the script for the site's refusal would send a repair agent after
    # a selector that is fine.
    script = parse(HEAD + "steps: [{op: emit, from: x}]\nvalidation: {min_rows: 5}\n")
    assert judge(outcome([], blocked=True, block_reason="cloudflare"), script) is None


def test_an_errored_run_is_not_judged():
    script = parse(HEAD + "steps: [{op: emit, from: x}]\nvalidation: {min_rows: 5}\n")
    assert judge(outcome([], error="StepError: boom"), script) is None


def test_history_feeds_the_row_count_band_rule():
    script = parse(
        HEAD + "steps: [{op: emit, from: x}]\n"
        "validation: {min_rows: 1, row_count_band: {relative_to: last_5_runs, tolerance: 0.2}}\n"
    )
    steady = judge(outcome([{"a": i} for i in range(100)]), script, [100, 98, 102])
    collapsed = judge(outcome([{"a": i} for i in range(10)]), script, [100, 98, 102])
    assert steady is not None and steady.passed
    assert collapsed is not None and not collapsed.passed


def test_an_empty_history_skips_the_band_rule_rather_than_failing_it():
    script = parse(
        HEAD + "steps: [{op: emit, from: x}]\n"
        "validation: {min_rows: 1, row_count_band: {relative_to: last_5_runs, tolerance: 0.2}}\n"
    )
    report = judge(outcome([{"a": 1}]), script, [])
    assert report is not None and report.passed


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("120,118,131", [120, 118, 131]),
        (" 120 , 118 ", [120, 118]),
        ("[120, 118]", [120, 118]),
        ("", []),
        (None, []),
        ("not numbers", []),
        ("[]", []),
    ],
)
def test_parse_history(raw, expected):
    assert parse_history(raw) == expected


@pytest.mark.parametrize(
    ("blocked", "error", "failed_rule", "expected"),
    [
        (False, None, False, EXIT_OK),
        (False, None, True, EXIT_VALIDATION_FAILED),
        (True, "blocked (cloudflare)", False, EXIT_BLOCKED),
        (False, "StepError: boom", False, EXIT_ERROR),
        (True, "StepError: boom", True, EXIT_BLOCKED),   # blocked outranks all
    ],
)
def test_exit_code_precedence(blocked, error, failed_rule, expected):
    from smartscraper.contracts import RuleResult, ValidatorReport

    report = ValidatorReport(
        passed=not failed_rule,
        rules=[RuleResult(rule="min_rows", passed=not failed_rule,
                          measured="0 rows", expected=">= 1 rows")],
    )
    result = outcome([], blocked=blocked, error=error)
    assert exit_code_for(result, report) == expected


def test_a_run_with_no_report_exits_zero_when_nothing_else_failed():
    assert exit_code_for(outcome([{"a": 1}]), None) == EXIT_OK


# ------------------------------------------------------- the pipeline wire shape
def test_report_to_dict_matches_what_the_pipeline_reads_back():
    # The runner serialises the report itself rather than importing `pipeline`,
    # which would drag SQLAlchemy into every run subprocess. This test is what
    # keeps the two shapes from drifting apart.
    from smartscraper.pipeline import report_from_dict

    script = parse(HEAD + "steps: [{op: emit, from: x}]\nvalidation: {min_rows: 5}\n")
    original = judge(outcome([{"a": 1}]), script)
    restored = report_from_dict(report_to_dict(original))
    assert restored.passed == original.passed
    assert restored.row_count == original.row_count
    assert [r.rule for r in restored.rules] == [r.rule for r in original.rules]
    assert [m.field for m in restored.metrics] == [m.field for m in original.metrics]


def test_the_pipeline_can_parse_the_real_final_stdout_line(base_url, tmp_path):
    from smartscraper.pipeline import parse_runner_report

    _, lines = run_cli(tmp_path, GRID.replace("HOST", base_url), 50)
    report = parse_runner_report(json.dumps(lines[-1]))
    assert report is not None, "pipeline must find validator_report on the last line"
    assert report.passed is True
    assert report.row_count == 4
    assert report.rules


def test_the_pipeline_parses_a_failing_report_from_the_real_run(base_url, tmp_path):
    from smartscraper.pipeline import parse_runner_report

    _, lines = run_cli(tmp_path, TOO_FEW.replace("HOST", base_url), 51)
    report = parse_runner_report(json.dumps(lines[-1]))
    assert report is not None and report.passed is False
    assert "min_rows" in [r.rule for r in report.failures]


def test_a_blocked_run_carries_a_null_report(base_url, tmp_path):
    _, lines = run_cli(tmp_path, BLOCKED.replace("HOST", base_url), 52)
    assert lines[-1]["validator_report"] is None


def test_the_report_carries_per_field_metrics(base_url, tmp_path):
    _, lines = run_cli(tmp_path, GRID.replace("HOST", base_url), 53)
    metrics = lines[-1]["validator_report"]["metrics"]
    assert {m["field"] for m in metrics} == {"name", "url"}
    assert all("null_rate" in m and "distinct_count" in m for m in metrics)


def test_each_rule_is_logged_as_its_own_line(base_url, tmp_path):
    _, lines = run_cli(tmp_path, TOO_FEW.replace("HOST", base_url), 54)
    rules = [x for x in lines if x["tag"] == "validation" and "rule" in x]
    assert rules
    assert any(x["rule"] == "min_rows" and x["passed"] is False for x in rules)


# ---------------------------------------------------------------- in-process
def test_main_returns_the_exit_code_without_raising(base_url, tmp_path):
    path = write(tmp_path, GRID.replace("HOST", base_url))
    code = main(["--script", str(path), "--run-id", "40", "--out", str(tmp_path / "run40")])
    assert code == EXIT_OK
