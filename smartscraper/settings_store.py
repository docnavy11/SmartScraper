"""Settings that can be changed without editing a file and restarting.

`.env` and the environment remain the place for secrets and for anything needed
before a database exists: where the database is, what to bind, the encryption
key, the bearer token. Everything else lives in the `setting` table and can be
edited in the UI.

The split is not about which module owns which file. It is about two real
properties: a value that is read once at process start cannot usefully be
changed while the process runs, and a secret should not round-trip through a
browser form. `EDITABLE` lists exactly what fails neither test.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper import repo
from smartscraper.config import Settings, get_settings, set_overrides
from smartscraper.db.models import Setting

log = logging.getLogger(__name__)

# Read at process start, or holds a secret. Neither belongs in a web form.
FIXED: frozenset[str] = frozenset({
    "data_dir", "scrapers_dir", "db_path",
    "host", "port", "allow_insecure_bind",
    "web_user", "web_password", "mcp_bearer_token", "secret_key",
})

EDITABLE: tuple[str, ...] = tuple(n for n in Settings.model_fields if n not in FIXED)

# Grouped the way the settings screen reads, so the UI does not invent its own
# arrangement and drift from this one.
GROUPS: dict[str, tuple[str, ...]] = {
    "Models & agents": (
        "builder_model", "repair_model", "fallback_model", "agent_effort",
        "max_builder_turns", "snapshot_budget_bytes",
    ),
    "Budgets": ("monthly_budget", "default_scraper_budget", "stop_at_budget"),
    "Engines & etiquette": (
        "default_engine", "headed", "max_concurrent_runs", "run_timeout_s",
        "respect_robots", "min_delay_s", "max_delay_s", "runner_cmd",
    ),
    "Solvers": (
        "byparr_url", "captcha_vendor", "captcha_key",
        "auto_solve_turnstile", "max_captcha_spend_per_run",
    ),
    "Retention": ("keep_artifacts_days", "keep_traces_days", "keep_audit_days"),
    "Credentials": ("anthropic_api_key",),
}

SECRETISH: frozenset[str] = frozenset({"anthropic_api_key", "captcha_key"})


def _model_choices() -> list[tuple[str, str]]:
    """The models this project can actually run, priced.

    Derived from `agents.cost.PRICES` rather than written out again. A model
    missing from that table raises `UnknownModelError` when usage is recorded,
    which would break budget tracking, so offering one here would be offering a
    choice that breaks the thing the budget guard depends on. The price is in the
    label because it is the reason anyone picks one model over another.
    """
    from smartscraper.agents.cost import PRICES

    out = []
    for name, price in sorted(PRICES.items(), key=lambda kv: -kv[1].input):
        short = name.removeprefix("claude-")
        out.append((name, f"{short}  ·  ${price.input:g} in / ${price.output:g} out per Mtok"))
    return out


def _engine_choices() -> list[tuple[str, str]]:
    from smartscraper.dsl.models import Engine

    notes = {
        "auto": "start cheap, climb only when blocked",
        "http": "no browser; fastest, and beats a headless fingerprint on some sites",
        "patchright": "chromium with the automation tells patched out",
        "playwright": "plain chromium; easiest to detect",
        "camoufox": "firefox built to spoof a fingerprint",
    }
    return [(e.value, f"{e.value}  ·  {notes.get(e.value, '')}") for e in Engine]


#: Settings with a knowable set of valid values. Anything here renders as a
#: select, and a value outside the set is refused on save rather than failing
#: later at the point of use.
CHOICES: dict[str, list[tuple[str, str]]] = {
    "builder_model": _model_choices(),
    "repair_model": _model_choices(),
    "fallback_model": _model_choices(),
    "default_engine": _engine_choices(),
    "agent_effort": [
        ("low", "low  ·  cheapest, for simple pages"),
        ("medium", "medium"),
        ("high", "high  ·  the default"),
        ("xhigh", "xhigh  ·  best for agentic work"),
        ("max", "max  ·  when correctness beats cost"),
    ],
}


def field_type(name: str) -> str:
    """What kind of control the form should render."""
    if name in CHOICES:
        return "choice"
    annotation = str(Settings.model_fields[name].annotation)
    if "bool" in annotation:
        return "bool"
    if "int" in annotation:
        return "int"
    if "float" in annotation:
        return "float"
    return "text"


def describe(name: str) -> str:
    return Settings.model_fields[name].description or ""


def coerce(name: str, raw: str) -> Any:
    """Turn one form value into the type the field wants.

    An empty box means "no override", which is not the same as an empty string:
    it restores whatever the environment says.
    """
    raw = (raw or "").strip()
    kind = field_type(name)
    if kind == "choice":
        return raw or None
    if kind == "bool":
        return raw.lower() in ("1", "true", "on", "yes")
    if raw == "":
        return None
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    return raw


async def load(session: AsyncSession) -> dict[str, Any]:
    """Read every override and apply it. Called at startup and after a save."""
    rows = (await session.scalars(select(Setting))).all()
    values = {r.key: (r.value or {}).get("v") for r in rows if r.key in Settings.model_fields}
    values = {k: v for k, v in values.items() if v is not None}
    set_overrides(values)
    return values


async def save(
    session: AsyncSession, changes: dict[str, Any], *, actor: str = "you"
) -> tuple[dict[str, Any], list[str]]:
    """Validate, persist and apply. Returns what changed and what was rejected.

    Validation happens against the real `Settings` model before anything is
    written, so a bad value is refused rather than stored and then crashing the
    next thing that reads it.
    """
    rejected: list[str] = []
    candidate = dict(await load(session))
    proposed: dict[str, Any] = {}

    for key, value in changes.items():
        if key not in EDITABLE:
            rejected.append(f"{key} is not editable here")
            continue
        if key in CHOICES and value is not None:
            allowed = [v for v, _ in CHOICES[key]]
            if value not in allowed:
                rejected.append(f"{key}: {value!r} is not one of {', '.join(allowed)}")
                continue
        proposed[key] = value

    merged = {**candidate, **{k: v for k, v in proposed.items() if v is not None}}
    for key, value in proposed.items():
        if value is None:
            merged.pop(key, None)
    try:
        Settings(**merged)
    except ValidationError as exc:
        for err in exc.errors():
            field = err["loc"][0] if err["loc"] else "?"
            rejected.append(f"{field}: {err['msg']}")
        return {}, rejected

    before = Settings(**candidate)
    applied: dict[str, Any] = {}
    for key, value in proposed.items():
        old = getattr(before, key, None)
        row = await session.scalar(select(Setting).where(Setting.key == key))
        if value is None:
            if row is not None:
                await session.delete(row)
                applied[key] = None
            continue
        if old == value and row is not None:
            continue
        if row is None:
            session.add(Setting(key=key, value={"v": value}, updated_by=actor))
        else:
            row.value = {"v": value}
            row.updated_by = actor
        applied[key] = value

    if applied:
        await session.flush()
        await load(session)
        detail = ", ".join(
            f"{k}={_redact(k, v)}" if v is not None else f"{k} back to the environment"
            for k, v in applied.items()
        )
        await repo.log(
            session, actor=actor, action="changed settings",
            object_type="settings", object_ref=", ".join(sorted(applied)), detail=detail[:400],
        )
    return applied, rejected


def _redact(key: str, value: Any) -> str:
    if key in SECRETISH and value:
        text = str(value)
        return f"…{text[-4:]}" if len(text) > 4 else "set"
    return str(value)


def current_view() -> list[dict[str, Any]]:
    """Every editable setting with its value, type and whether it is overridden."""
    from smartscraper.config import current_overrides

    overrides = current_overrides()
    live = get_settings()
    out: list[dict[str, Any]] = []
    for group, names in GROUPS.items():
        for name in names:
            if name not in EDITABLE:
                continue
            value = getattr(live, name, None)
            out.append({
                "group": group,
                "name": name,
                "value": "" if value is None else value,
                "type": field_type(name),
                "help": describe(name),
                "overridden": name in overrides,
                "secret": name in SECRETISH,
                "choices": CHOICES.get(name, []),
            })
    return out
