"""Web UI tests.

Each test gets its own SQLite file under tmp_path. The empty-database tests use
the same app factory as the seeded ones, so an empty install is exercised on
every route rather than only on the overview.
"""

from __future__ import annotations

import asyncio
import pathlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.routing import Mount

from smartscraper.db.models import (
    AuditEntry,
    Delivery,
    DeliveryTarget,
    LlmUsage,
    Profile,
    ProxyPool,
    Record,
    Repair,
    Run,
    RunMetric,
    RunStatus,
    Scraper,
    ScriptVersion,
)
from smartscraper.web.app import MCP_PATH, create_app

DIFF = """@@ -20,4 +20,5 @@ steps[2].fields.price
       price:
-        selector: ".price"
+        selector: "[data-testid=price-now]"
+        fallback_selectors: [".price"]
         attr: text
"""

YAML_V1 = """version: 1
engine: browser
steps:
  - op: goto
    url: "https://shop.example.eu/pricing"
  - op: extract_list
    selector: "css=.product-card"
    as: products
"""


def _url(tmp_path, name: str = "web.db") -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


def _run(coro):
    """Run a coroutine on a private event loop.

    TestClient owns its own loop, so every direct DB check here opens a fresh
    engine against the same file rather than borrowing the app's.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _in_db(url: str, fn):
    """Call `fn(session)` against `url` on a private loop and return its result."""

    async def go():
        engine = create_async_engine(url, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                result = await fn(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return _run(go())


async def _seed(session, scripts_dir=None) -> dict[str, int]:
    """Seed two scrapers, four runs and a pending repair.

    `scripts_dir` is where Scraper.yaml_path points. Approving a repair calls
    pipeline.promote_version, which WRITES that file, so it must be a tmp_path
    and never the repository's own scrapers/ directory.
    """
    now = datetime.now(UTC)
    ids: dict[str, int] = {}
    s = session
    shop_yaml = (
        str(pathlib.Path(scripts_dir) / "shop-eu-prices.yaml")
        if scripts_dir
        else "shop-eu-prices.yaml"
    )
    if scripts_dir:
        pathlib.Path(shop_yaml).write_text("version: 7\n", encoding="utf-8")
    shop = Scraper(
        name="shop-eu-prices",
        url="https://shop.example.eu/pricing",
        goal="every product with price and currency",
        yaml_path=shop_yaml,
        schedule="0 * * * *",
        enabled=True,
        promotion_policy="auto_if_minor",
        proxy_pool="eu-resi",
        profile="shop-eu-login",
    )
    hn = Scraper(
        name="hn-frontpage",
        url="https://news.ycombinator.com",
        yaml_path="",  # no file: promotion returns None for this one
        schedule="*/15 * * * *",
        enabled=True,
    )
    s.add_all([shop, hn])
    await s.flush()
    ids["shop"] = shop.id
    ids["hn"] = hn.id

    s.add_all(
        [
            ScriptVersion(scraper_id=shop.id, version=7, yaml=YAML_V1, status="active", created_by="repair"),
            ScriptVersion(
                scraper_id=shop.id, version=8, yaml=YAML_V1, status="candidate", created_by="repair",
                change_summary="price selector fix", is_minor=True,
            ),
            ScriptVersion(
                scraper_id=hn.id, version=1, yaml=YAML_V1, status="active", created_by="builder",
                has_custom_python=True,
            ),
        ]
    )

    good = Run(
        scraper_id=shop.id, script_version=7, status=RunStatus.PASSED, row_count=452,
        duration_ms=29000, engine_used="patchright", created_at=now - timedelta(hours=2),
        finished_at=now - timedelta(hours=2),
    )
    bad = Run(
        scraper_id=shop.id, script_version=7, status=RunStatus.VALIDATION_FAILED, row_count=118,
        duration_ms=41000, engine_used="patchright+proxy", escalation_level=3,
        created_at=now - timedelta(minutes=10), finished_at=now - timedelta(minutes=9),
        validator_report={
            "passed": False,
            "row_count": 118,
            "rules": [
                {"rule": "max_null_rate.price", "passed": False, "measured": "0.66", "expected": "<= 0.05"},
                {"rule": "min_rows", "passed": True, "measured": "118", "expected": ">= 10"},
            ],
        },
    )
    blocked = Run(
        scraper_id=hn.id, script_version=1, status=RunStatus.BLOCKED, engine_used="http",
        block_reason="cloudflare", created_at=now - timedelta(hours=6),
    )
    running = Run(
        scraper_id=hn.id, script_version=1, status=RunStatus.RUNNING, engine_used="http",
        created_at=now - timedelta(minutes=1), started_at=now - timedelta(minutes=1),
    )
    s.add_all([good, bad, blocked, running])
    await s.flush()
    ids["good_run"] = good.id
    ids["bad_run"] = bad.id
    ids["running_run"] = running.id

    s.add_all(
        [
            RunMetric(run_id=bad.id, field="price", null_rate=0.66, distinct_count=40, sample="8.95"),
            RunMetric(run_id=bad.id, field="name", null_rate=0.0, distinct_count=118, sample="Lavazza"),
            RunMetric(run_id=good.id, field="price", null_rate=0.0, distinct_count=452, sample="8.95"),
            Record(
                run_id=good.id, scraper_id=shop.id, source="script", row_hash="a" * 8,
                data={"name": "Lavazza Qualita Rossa 1kg", "price": 8.95, "currency": "EUR"},
            ),
            Record(
                run_id=bad.id, scraper_id=shop.id, source="llm_fallback", row_hash="b" * 8,
                data={"name": "Illy Classico Beans 1kg", "price": None, "currency": "EUR"},
            ),
            LlmUsage(scraper_id=shop.id, run_id=bad.id, agent="repair", model="claude-opus-5", cost_usd=0.22),
            LlmUsage(scraper_id=shop.id, run_id=bad.id, agent="fallback",
                     model="claude-sonnet-5", cost_usd=0.09),
            Profile(name="shop-eu-login", domain="shop.example.eu", kind="storage_state",
                    expires_at=now + timedelta(days=22)),
            ProxyPool(name="eu-resi", vendor="Decodo", kind="residential", gb_used=4.2, cost_usd=16.8),
            AuditEntry(actor="you", action="approved version", object_type="scraper",
                       object_ref="shop-eu-prices v7", detail="reviewed"),
        ]
    )

    target = DeliveryTarget(scraper_id=shop.id, kind="webhook", config={"url": "https://ops.internal/hooks"})
    s.add(target)
    await s.flush()
    s.add(Delivery(run_id=good.id, target_id=target.id, status="sent", rows_sent=452))

    repair = Repair(
        scraper_id=shop.id, run_id=bad.id, candidate_version=8, status="pending_approval",
        reason="price selector returned empty on 28 of 42 cards", diff=DIFF, is_minor=True,
        test_run_id=good.id,
    )
    s.add(repair)
    await s.flush()
    ids["repair"] = repair.id
    return ids


@pytest.fixture
def empty_client(tmp_path) -> Iterator[TestClient]:
    url = _url(tmp_path, "empty.db")
    app = create_app(create_async_engine(url, future=True))
    with TestClient(app) as client:
        client.db_url = url
        yield client


@pytest.fixture
def client(tmp_path) -> Iterator[tuple[TestClient, dict[str, int], str]]:
    url = _url(tmp_path, "seeded.db")
    scripts = tmp_path / "scrapers"
    scripts.mkdir()
    app = create_app(create_async_engine(url, future=True))
    with TestClient(app) as client:
        ids = _in_db(url, lambda s: _seed(s, scripts))
        client.db_url = url
        client.scripts_dir = scripts
        yield client, ids, url


# ------------------------------------------------------------------- health
def test_healthz(empty_client: TestClient) -> None:
    r = empty_client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------------------------------------------------------- empty database
EMPTY_ROUTES = [
    "/", "/scrapers", "/scrapers/new", "/runs", "/records", "/repairs",
    "/network", "/coverage", "/delivery", "/mcp-console", "/audit", "/settings", "/styleguide",
    "/builder", "/palette",
    "/partials/activity", "/partials/attention", "/partials/runs",
]


@pytest.mark.parametrize("path", EMPTY_ROUTES)
def test_empty_database_renders(empty_client: TestClient, path: str) -> None:
    r = empty_client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:400]}"


def test_empty_overview_is_the_first_run_screen(empty_client: TestClient) -> None:
    body = empty_client.get("/").text
    assert "No scrapers yet" in body
    assert "Start builder" in body


def test_empty_lists_render_an_empty_state(empty_client: TestClient) -> None:
    assert "No scrapers yet" in empty_client.get("/scrapers").text
    assert "No runs yet" in empty_client.get("/runs").text
    assert "No records yet" in empty_client.get("/records").text
    assert "Nothing to approve" in empty_client.get("/repairs").text
    assert "No profiles" in empty_client.get("/network").text
    assert "No delivery targets" in empty_client.get("/delivery").text


# ------------------------------------------------------------- seeded pages
def test_seeded_routes(client) -> None:
    c, ids, _ = client
    paths = [
        "/", "/scrapers", f"/scrapers/{ids['shop']}", f"/scrapers/{ids['shop']}/script",
        f"/scrapers/{ids['shop']}/schema", "/scrapers/new", "/runs", f"/runs/{ids['bad_run']}",
        f"/runs/{ids['running_run']}/live", "/records", f"/records?scraper_id={ids['shop']}",
        "/repairs", f"/repairs/{ids['repair']}", "/network", "/coverage", "/delivery",
        "/mcp-console", "/audit", "/settings", "/styleguide", "/builder", "/palette",
        "/partials/activity", "/partials/attention", "/partials/runs",
    ]
    for path in paths:
        r = c.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:400]}"


def test_overview_names_the_failing_scraper_and_the_rule(client) -> None:
    c, _, _ = client
    body = c.get("/").text
    assert "shop-eu-prices" in body
    assert "max_null_rate.price" in body


def test_run_detail_shows_the_validator_report(client) -> None:
    c, ids, _ = client
    body = c.get(f"/runs/{ids['bad_run']}").text
    assert "max_null_rate.price" in body
    assert "0.66" in body


def test_records_marks_llm_fallback_rows(client) -> None:
    c, ids, _ = client
    body = c.get(f"/records?scraper_id={ids['shop']}").text
    assert "llm_fallback" in body or "llm" in body


def test_running_run_redirects_to_live(client) -> None:
    c, ids, _ = client
    r = c.get(f"/runs/{ids['running_run']}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/live")


def test_unbuilt_screens_answer_instead_of_404ing(client) -> None:
    """A screen with no implementation still answers, and says what is missing.

    Only the command palette is left. Builder live is real now, and this test
    used to assert it said otherwise, which is why it failed the moment the
    builder was wired up. A test that asserts a stub behaves like a stub goes on
    passing long after it should have started failing, and then fails for the
    wrong reason once the real thing lands.
    """
    c, _, _ = client
    r = c.get("/palette")
    assert r.status_code == 200
    assert "command palette is not built" in r.text
    assert "not built" in r.text


def test_builder_live_is_a_real_screen_now(client) -> None:
    """It lists builds. It must not still advertise itself as missing."""
    c, _, _ = client
    r = c.get("/builder")
    assert r.status_code == 200
    assert "builder is not wired up" not in r.text
    assert "not built" not in r.text
    # with nothing running it explains itself rather than showing a bare shell
    assert "No build has run" in r.text


def test_style_guide_maps_every_screen_in_the_inventory(client) -> None:
    c, _, _ = client
    body = c.get("/styleguide").text
    for name in ("Overview", "Scraper detail", "Builder live", "Repair review",
                 "Schema and field health", "MCP console", "First run",
                 "Command palette", "Phone triage"):
        assert name in body, f"{name} missing from the screens map"
    assert "23" in body


def test_unknown_run_is_404(client) -> None:
    c, _, _ = client
    assert c.get("/runs/999999").status_code == 404


def test_scraper_filter_narrows_the_list_and_has_its_own_empty_state(client) -> None:
    c, _, _ = client
    hit = c.get("/scrapers?q=shop").text
    assert "shop-eu-prices" in hit
    assert "hn-frontpage" not in hit
    miss = c.get("/scrapers?q=nothing-matches-this").text
    assert "Nothing matches" in miss


def test_unknown_scraper_is_404(client) -> None:
    c, _, _ = client
    assert c.get("/scrapers/999999").status_code == 404


# ------------------------------------------------------------- interactions
def test_acknowledge_writes_the_scraper_and_an_audit_entry(client) -> None:
    c, ids, url = client
    r = c.post(
        f"/scrapers/{ids['shop']}/acknowledge",
        data={"hours": "24", "note": "known, waiting on v8", "back": "/"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    async def check(s):
        scraper = await s.get(Scraper, ids["shop"])
        entries = (await s.scalars(select(AuditEntry))).all()
        return scraper.acknowledged_until, scraper.ack_note, [e.action for e in entries]

    until, note, logged = _in_db(url, check)
    assert until is not None
    assert note == "known, waiting on v8"
    assert "acknowledged" in logged


def test_reopen_clears_the_acknowledgement(client) -> None:
    c, ids, url = client
    c.post(f"/scrapers/{ids['shop']}/acknowledge", data={"hours": "24"}, follow_redirects=False)
    c.post(f"/scrapers/{ids['shop']}/reopen", data={"back": "/"}, follow_redirects=False)

    async def check(s):
        return (await s.get(Scraper, ids["shop"])).acknowledged_until

    assert _in_db(url, check) is None


def test_disable_then_enable(client) -> None:
    c, ids, url = client

    async def enabled(s):
        return (await s.get(Scraper, ids["shop"])).enabled

    c.post(f"/scrapers/{ids['shop']}/disable", data={"back": "/scrapers"}, follow_redirects=False)
    assert _in_db(url, enabled) is False
    c.post(f"/scrapers/{ids['shop']}/enable", data={"back": "/scrapers"}, follow_redirects=False)
    assert _in_db(url, enabled) is True


def test_run_now_queues_a_run(client) -> None:
    c, ids, url = client
    r = c.post(f"/scrapers/{ids['shop']}/run", follow_redirects=False)
    assert r.status_code == 303

    async def queued(s):
        return (await s.scalars(select(Run).where(Run.status == RunStatus.QUEUED))).all()

    assert len(_in_db(url, queued)) == 1


def test_approve_repair_promotes_the_candidate(client) -> None:
    c, ids, url = client
    r = c.post(f"/repairs/{ids['repair']}/approve", data={"back": "/repairs"}, follow_redirects=False)
    assert r.status_code == 303

    async def check(s):
        repair = await s.get(Repair, ids["repair"])
        versions = (
            await s.scalars(select(ScriptVersion).where(ScriptVersion.scraper_id == ids["shop"]))
        ).all()
        actions_logged = [e.action for e in (await s.scalars(select(AuditEntry))).all()]
        return repair.status, repair.decided_by, {v.version: v.status for v in versions}, actions_logged

    status, by, versions, logged = _in_db(url, check)
    assert status == "approved"
    assert by == "you"
    assert versions[8] == "active"
    assert versions[7] == "retired"
    assert "approved version" in logged
    assert "promoted version" in logged, "pipeline.promote_version did not run"

    # The table and the file on disk must agree after an approval.
    written = (c.scripts_dir / "shop-eu-prices.yaml").read_text(encoding="utf-8")
    assert written == YAML_V1, "scrapers/*.yaml was not rewritten to the promoted version"


def test_reject_repair_marks_the_candidate_rejected(client) -> None:
    c, ids, url = client
    c.post(
        f"/repairs/{ids['repair']}/reject",
        data={"reason": "not minor", "back": "/repairs"},
        follow_redirects=False,
    )

    async def check(s):
        repair = await s.get(Repair, ids["repair"])
        candidate = await s.scalar(
            select(ScriptVersion).where(
                ScriptVersion.scraper_id == ids["shop"], ScriptVersion.version == 8
            )
        )
        return repair.status, candidate.status

    assert _in_db(url, check) == ("rejected", "rejected")


def test_bulk_rerun_queues_one_run_per_selection(client) -> None:
    c, ids, url = client
    r = c.post(
        "/runs/bulk/rerun",
        data={"run_id": [str(ids["good_run"]), str(ids["bad_run"])], "back": "/runs"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    async def queued(s):
        return (await s.scalars(select(Run).where(Run.trigger == "rerun"))).all()

    assert len(_in_db(url, queued)) == 2


# ---------------------------------------------------------------------- SSE
def test_log_stream_emits_rendered_lines(client, tmp_path) -> None:
    c, ids, url = client
    log = tmp_path / "run.log"
    log.write_text(
        '{"ts": "2026-09-17T12:04:01.000+00:00", "level": "info", "tag": "runner", "msg": "starting"}\n'
        '{"ts": "2026-09-17T12:04:02.000+00:00", "level": "error", "tag": "validate", "msg": "FAIL"}\n',
        encoding="utf-8",
    )

    async def attach(s):
        run = await s.get(Run, ids["good_run"])
        run.log_path = str(log)

    _in_db(url, attach)

    body = ""
    with c.stream("GET", f"/runs/{ids['good_run']}/log/stream") as r:
        assert r.status_code == 200
        for chunk in r.iter_text():
            body += chunk
            if "event: done" in body:
                break
    assert "starting" in body
    assert "l-err" in body


def test_run_live_page_wires_the_stream(client) -> None:
    c, ids, _ = client
    body = c.get(f"/runs/{ids['running_run']}/live").text
    assert f"/runs/{ids['running_run']}/log/stream" in body
    assert "sse-swap" in body


# ---------------------------------------------------------------------- MCP
def test_mcp_endpoint_is_mounted_and_owns_its_path(client) -> None:
    """/mcp belongs to the protocol endpoint, not to a page.

    This asserts the mount exists, not that the protocol works: streamable HTTP
    refuses a session-less browser GET, so the status is a redirect or a 4xx and
    never 200. A missing mount would give 404, which is the thing being ruled
    out. The protocol itself is tested over a real socket in tests/test_mcp_http.py.
    """
    c, _, _ = client
    mounts = [r for r in c.app.routes if isinstance(r, Mount) and r.path == MCP_PATH]
    assert mounts, f"nothing mounted at {MCP_PATH}"
    r = c.get("/mcp/")
    assert r.status_code != 404, "the MCP app is not mounted"
    assert "text/html" not in r.headers.get("content-type", "")


def test_healthz_reports_the_mount(client) -> None:
    c, _, _ = client
    assert c.get("/healthz").json()["mcp"] is True


def test_app_can_be_built_without_the_mcp_mount(tmp_path) -> None:
    app = create_app(create_async_engine(_url(tmp_path, "nomcp.db"), future=True), mount_mcp=False)
    with TestClient(app) as c:
        assert c.get("/healthz").json()["mcp"] is False
        assert c.get("/mcp/").status_code == 404
        assert c.get("/mcp-console").status_code == 200


def test_console_lists_the_servers_real_tools_and_resources(client) -> None:
    c, _, _ = client
    body = c.get("/mcp-console").text
    for tool in ("list_scrapers", "get_scraper", "create_scraper", "run_scraper", "get_run",
                 "get_results", "search_results", "get_pending_repairs", "approve_repair"):
        assert tool in body, f"{tool} missing from the console"
    for uri in ("scraper://{name}/script", "scraper://{name}/schema", "run://{run_id}/log"):
        assert uri in body, f"{uri} missing from the console"


def test_console_marks_the_destructive_tool(client) -> None:
    c, _, _ = client
    body = c.get("/mcp-console?tool=approve_repair").text
    assert "destructive" in body


def test_endpoint_url_carries_the_trailing_slash(client) -> None:
    c, _, _ = client
    for path in ("/mcp-console", "/delivery", "/settings"):
        assert "/mcp/" in c.get(path).text, f"{path} does not show the endpoint URL"


def test_no_token_is_shown_as_loopback_only(client) -> None:
    """With no SS_MCP_BEARER_TOKEN the screens say the endpoint is unauthenticated."""
    c, _, _ = client
    assert "loopback only" in c.get("/delivery").text


def test_insecure_bind_is_refused(monkeypatch, tmp_path) -> None:
    """Building the app for a non-loopback host with no token must raise."""
    from smartscraper.config import get_settings
    from smartscraper.mcp.server import InsecureBindError

    monkeypatch.setenv("SS_HOST", "0.0.0.0")
    monkeypatch.delenv("SS_MCP_BEARER_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(InsecureBindError):
            create_app(create_async_engine(_url(tmp_path, "bind.db"), future=True))
    finally:
        get_settings.cache_clear()


# ------------------------------------------------------------ style system
def test_stylesheet_carries_the_token_set(empty_client: TestClient) -> None:
    css = empty_client.get("/static/app.css").text
    assert '[data-theme="light"]' in css
    assert "prefers-color-scheme" in css
    assert "IBM Plex Mono" in css
    assert "tabular-nums" in css
    assert "--accent: #35C2C2" in css


def test_pages_use_real_controls_and_label_icon_buttons(client) -> None:
    c, _, _ = client
    body = c.get("/scrapers").text
    assert "<button" in body
    assert "aria-label=" in body
    assert "<table" in body
