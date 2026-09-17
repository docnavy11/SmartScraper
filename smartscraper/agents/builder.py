"""The builder agent: URL plus a goal in, a tested ScrapeScript out.

The loop is the model's; this module owns what happens around it. It injects the
browser tools, hands the agent the DSL as its output contract, and afterwards
backfills `output_schema` and `validation` from what was actually measured
during the run rather than from what the model asserted.

It also saves the captured page as a fixture, so a later repair can be tried
offline before anything touches the site again.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..contracts import Engine, LLMGateway, Usage
from ..dsl.models import ScrapeScript, Validation, json_schema
from . import prompts
from .tools_browser import BrowserToolbox, TrialRun, TrialRunner

# Backfill band for a row count, applied only when the agent left the defaults
# in place. n is the number of rows the test run produced. These multipliers are
# a starting policy, not a measurement: half the observed count as the floor,
# three times as the ceiling.
MIN_ROWS_FACTOR = 0.5
MAX_ROWS_FACTOR = 3.0
# Headroom added to an observed per-field null rate before it becomes a threshold.
NULL_RATE_HEADROOM = 0.05


@dataclass(slots=True)
class BuildRequest:
    url: str
    goal: str
    name: str = ""
    target_schema: dict[str, Any] | None = None
    max_turns: int | None = None


@dataclass(slots=True)
class BuildResult:
    ok: bool
    script: ScrapeScript | None = None
    usage: Usage | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    fixture_path: Path | None = None
    transcript_path: Path | None = None
    text: str = ""
    error: str | None = None
    probes: list[dict[str, Any]] = field(default_factory=list)
    # Interactions this engine could not perform. Non-empty means the
    # scraper should be escalated to a browser rung and rebuilt.
    capability_gaps: list[str] = field(default_factory=list)
    # Set by `jobs.build_scraper` once the scraper row exists, so a caller can
    # link straight to it instead of guessing the slug.
    scraper_id: int | None = None


def slug(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return out[:60] or "scraper"


# --------------------------------------------------------------------------- inference
def infer_output_schema(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A JSON Schema for one record, inferred from the rows a test run produced.

    Types come from the observed values. `required` lists only the fields that
    were non-null in every observed row, so it is a statement about this sample
    and nothing more.
    """
    if not rows:
        return None

    types: dict[str, set[str]] = {}
    non_null: dict[str, int] = {}
    for row in rows:
        for key, value in row.items():
            types.setdefault(key, set()).add(_json_type(value))
            if value is not None and value != "":
                non_null[key] = non_null.get(key, 0) + 1

    properties: dict[str, Any] = {}
    for key, seen in types.items():
        seen.discard("null")
        if not seen:
            properties[key] = {"type": ["string", "null"]}
        elif len(seen) == 1:
            properties[key] = {"type": next(iter(seen))}
        else:
            properties[key] = {"type": sorted(seen)}

    required = sorted(k for k in types if non_null.get(k, 0) == len(rows))
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def measured_null_rates(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Observed null-or-empty rate per field across the rows of one test run."""
    if not rows:
        return {}
    keys: set[str] = set()
    for row in rows:
        keys.update(row)
    out: dict[str, float] = {}
    for key in sorted(keys):
        missing = sum(1 for r in rows if r.get(key) is None or r.get(key) == "")
        out[key] = round(missing / len(rows), 4)
    return out


def derive_validation(script: ScrapeScript, test: TrialRun | None) -> Validation:
    """Fill a validation block from the test run, leaving anything the agent set.

    Only defaults are replaced. If the agent gave a threshold it stands, because
    it may know something the single test run does not show.
    """
    current = script.validation
    if test is None or not test.ok or test.row_count <= 0:
        return current

    n = test.row_count
    updates: dict[str, Any] = {}
    if current.min_rows == Validation().min_rows:
        updates["min_rows"] = max(1, int(n * MIN_ROWS_FACTOR))
    if current.max_rows is None:
        updates["max_rows"] = max(1, int(n * MAX_ROWS_FACTOR))
    if not current.max_null_rate and test.rows:
        rates = measured_null_rates(test.rows)
        updates["max_null_rate"] = {
            k: round(min(1.0, v + NULL_RATE_HEADROOM), 4) for k, v in rates.items()
        }
    if not current.required_fields and test.rows:
        rates = measured_null_rates(test.rows)
        updates["required_fields"] = [k for k, v in rates.items() if v == 0.0]

    if not updates:
        return current
    return current.model_copy(update=updates)


# --------------------------------------------------------------------------- fixture
def save_fixture(
    name: str,
    toolbox: BrowserToolbox,
    test: TrialRun | None,
    *,
    settings: Settings,
) -> Path | None:
    """Write the captured page next to a note on what the run produced.

    This is what a repair is tried against before it is allowed near the site.
    """
    snap = toolbox.last_snapshot
    if snap is None:
        return None
    directory = settings.fixtures_dir / slug(name)
    directory.mkdir(parents=True, exist_ok=True)

    html_path = directory / "page.html"
    html_path.write_text(snap.html, encoding="utf-8")
    (directory / "tree.txt").write_text(snap.accessibility_tree, encoding="utf-8")
    (directory / "meta.json").write_text(
        json.dumps(
            {
                "url": snap.url,
                "title": snap.title,
                "truncated": snap.truncated,
                "row_count": test.row_count if test else 0,
                "sample_rows": (test.rows[:5] if test else []),
                "probes": [
                    {
                        "selector": p.selector,
                        "matched": p.matched,
                        "non_empty": p.non_empty,
                        "error": p.error,
                    }
                    for p in toolbox.probes
                ],
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    return html_path


# --------------------------------------------------------------------------- prompt
def build_prompt(request: BuildRequest) -> str:
    parts = [
        f"URL: {request.url}",
        f"Goal: {request.goal}",
    ]
    if request.target_schema:
        parts.append(
            "The user asked for this output schema. Match it if the page supports it; "
            "say so in your answer if it does not:\n"
            + json.dumps(request.target_schema, indent=2)
        )
    parts.append(
        "The script you pass to propose_script must validate against this JSON Schema:\n"
        + json.dumps(json_schema(), indent=2)
    )
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- entry point
async def build(
    request: BuildRequest,
    *,
    gateway: LLMGateway,
    engine: Engine,
    settings: Settings | None = None,
    test_run: TrialRunner | None = None,
    on_event: Any | None = None,
) -> BuildResult:
    """Drive one build. Returns the tested script plus what it cost."""
    settings = settings or get_settings()
    toolbox = BrowserToolbox(engine, settings=settings, test_run=test_run, goal=request.goal)

    result = await gateway.run_agent(
        system=prompts.load(prompts.BUILDER),
        prompt=build_prompt(request),
        tools=toolbox.tools(),
        model=settings.builder_model,
        max_turns=request.max_turns or settings.max_builder_turns,
        on_event=on_event,
    )

    probes = [
        {"selector": p.selector, "matched": p.matched, "non_empty": p.non_empty, "error": p.error}
        for p in toolbox.probes
    ]
    script = toolbox.accepted
    test = toolbox.last_test

    if script is None:
        reason = result.error or "the agent finished without a script that validated and ran"
        if toolbox.proposals and toolbox.proposals[-1].errors:
            reason = f"last proposal did not validate: {toolbox.proposals[-1].errors}"
        return BuildResult(
            ok=False,
            usage=result.usage,
            transcript_path=result.transcript_path,
            text=result.text,
            error=reason,
            probes=probes,
            capability_gaps=list(toolbox.capability_gaps),
        )

    updates: dict[str, Any] = {"validation": derive_validation(script, test)}
    if script.output_schema is None:
        inferred = request.target_schema or (infer_output_schema(test.rows) if test else None)
        if inferred is not None:
            updates["output_schema"] = inferred
    script = script.model_copy(update=updates)

    fixture = save_fixture(request.name or request.url, toolbox, test, settings=settings)

    return BuildResult(
        ok=True,
        script=script,
        usage=result.usage,
        rows=test.rows if test else [],
        row_count=test.row_count if test else 0,
        fixture_path=fixture,
        transcript_path=result.transcript_path,
        text=result.text,
        probes=probes,
        capability_gaps=list(toolbox.capability_gaps),
    )
