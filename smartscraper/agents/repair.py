"""The repair agent, and the pure function that decides how big its change was.

`classify_change` is what the `auto_if_minor` promotion policy rests on, so it is
a plain function over two scripts with no I/O and no model in it. Minor means all
four of these hold:

  * only `selector` and `fallback_selectors` values differ,
  * the step list is the same length with the same ops in the same order,
  * no `custom_python` step appears, changes, or disappears,
  * no record field is added or removed, and `output_schema` is untouched.

Anything else, including a change to a validation threshold or the engine, is
major and goes to a human.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from ..contracts import Engine, LLMGateway, PageSnapshot, Usage, ValidatorReport
from ..dsl.models import ScrapeScript, json_schema
from . import prompts
from .tools_browser import BrowserToolbox, TrialRunner

# Step-level keys whose value may change without making a change major.
MINOR_KEYS = frozenset({"selector", "fallback_selectors"})


# --------------------------------------------------------------------------- classify
def _flatten(steps: list[Any], prefix: str = "") -> list[tuple[str, str, dict[str, Any]]]:
    """(path, op, body) for every step including the ones nested inside loops."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for index, step in enumerate(steps):
        body = step.model_dump(mode="json", by_alias=True)
        nested = body.pop("steps", None)
        path = f"{prefix}{index}"
        out.append((path, str(body.get("op", "")), body))
        if nested is not None:
            out.extend(_flatten(step.steps, prefix=f"{path}."))
    return out


def _top_level(script: ScrapeScript) -> dict[str, Any]:
    body = script.model_dump(mode="json", by_alias=True)
    body.pop("steps", None)
    body.pop("version", None)
    return body


def classify_change(old: ScrapeScript, new: ScrapeScript) -> tuple[bool, list[str]]:
    """Is the move from `old` to `new` minor, and what decided that?

    Returns `(is_minor, reasons)`. When it is not minor the reasons are the
    disqualifying changes. When it is minor they are the selector changes that
    were made, so a reviewer can read them without opening the diff.
    """
    blocking: list[str] = []
    minor_changes: list[str] = []

    if new.has_custom_python and not old.has_custom_python:
        blocking.append("adds a custom_python step")

    old_top, new_top = _top_level(old), _top_level(new)
    for key in sorted(set(old_top) | set(new_top)):
        if old_top.get(key) != new_top.get(key):
            blocking.append(f"{key} changed")

    old_steps = _flatten(old.steps)
    new_steps = _flatten(new.steps)

    if len(old_steps) != len(new_steps):
        blocking.append(f"step count changed: {len(old_steps)} -> {len(new_steps)}")
    else:
        for (path, old_op, old_body), (_, new_op, new_body) in zip(old_steps, new_steps, strict=True):
            if old_op != new_op:
                blocking.append(f"step {path}: op changed {old_op} -> {new_op}")
                continue
            blocking.extend(_diff_step(path, old_op, old_body, new_body, minor_changes))

    if blocking:
        return False, blocking
    return True, minor_changes


def _diff_step(
    path: str,
    op: str,
    old_body: dict[str, Any],
    new_body: dict[str, Any],
    minor_changes: list[str],
) -> list[str]:
    blocking: list[str] = []
    for key in sorted(set(old_body) | set(new_body)):
        before, after = old_body.get(key), new_body.get(key)
        if before == after:
            continue
        if key == "fields":
            blocking.extend(_diff_fields(path, before or {}, after or {}, minor_changes))
            continue
        if key in MINOR_KEYS and op != "custom_python":
            minor_changes.append(f"step {path} ({op}): {key} {before!r} -> {after!r}")
            continue
        blocking.append(f"step {path} ({op}): {key} changed")
    return blocking


def _diff_fields(
    path: str,
    old_fields: dict[str, Any],
    new_fields: dict[str, Any],
    minor_changes: list[str],
) -> list[str]:
    blocking: list[str] = []
    added = sorted(set(new_fields) - set(old_fields))
    removed = sorted(set(old_fields) - set(new_fields))
    for name in added:
        blocking.append(f"step {path}: field {name!r} added")
    for name in removed:
        blocking.append(f"step {path}: field {name!r} removed")

    for name in sorted(set(old_fields) & set(new_fields)):
        before, after = old_fields[name] or {}, new_fields[name] or {}
        for key in sorted(set(before) | set(after)):
            if before.get(key) == after.get(key):
                continue
            if key in MINOR_KEYS:
                minor_changes.append(
                    f"step {path} field {name}: {key} {before.get(key)!r} -> {after.get(key)!r}"
                )
            else:
                blocking.append(f"step {path} field {name}: {key} changed")
    return blocking


