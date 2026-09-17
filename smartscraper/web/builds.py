"""In-flight builds, so a person is never left staring at a spinner.

A build takes minutes: a browser opens, an agent explores, probes selectors,
writes a script, and test-runs it. Doing that inside the HTTP request meant the
form POST hung with no output at all until it finished or the browser gave up.

So the request starts the work and returns immediately with somewhere to watch
it. This registry holds what is running and what each one has reported.

Deliberately in memory. A build that was interrupted by a restart did not
happen, and resuming one is meaningless: the browser is gone. What survives a
restart is what the build produced, which is a scraper row and a YAML file.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

# Keep a bounded history so the page still explains itself after a build ends.
MAX_JOBS = 40


@dataclass
class Event:
    at: datetime
    stage: str
    detail: str = ""

    @property
    def clock(self) -> str:
        return self.at.strftime("%H:%M:%S")


@dataclass
class BuildJob:
    id: str
    url: str
    goal: str
    name: str | None = None
    status: str = "running"          # running | done | failed
    events: list[Event] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    scraper_id: int | None = None
    scraper_name: str | None = None
    row_count: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    capability_gaps: list[str] = field(default_factory=list)
    # The agent's own last piece of reasoning. When a build fails it is usually
    # the clearest account of why, and far better than a generic summary.
    last_thought: str = ""
    final_text: str = ""
    transcript_path: str | None = None

    @property
    def elapsed_s(self) -> int:
        end = self.finished_at or datetime.now(UTC)
        return int((end - self.started_at).total_seconds())

    @property
    def done(self) -> bool:
        return self.status != "running"

    @property
    def last_stage(self) -> str:
        return self.events[-1].stage if self.events else "starting"

    @property
    def hint(self) -> str:
        """One actionable sentence about a failure, or empty.

        Derived from what the transcript actually contains, not from a guess.
        """
        if self.status != "failed":
            return ""
        # Only what the build actually reported. Stage names must not be matched:
        # "checking budget" is a step every build takes, and matching it told
        # someone their budget was spent when the agent had simply run out of
        # turns.
        haystack = " ".join([self.error or "", self.last_thought, self.final_text]).lower()

        if "maximum number of turns" in haystack or "max_turns" in haystack:
            return (
                "The agent hit its turn limit before it finished. It was making progress, so the "
                "site is probably buildable; raise SS_MAX_BUILDER_TURNS and try again, or narrow "
                "the goal to fewer fields."
            )

        if "not installed" in haystack and "escalate" in haystack:
            return (
                "The site refused the browser this build could open, and the stealthier engine it "
                "wanted next is not installed. Install camoufox, or point the scraper at a "
                "residential proxy pool, and try again."
            )
        if any(w in haystack for w in ("403", "bot", "blocked", "block page", "captcha", "challenge")):
            tried = [e.detail for e in self.events if e.stage == "opening a browser"]
            attempted = ", ".join(dict.fromkeys(tried)) or "the configured engine"
            return (
                f"The site refused every browser this build tried ({attempted}) and served a block "
                "page, so there was nothing to extract. A residential proxy pool is the next lever; "
                "some sites refuse datacentre addresses whatever the browser looks like."
            )
        if "budget" in haystack and "spent" in haystack or "BudgetRefused" in (self.error or ""):
            return "The monthly agent budget is spent. Raise it in settings or wait for the next month."
        if "already exists" in haystack:
            return "A scraper of that name already exists. Give this one a different name."
        if "timeout" in haystack or "timed out" in haystack:
            return "The page did not finish loading in time. A slower site may need a longer timeout."
        if "x server" in haystack or "display" in haystack:
            return "A headed browser cannot start here. Set SS_HEADED=false."
        return ""

    def say(self, stage: Any, detail: str = "") -> None:
        """Record a step.

        Called two ways. This module's own stages arrive as
        `say("opening a browser", "patchright")`. The agent harness forwards its
        raw message objects as a single argument, and their repr is a wall of
        JSON, so those are summarised into something a person can read.
        """
        if not isinstance(stage, str):
            described = _describe(stage)
            if described is None:
                return  # a message with nothing in it for a person to read
            stage, detail = described
        if stage == "thinking" and detail:
            self.last_thought = str(detail)
        self.events.append(Event(datetime.now(UTC), str(stage)[:60], str(detail)[:160]))
        log.info("build %s: %s %s", self.id, stage, detail)


# Envelope messages that carry no content of their own. Showing them turns the
# progress list into a stream of the words "user" and "assistant".
_NOISE = {"user", "assistant", "system", "ratelimitevent", "streamevent", "partial"}


def _describe(message: Any) -> tuple[str, str] | None:
    """Turn one agent-harness message into a stage and a short detail.

    Deliberately duck-typed. The harness is meant to be swappable, so this reads
    whatever attributes are there and falls back to a truncated repr rather than
    importing the SDK's classes and breaking when they change.
    """
    kind = type(message).__name__

    subtype = getattr(message, "subtype", None)
    if subtype == "init":
        data = getattr(message, "data", {}) or {}
        tools = data.get("tools") or []
        return "agent ready", f"{len(tools)} tools available"

    if kind.startswith("Result"):
        turns = getattr(message, "num_turns", None)
        return "agent finished", f"{turns} turns" if turns else ""

    content = getattr(message, "content", None)
    if isinstance(content, list):
        tools = [getattr(b, "name", "") for b in content if getattr(b, "name", None)]
        if tools:
            pretty = [t.rsplit("__", 1)[-1] for t in tools]
            return "calling " + ", ".join(pretty[:3]), ""
        texts = [getattr(b, "text", "") for b in content if getattr(b, "text", None)]
        if texts:
            return "thinking", " ".join(texts).strip().replace("\n", " ")[:160]

    text = getattr(message, "text", None)
    if isinstance(text, str) and text.strip():
        return "thinking", text.strip().replace("\n", " ")[:160]

    name = kind.replace("Message", "").lower()
    if name in _NOISE or not name:
        return None
    return name, ""


_JOBS: dict[str, BuildJob] = {}
_TASKS: set[asyncio.Task] = set()


def get(job_id: str) -> BuildJob | None:
    return _JOBS.get(job_id)


def recent(limit: int = 10) -> list[BuildJob]:
    return sorted(_JOBS.values(), key=lambda j: j.started_at, reverse=True)[:limit]


def _trim() -> None:
    if len(_JOBS) <= MAX_JOBS:
        return
    for job in sorted(_JOBS.values(), key=lambda j: j.started_at)[: len(_JOBS) - MAX_JOBS]:
        if job.done:
            _JOBS.pop(job.id, None)


def start(url: str, goal: str, name: str | None = None) -> BuildJob:
    """Register a build and run it in the background. Returns at once.

    If there is no running event loop, as in a synchronous test, the job is
    registered and marked failed rather than silently never starting.
    """
    job = BuildJob(id=uuid.uuid4().hex[:12], url=url, goal=goal, name=name or None)
    _JOBS[job.id] = job
    _trim()
    job.say("queued", url)

    async def run() -> None:
        from smartscraper.agents.jobs import build_scraper

        try:
            result = await build_scraper(url, goal, name=name or None, on_event=job.say)
        except Exception as exc:  # noqa: BLE001 - the page must say what happened
            log.exception("build %s raised", job.id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = datetime.now(UTC)
            return

        job.finished_at = datetime.now(UTC)
        job.row_count = getattr(result, "row_count", 0)
        job.capability_gaps = list(getattr(result, "capability_gaps", []) or [])
        usage = getattr(result, "usage", None)
        job.cost_usd = float(getattr(usage, "cost_usd", 0.0) or 0.0)
        job.final_text = str(getattr(result, "text", "") or "")[:2000]
        transcript = getattr(result, "transcript_path", None)
        job.transcript_path = str(transcript) if transcript else None
        if getattr(result, "ok", False):
            job.status = "done"
            job.scraper_id = getattr(result, "scraper_id", None)
            job.scraper_name = getattr(getattr(result, "script", None), "name", None) or name
        else:
            job.status = "failed"
            job.error = getattr(result, "error", None) or "the builder did not produce a working script"

    try:
        task = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        job.status = "failed"
        job.error = "no event loop; the build could not be started"
        job.finished_at = datetime.now(UTC)
        return job

    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return job


def cancel_all() -> int:
    """Stop every in-flight build. For shutdown and for tests.

    A build task outliving the loop it was created on raises "Event loop is
    closed" from a thread nobody is watching.
    """
    stopped = 0
    for task in list(_TASKS):
        if not task.done():
            task.cancel()
            stopped += 1
    for job in _JOBS.values():
        if not job.done:
            job.status = "failed"
            job.error = "cancelled"
            job.finished_at = datetime.now(UTC)
    return stopped


def snapshot(job: BuildJob) -> dict[str, Any]:
    """What the progress page and its event stream both render from."""
    return {
        "id": job.id,
        "status": job.status,
        "stage": job.last_stage,
        "elapsed": job.elapsed_s,
        "events": [{"clock": e.clock, "stage": e.stage, "detail": e.detail} for e in job.events],
        "scraper_id": job.scraper_id,
        "row_count": job.row_count,
        "cost_usd": job.cost_usd,
        "error": job.error,
        "capability_gaps": job.capability_gaps,
    }
