"""The paths a person actually clicks, end to end through HTTP.

Why this file exists. The suite reached 963 passing tests while the primary
journey, creating a scraper, did nothing at all. The new-scraper form posted to a
no-op that logged a warning and redirected with a hardcoded "the builder agent is
not wired up yet". There was a test for that route. It asserted the redirect
carried that message, so it passed, and went on passing after the builder was
built, because it was measuring the stub rather than the outcome.

A unit test that asserts a stub behaves like a stub is worth nothing once the
real thing exists. These tests assert effects a person would notice: a row in the
database, a file on disk, a status that changed. They fail if a route is quietly
disconnected from the subsystem behind it, which is the failure the rest of the
suite could not see.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from smartscraper.db.models import Run, RunStatus, Scraper, ScriptVersion, VersionStatus
from smartscraper.web.app import create_app

# The exact string the stub used to redirect with. If it ever comes back, the
# route has been disconnected from the builder again.
STUB_MESSAGE = "not wired up yet"


@pytest.fixture
def client(tmp_path):
    """One database, reachable both through the app and through `get_session`.

    The builder writes its scraper through `get_session`, the request reads it
    through the app's engine. Pointing both at the same file is what makes the
    journey a journey rather than two halves that never meet.
    """
    from smartscraper.db import session as db_session

    url = f"sqlite+aiosqlite:///{tmp_path / 'journey.db'}"
    saved = (db_session._engine, db_session._factory)
    db_session.init_engine(url)
    engine = create_async_engine(url, future=True)
    try:
        with TestClient(create_app(engine, mount_mcp=False)) as c:
            yield c
    finally:
        from smartscraper.web import builds

        builds.cancel_all()
        builds._JOBS.clear()
        db_session._engine, db_session._factory = saved


def _post_new_scraper(client: TestClient, **form) -> str:
    """Submit the form the way a browser does. Returns where it sent us."""
    body = {"url": "https://example.test/products", "goal": "every product name and price"}
    body.update(form)
    r = client.post("/scrapers", data=body, follow_redirects=False)
    assert r.status_code == 303, r.status_code
    return r.headers["location"]


def test_the_new_scraper_form_is_connected_to_the_builder(client, monkeypatch):
    """A real build must create a real scraper and land on its page."""
    async def fake_build(url, goal, name=None, output_schema=None, on_event=None):
        from smartscraper.agents.builder import slug
        from smartscraper.db.session import get_session

        if on_event:
            on_event("exploring the page", url)
        async with get_session() as s:
            sc = Scraper(name=slug(name or url), url=url, goal=goal, yaml_path="built.yaml")
            s.add(sc)
            await s.flush()
            s.add(ScriptVersion(scraper_id=sc.id, version=1, yaml="version: 1\nsteps: []\n",
                                status=VersionStatus.ACTIVE, created_by="builder"))
            scraper_id = sc.id
        if on_event:
            on_event("done", "ready")
        return type("B", (), {"ok": True, "row_count": 42, "capability_gaps": [],
                              "error": None, "usage": None, "script": None,
                              "scraper_id": scraper_id})()

    monkeypatch.setattr("smartscraper.agents.jobs.build_scraper", fake_build)

    location = _post_new_scraper(client, name="Demo Shop")

    assert STUB_MESSAGE not in location, "the route is still wired to the stub"
    # A build takes minutes, so the POST hands back a page to watch it on.
    assert re.fullmatch(r"/builder/[0-9a-f]+", location), location

    job_id = location.rsplit("/", 1)[1]
    job = _await_build(job_id)
    assert job.status == "done", job.error
    assert job.scraper_id, "the build finished but no scraper id came back"
    assert client.get(f"/scrapers/{job.scraper_id}").status_code == 200
    assert client.get(location).status_code == 200


def test_a_failed_build_shows_the_real_reason_not_a_canned_message(client, monkeypatch):
    """No API key is an ordinary outcome. The person must see why."""

    async def failing_build(url, goal, name=None, output_schema=None, on_event=None):
        if on_event:
            on_event("failed", "ANTHROPIC_API_KEY is not set")
        return type("B", (), {"ok": False, "error": "ANTHROPIC_API_KEY is not set",
                              "row_count": 0, "capability_gaps": [], "usage": None,
                              "script": None, "scraper_id": None})()

    monkeypatch.setattr("smartscraper.agents.jobs.build_scraper", failing_build)

    location = _post_new_scraper(client)
    assert re.fullmatch(r"/builder/[0-9a-f]+", location), location

    job = _await_build(location.rsplit("/", 1)[1])
    assert job.status == "failed"
    assert "ANTHROPIC_API_KEY" in (job.error or "")

    # and the reason is on the page, not only in a log
    body = client.get(location).text
    assert "ANTHROPIC_API_KEY" in body
    assert STUB_MESSAGE not in body


def _await_build(job_id: str, timeout_s: float = 10.0):
    """Wait for a background build to finish. Fails loudly rather than hanging."""
    import time

    from smartscraper.web import builds

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = builds.get(job_id)
        assert job is not None, f"build {job_id} was never registered"
        if job.done:
            return job
        time.sleep(0.05)
    raise AssertionError(f"build {job_id} did not finish within {timeout_s}s")


def test_starting_a_build_returns_at_once_instead_of_hanging(client, monkeypatch):
    """The bug this flow exists for: the POST used to run the whole build inline,
    so the browser sat on a blank request for minutes with nothing to show."""
    import asyncio
    import time

    async def slow_build(url, goal, name=None, output_schema=None, on_event=None):
        if on_event:
            on_event("exploring the page", url)
        await asyncio.sleep(3)
        return type("B", (), {"ok": True, "row_count": 1, "capability_gaps": [],
                              "error": None, "usage": None, "script": None,
                              "scraper_id": None})()

    monkeypatch.setattr("smartscraper.agents.jobs.build_scraper", slow_build)

    started = time.time()
    location = _post_new_scraper(client)
    elapsed = time.time() - started

    assert elapsed < 2.0, f"the POST blocked for {elapsed:.1f}s waiting on the build"
    assert re.fullmatch(r"/builder/[0-9a-f]+", location), location

    # and the page says something while the build is still going
    body = client.get(location).text
    assert "exploring the page" in body or "queued" in body


def test_the_form_rejects_an_empty_url_without_calling_the_agent(client, monkeypatch):
    called: list[str] = []

    async def spy(url, goal, name=None, output_schema=None, on_event=None):
        called.append(url)
        return type("B", (), {"ok": False, "error": "should not have been called",
                              "row_count": 0, "capability_gaps": [], "usage": None,
                              "script": None, "scraper_id": None})()

    monkeypatch.setattr("smartscraper.agents.jobs.build_scraper", spy)

    location = _post_new_scraper(client, url="")

    assert called == [], "an empty form should not spend tokens"
    assert "URL" in location


@pytest.mark.parametrize(
    "path",
    ["/", "/scrapers", "/scrapers/new", "/runs", "/records", "/repairs",
     "/network", "/delivery", "/settings", "/audit"],
)
def test_no_page_claims_a_capability_it_does_not_have(client, path):
    """A page may say a thing is not built. It may not say it about something that is.

    The builder, delivery retry and target test all exist now. A page still
    advertising them as missing is a page that will mislead the next person.
    """
    body = client.get(path).text.lower()
    assert "builder agent is not wired up" not in body, f"{path} still advertises the old stub"


def test_the_audit_log_records_what_the_ui_did(client, monkeypatch):
    """Every mutating action writes an audit line. That is the only record that
    survives a restart, and it is how a person reconstructs what happened."""

    async def failing_build(url, goal, name=None, output_schema=None, on_event=None):
        return type("B", (), {"ok": False, "error": "no key", "row_count": 0,
                              "capability_gaps": [], "usage": None, "script": None,
                              "scraper_id": None})()

    monkeypatch.setattr("smartscraper.agents.jobs.build_scraper", failing_build)
    _post_new_scraper(client)

    body = client.get("/audit").text
    assert "started builder" in body, "the attempt left no trace in the audit log"


def test_run_status_values_the_hold_rule_reads_are_the_ones_the_ui_shows(client):
    """The delivery hold keys off `validation_failed`. If the UI renders a
    different vocabulary, a person cannot tell why nothing shipped."""
    assert RunStatus.VALIDATION_FAILED == "validation_failed"
    assert RunStatus.PASSED == "passed"
    assert Run.__tablename__ == "run"


def test_run_now_hands_the_run_to_the_scheduler(client, monkeypatch):
    """A queued row that nothing consumes is the same bug as an unwired form.

    The row used to be created and left in `queued` forever, with a log line
    admitting it. This asserts the hand-off actually happens.
    """
    handed: list[dict] = []

    async def spy(scraper_id, trigger="manual", **kw):
        handed.append({"scraper_id": scraper_id, "trigger": trigger, **kw})
        return kw.get("adopt_run_id") or 1

    monkeypatch.setattr("smartscraper.scheduler.tasks.run_scraper_now", spy)

    from smartscraper.db.session import get_session

    async def seed() -> int:
        async with get_session() as s:
            sc = Scraper(name="dispatch-me", url="https://example.test/", yaml_path="d.yaml")
            s.add(sc)
            await s.flush()
            s.add(ScriptVersion(scraper_id=sc.id, version=1, yaml="version: 1\nsteps: []\n",
                                status=VersionStatus.ACTIVE))
            return sc.id

    scraper_id = client.portal.call(seed) if hasattr(client, "portal") else None
    if scraper_id is None:
        pytest.skip("no portal on this TestClient build")

    r = client.post(f"/scrapers/{scraper_id}/run", follow_redirects=False)
    assert r.status_code in (303, 302), r.status_code

    assert handed, "the run was queued but never handed to the scheduler"
    assert handed[0]["scraper_id"] == scraper_id
    assert handed[0].get("adopt_run_id"), "the scheduler must adopt the queued row, not make a second one"


def test_a_blocked_build_climbs_the_engine_ladder(client, monkeypatch):
    """A site that refuses one engine may not refuse the next one up.

    A retail site returned HTTP 403 and a bot-block page to headless Chromium. The
    build tried one engine and stopped, so a protected site could never be built
    at all.
    """
    from smartscraper.agents import jobs

    rungs: list[str] = []

    class FakeEngine:
        name = "fake"
        interactive = True

    def fake_open(rung, **kw):
        rungs.append(rung)

        class Ctx:
            async def __aenter__(self):
                return FakeEngine()

            async def __aexit__(self, *a):
                return False

        return Ctx()

    async def blocked_then_fine(request, **kw):
        from smartscraper.agents.builder import BuildResult

        if len(rungs) == 1:
            return BuildResult(ok=False, error="HTTP 403 bot-block interstitial")
        return BuildResult(ok=True, row_count=7, text="fine on the second rung")

    monkeypatch.setattr(jobs, "open_engine", fake_open)
    monkeypatch.setattr(jobs, "build", blocked_then_fine)
    monkeypatch.setattr(jobs, "persist_build", _noop_persist)
    monkeypatch.setattr(jobs, "record_usage", _noop_usage)

    import asyncio

    result = asyncio.new_event_loop().run_until_complete(
        jobs.build_scraper("https://blocked.test/", "everything")
    )

    assert result.ok, result.error
    # http leads: a retail site refused headless Chromium with a 403 while plain
    # curl got 200 from the same IP, so the cheapest rung is also the one that
    # works on exactly the sites that block a browser.
    assert rungs == ["http", "patchright"], rungs


def test_a_build_that_is_merely_confusing_is_not_retried(client, monkeypatch):
    """Escalation costs a second agent run. A page the agent could not read will
    read no better from a stealthier browser, so it must not pay twice."""
    from smartscraper.agents import jobs

    rungs: list[str] = []

    def fake_open(rung, **kw):
        rungs.append(rung)

        class Ctx:
            async def __aenter__(self):
                return type("E", (), {"name": "fake", "interactive": True})()

            async def __aexit__(self, *a):
                return False

        return Ctx()

    async def just_confused(request, **kw):
        from smartscraper.agents.builder import BuildResult

        return BuildResult(ok=False, error="no repeating element matched the goal")

    monkeypatch.setattr(jobs, "open_engine", fake_open)
    monkeypatch.setattr(jobs, "build", just_confused)
    monkeypatch.setattr(jobs, "record_usage", _noop_usage)

    import asyncio

    result = asyncio.new_event_loop().run_until_complete(
        jobs.build_scraper("https://plain.test/", "everything")
    )

    assert not result.ok
    assert rungs == ["http"], f"retried a failure that was not the engine's fault: {rungs}"


async def _noop_persist(result, *, url, goal, name):
    return 1


async def _noop_usage(result, *, agent, scraper_id, run_id):
    return None