def unified_diff(old: ScrapeScript, new: ScrapeScript) -> str:
    """The candidate against the active version, as YAML."""
    return "".join(
        difflib.unified_diff(
            old.to_yaml().splitlines(keepends=True),
            new.to_yaml().splitlines(keepends=True),
            fromfile=f"v{old.version}",
            tofile=f"v{new.version}",
        )
    )


# --------------------------------------------------------------------------- agent
@dataclass(slots=True)
class RepairRequest:
    script: ScrapeScript
    report: ValidatorReport
    snapshot: PageSnapshot | None = None
    last_good_sample: list[dict[str, Any]] = field(default_factory=list)
    name: str = ""
    max_turns: int | None = None


@dataclass(slots=True)
class RepairResult:
    ok: bool
    candidate: ScrapeScript | None = None
    diff: str = ""
    rationale: str = ""
    is_minor: bool = False
    reasons: list[str] = field(default_factory=list)
    usage: Usage | None = None
    transcript_path: Any = None
    error: str | None = None
    # Interactions this engine could not perform; a signal to escalate.
    capability_gaps: list[str] = field(default_factory=list)


def repair_prompt(request: RepairRequest) -> str:
    report = request.report
    parts = [
        "The active script has stopped producing good data.",
        "Active script:\n\n```yaml\n" + request.script.to_yaml() + "```",
        "Validator report: "
        + report.summary()
        + "\n"
        + json.dumps(
            {
                "row_count": report.row_count,
                "failures": [
                    {"rule": r.rule, "measured": r.measured, "expected": r.expected}
                    for r in report.failures
                ],
                "metrics": [
                    {
                        "field": m.field,
                        "null_rate": m.null_rate,
                        "distinct_count": m.distinct_count,
                        "sample": m.sample,
                    }
                    for m in report.metrics
                ],
            },
            indent=2,
            default=str,
        ),
    ]
    if request.last_good_sample:
        parts.append(
            "Last known good rows:\n"
            + json.dumps(request.last_good_sample[:5], indent=2, default=str)
        )
    if request.snapshot is not None:
        parts.append(
            f"The failed run captured {request.snapshot.url} "
            f"(title: {request.snapshot.title!r}). Use snapshot and extract_probe to read it."
        )
    parts.append(
        "Your repaired script must validate against this JSON Schema:\n"
        + json.dumps(json_schema(), indent=2)
    )
    return "\n\n".join(parts)


def first_paragraph(text: str) -> str:
    for block in text.split("\n\n"):
        cleaned = " ".join(block.split())
        if cleaned:
            return cleaned
    return ""


async def repair(
    request: RepairRequest,
    *,
    gateway: LLMGateway,
    engine: Engine,
    settings: Settings | None = None,
    test_run: TrialRunner | None = None,
    on_event: Any | None = None,
) -> RepairResult:
    """Run one repair. The candidate is not promoted here; that is the caller's call."""
    settings = settings or get_settings()
    toolbox = BrowserToolbox(engine, settings=settings, test_run=test_run, goal="repair")

    result = await gateway.run_agent(
        system=prompts.load(prompts.REPAIR),
        prompt=repair_prompt(request),
        tools=toolbox.tools(),
        model=settings.repair_model,
        max_turns=request.max_turns or settings.max_builder_turns,
        on_event=on_event,
    )

    candidate = toolbox.accepted
    if candidate is None:
        reason = result.error or "the repair agent produced no script that validated and ran"
        if toolbox.proposals and toolbox.proposals[-1].errors:
            reason = f"last proposal did not validate: {toolbox.proposals[-1].errors}"
        return RepairResult(
            ok=False,
            usage=result.usage,
            transcript_path=result.transcript_path,
            error=reason,
            capability_gaps=list(toolbox.capability_gaps),
        )

    candidate = candidate.model_copy(update={"version": request.script.version + 1})
    is_minor, reasons = classify_change(request.script, candidate)

    return RepairResult(
        ok=True,
        candidate=candidate,
        diff=unified_diff(request.script, candidate),
        rationale=first_paragraph(result.text),
        is_minor=is_minor,
        reasons=reasons,
        usage=result.usage,
        transcript_path=result.transcript_path,
        capability_gaps=list(toolbox.capability_gaps),
    )
