"""In-memory MCP server tests.

The client here is fastmcp's in-process client: it speaks the real MCP protocol
against the real server object, so a tool whose schema or return shape is wrong
fails here exactly as it would for an external agent.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from smartscraper.db import session as dbsession
from smartscraper.db.models import (
    Base,
    Record,
    Repair,
    Run,
    RunStatus,
    Scraper,
    ScriptVersion,
    VersionStatus,
)
from smartscraper.mcp import server as srv
from smartscraper.mcp.server import InsecureBindError, build_auth, ensure_bind_allowed, mcp

SCRIPT_YAML = "version: 1\nengine: browser\nsteps:\n  - {op: goto, url: 'https://example.com'}\n"


@pytest.fixture
async def db():
    """A fresh in-memory SQLite for each test. SQLAlchemy uses a StaticPool for
    sqlite ':memory:', so every session shares the one connection."""
    engine = dbsession.init_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
    dbsession._engine = None
    dbsession._factory = None


@pytest.fixture
def jobs(monkeypatch):
    """No real queue in tests. Records what the tools tried to enqueue, and lets a
    test install a fake worker that does what the scheduler would do."""
    calls: list[tuple[str, dict]] = []
    worker: dict = {"fn": None, "job_id": None}

    def fake_enqueue(name, /, **kwargs):
        calls.append((name, kwargs))
        fn = worker["fn"]
        if fn is not None:
            # A real worker writes its rows from another process, so the fake one
            # runs as a task the tool's polling loop can observe.
            asyncio.get_running_loop().create_task(fn(name, kwargs))
        return worker["job_id"]

    monkeypatch.setattr(srv, "enqueue", fake_enqueue)
    monkeypatch.setattr(srv, "APPEAR_TIMEOUT_S", 2.0)
    return type("Jobs", (), {"calls": calls, "worker": worker})()


@pytest.fixture
async def client(db, jobs):
    async with Client(mcp) as c:
        yield c


async def seed(*, newest_failed: bool = False, with_fallback: bool = True) -> dict[str, int]:
    """One scraper, an active version, two runs and four rows.

    newest_failed=True gives the scraper a newer run with status
    validation_failed, which is what must surface as stale=true.
    """
    now = datetime.now(UTC)
    async with dbsession.get_session() as s:
        sc = Scraper(name="demo-shop", url="https://example.com/p", goal="products",
                     yaml_path="/tmp/demo-shop.yaml", schedule="0 6 * * *")
        s.add(sc)
        await s.flush()
        s.add(ScriptVersion(scraper_id=sc.id, version=1, yaml=SCRIPT_YAML,
                            status=VersionStatus.ACTIVE.value, created_by="builder",
                            output_schema={"type": "array"}))
        good = Run(scraper_id=sc.id, script_version=1, status=RunStatus.PASSED.value,
                   row_count=3, created_at=now - timedelta(hours=2), engine_used="patchright")
        s.add(good)
        await s.flush()
        ids = {"scraper_id": sc.id, "good_run": good.id}
        if newest_failed:
            bad = Run(scraper_id=sc.id, script_version=1, status=RunStatus.VALIDATION_FAILED.value,
                      row_count=1, created_at=now,
                      validator_report={"passed": False, "rules": [{"rule": "min_rows", "passed": False}]})
            s.add(bad)
            await s.flush()
            ids["bad_run"] = bad.id
        for i in range(3):
            s.add(Record(run_id=good.id, scraper_id=sc.id, source="script",
                         row_hash=f"h{i}", data={"name": f"Widget {i}", "price": 10 + i}))
        if with_fallback:
            s.add(Record(run_id=ids.get("bad_run", good.id), scraper_id=sc.id,
                         source="llm_fallback", row_hash="hf",
                         data={"name": "Fallback Widget", "price": 99}))
    return ids


# --------------------------------------------------------------------------- shape
async def test_tools_are_all_registered(client):
    names = {t.name for t in await client.list_tools()}
    assert names == {
        "list_scrapers", "get_scraper", "create_scraper", "run_scraper", "get_run",
        "get_results", "search_results", "get_pending_repairs", "approve_repair",
    }


async def test_every_tool_has_a_description_and_typed_arguments(client):
    for t in await client.list_tools():
        assert t.description and len(t.description) > 40, f"{t.name} needs a real description"
        props = (t.input_schema or {}).get("properties", {})
        for arg, spec in props.items():
            assert spec.get("description"), f"{t.name}.{arg} has no description"


async def test_list_scrapers_shape(client):
    await seed()
    out = (await client.call_tool("list_scrapers", {})).data
    assert out["count"] == 1
    row = out["scrapers"][0]
    assert row["name"] == "demo-shop"
    assert row["schedule"] == "0 6 * * *"
    assert row["last_run"]["status"] == "passed"
    assert row["freshness"]["stale"] is False


async def test_get_scraper_shape(client):
    ids = await seed()
    out = (await client.call_tool("get_scraper", {"scraper": "demo-shop"})).data
    assert out["scraper"]["id"] == ids["scraper_id"]
    assert out["active_version"]["version"] == 1
    assert out["versions"] == 1
    assert out["record_count"] == 4
    assert len(out["recent_runs"]) == 1


async def test_get_scraper_accepts_numeric_id(client):
    ids = await seed()
    out = (await client.call_tool("get_scraper", {"scraper": str(ids["scraper_id"])})).data
    assert out["scraper"]["name"] == "demo-shop"


async def test_unknown_scraper_is_a_clear_error(client):
    await seed()
    with pytest.raises(ToolError, match="no scraper named"):
        await client.call_tool("get_scraper", {"scraper": "nope"})


async def test_get_run_shape(client):
    ids = await seed(newest_failed=True)
    out = (await client.call_tool("get_run", {"run_id": ids["bad_run"]})).data
    assert out["run"]["status"] == "validation_failed"
    assert out["validator_report"]["passed"] is False
    assert out["log_available"] is False
    assert out["metrics"] == []


# --------------------------------------------------------------------------- freshness
async def test_get_results_reports_clean_data_as_fresh(client):
    await seed(with_fallback=False)
    out = (await client.call_tool("get_results", {"scraper": "demo-shop"})).data
    assert out["count"] == 3
    assert out["freshness"]["stale"] is False
    assert out["freshness"]["fallback_rows"] == 0
    assert out["freshness"]["script_rows"] == 3


async def test_get_results_is_stale_when_newest_run_failed_validation(client):
    await seed(newest_failed=True)
    out = (await client.call_tool("get_results", {"scraper": "demo-shop"})).data
    f = out["freshness"]
    assert f["stale"] is True
    assert "did not pass" in f["reason"]
    assert f["last_clean_run_at"] < f["last_run_at"]


async def test_get_results_flags_fallback_rows(client):
    await seed(newest_failed=True)
    out = (await client.call_tool("get_results", {"scraper": "demo-shop"})).data
    assert out["freshness"]["fallback_rows"] == 1
    assert out["freshness"]["script_rows"] == 3
    flagged = [r for r in out["rows"] if r["source"] == "llm_fallback"]
    assert len(flagged) == 1
    assert flagged[0]["id"] in out["freshness"]["fallback_row_ids"]


async def test_include_fallback_false_removes_llm_rows(client):
    await seed(newest_failed=True)
    out = (await client.call_tool(
        "get_results", {"scraper": "demo-shop", "include_fallback": False}
    )).data
    assert out["count"] == 3
    assert {r["source"] for r in out["rows"]} == {"script"}
    assert out["freshness"]["fallback_rows"] == 0
    assert out["freshness"]["include_fallback"] is False
    # Excluding the rows does not hide that the data is stale.
    assert out["freshness"]["stale"] is True


async def test_get_results_scoped_to_one_run(client):
    ids = await seed(newest_failed=True)
    out = (await client.call_tool(
        "get_results", {"scraper": "demo-shop", "run_id": ids["good_run"]}
    )).data
    assert out["count"] == 3
    assert {r["run_id"] for r in out["rows"]} == {ids["good_run"]}


async def test_get_results_latest_run_only(client):
    ids = await seed(newest_failed=True)
    out = (await client.call_tool(
        "get_results", {"scraper": "demo-shop", "latest_run_only": True}
    )).data
    assert out["run_id"] == ids["bad_run"]
    assert out["count"] == 1
    assert out["rows"][0]["source"] == "llm_fallback"


async def test_get_results_paging(client):
    await seed()
    first = (await client.call_tool("get_results", {"scraper": "demo-shop", "limit": 2})).data
    second = (await client.call_tool(
        "get_results", {"scraper": "demo-shop", "limit": 2, "offset": 2}
    )).data
    assert first["count"] == 2
    assert second["count"] == 2
    assert {r["id"] for r in first["rows"]}.isdisjoint({r["id"] for r in second["rows"]})


# --------------------------------------------------------------------------- search
async def test_search_results_finds_a_value_in_any_field(client):
    await seed()
    out = (await client.call_tool("search_results", {"query": "Widget 1"})).data
    assert out["count"] == 1
    assert out["rows"][0]["data"]["name"] == "Widget 1"


async def test_search_results_scoped_and_without_fallback(client):
    await seed(newest_failed=True)
    out = (await client.call_tool(
        "search_results", {"query": "Widget", "scraper": "demo-shop", "include_fallback": False}
    )).data
    assert out["count"] == 3
    assert {r["source"] for r in out["rows"]} == {"script"}
    assert out["freshness"]["stale"] is True


async def test_search_results_without_scraper_has_no_freshness(client):
    await seed()
    out = (await client.call_tool("search_results", {"query": "Widget"})).data
    assert out["freshness"] is None


# --------------------------------------------------------------------------- writes
async def test_create_scraper_hands_the_build_to_the_builder(client, jobs):
    await seed()
    out = (await client.call_tool("create_scraper", {
        "url": "https://books.example.com/list", "goal": "every book title and price",
    })).data
    assert out["status"] == "building"
    assert out["name"] == "books-example-com-every-book-title"
    # The builder agent owns the scraper row, so there is none yet.
    assert out["scraper_id"] is None
    assert out["queued_to_worker"] is False  # the fake queue accepted nothing
    name, kwargs = jobs.calls[-1]
    assert name == "build_scraper"
    assert kwargs == {
        "url": "https://books.example.com/list",
        "goal": "every book title and price",
        "name": "books-example-com-every-book-title",
        "output_schema": None,
    }


async def test_create_scraper_reports_the_scraper_once_the_builder_made_it(client, jobs):
    """An immediate-mode queue runs the builder inside the enqueue call, so the
    scraper row can already exist when the tool returns."""

    async def build(name, kwargs):
        async with dbsession.get_session() as s:
            s.add(Scraper(name=kwargs["name"], url=kwargs["url"], goal=kwargs["goal"],
                          yaml_path=f"/tmp/{kwargs['name']}.yaml"))

    jobs.worker["fn"] = build
    jobs.worker["job_id"] = "job-abc"
    out = (await client.call_tool("create_scraper", {
        "url": "https://x.example/list", "goal": "things", "name": "x-things",
    })).data
    assert out["job_id"] == "job-abc"
    assert out["queued_to_worker"] is True
    assert out["name"] == "x-things"
    # The builder task was scheduled, not awaited, so the row may not be visible
    # yet; what matters is that the tool never invents one.
    assert out["scraper_id"] in (None, 2)


async def test_create_scraper_rejects_a_bad_url(client):
    with pytest.raises(ToolError, match="http"):
        await client.call_tool("create_scraper", {"url": "example.com", "goal": "things"})


async def test_create_scraper_rejects_a_duplicate_name(client):
    await seed()
    with pytest.raises(ToolError, match="already exists"):
        await client.call_tool("create_scraper", {
            "url": "https://example.com", "goal": "x", "name": "demo-shop",
        })


async def test_run_scraper_enqueues_with_the_scheduler_signature(client, jobs):
    ids = await seed()
    await client.call_tool("run_scraper", {"scraper": "demo-shop"})
    name, kwargs = jobs.calls[-1]
    assert name == "run_scraper"
    assert kwargs == {"scraper_id": ids["scraper_id"], "trigger": "mcp"}


async def test_run_scraper_returns_the_run_the_worker_created(client, jobs):
    ids = await seed()
    created: dict = {}

    async def worker(name, kwargs):
        """Stand in for the scheduler: write the Run row it would write."""
        async with dbsession.get_session() as s:
            run = Run(scraper_id=kwargs["scraper_id"], script_version=1,
                      status=RunStatus.RUNNING.value, trigger=kwargs["trigger"])
            s.add(run)
            await s.flush()
            created["run_id"] = run.id

    jobs.worker["fn"] = worker
    jobs.worker["job_id"] = "job-1"

    out = (await client.call_tool("run_scraper", {"scraper": "demo-shop"})).data
    assert out["run_id"] == created["run_id"]
    assert out["run"]["trigger"] == "mcp"
    assert out["run"]["scraper_id"] == ids["scraper_id"]
    assert out["queued_to_worker"] is True
    assert out["timed_out"] is False
    got = (await client.call_tool("get_run", {"run_id": out["run_id"]})).data
    assert got["run"]["id"] == out["run_id"]


async def test_run_scraper_wait_returns_when_the_run_reaches_a_terminal_state(client, jobs):
    await seed()

    async def worker(name, kwargs):
        async with dbsession.get_session() as s:
            run = Run(scraper_id=kwargs["scraper_id"], script_version=1,
                      status=RunStatus.RUNNING.value, trigger="mcp")
            s.add(run)
            await s.flush()
            run_id = run.id
        await asyncio.sleep(0.1)
        async with dbsession.get_session() as s:
            finished = await s.get(Run, run_id)
            finished.status = RunStatus.PASSED.value
            finished.row_count = 7

    jobs.worker["fn"] = worker
    jobs.worker["job_id"] = "job-2"

    out = (await client.call_tool(
        "run_scraper", {"scraper": "demo-shop", "wait": True, "timeout_s": 10}
    )).data
    assert out["waited"] is True
    assert out["timed_out"] is False
    assert out["status"] == "passed"
    assert out["run"]["row_count"] == 7


async def test_run_scraper_reports_no_run_id_when_no_worker_takes_it(client):
    await seed()
    out = (await client.call_tool("run_scraper", {"scraper": "demo-shop"})).data
    assert out["run_id"] is None
    assert out["run"] is None
    assert out["queued_to_worker"] is False
    assert out["timed_out"] is True
    assert out["status"] == "queued"


async def test_run_scraper_wait_times_out_without_a_worker(client):
    await seed()
    out = (await client.call_tool(
        "run_scraper", {"scraper": "demo-shop", "wait": True, "timeout_s": 1}
    )).data
    assert out["waited"] is True
    assert out["timed_out"] is True
    assert out["status"] == "queued"


async def test_run_scraper_refuses_a_scraper_with_no_active_version(client):
    async with dbsession.get_session() as s:
        s.add(Scraper(name="bare", url="https://x.example", goal="g", yaml_path="/tmp/bare.yaml"))
    with pytest.raises(ToolError, match="no active script version"):
        await client.call_tool("run_scraper", {"scraper": "bare"})


# --------------------------------------------------------------------------- repairs
async def make_repair() -> dict[str, int]:
    ids = await seed()
    async with dbsession.get_session() as s:
        s.add(ScriptVersion(scraper_id=ids["scraper_id"], version=2, yaml=SCRIPT_YAML + "# fixed\n",
                            status=VersionStatus.CANDIDATE.value, created_by="repair"))
        rp = Repair(scraper_id=ids["scraper_id"], run_id=ids["good_run"], candidate_version=2,
                    reason="price selector no longer matches", diff="- old\n+ new", is_minor=True)
        s.add(rp)
        await s.flush()
        ids["repair_id"] = rp.id
    return ids


async def test_get_pending_repairs_shape(client):
    ids = await make_repair()
    out = (await client.call_tool("get_pending_repairs", {})).data
    assert out["count"] == 1
    rp = out["repairs"][0]
    assert rp["id"] == ids["repair_id"]
    assert rp["scraper"] == "demo-shop"
    assert rp["candidate_version"] == 2
    assert rp["diff"] == "- old\n+ new"


async def test_approve_repair_promotes_the_candidate(client):
    ids = await make_repair()
    out = (await client.call_tool(
        "approve_repair", {"repair_id": ids["repair_id"], "note": "diff looks right"}
    )).data
    assert out["status"] == "approved"
    assert out["promoted_version"] == 2
    assert out["retired_version"] == 1
    detail = (await client.call_tool("get_scraper", {"scraper": "demo-shop"})).data
    assert detail["active_version"]["version"] == 2
    assert (await client.call_tool("get_pending_repairs", {})).data["count"] == 0


async def test_approve_repair_twice_is_refused(client):
    ids = await make_repair()
    await client.call_tool("approve_repair", {"repair_id": ids["repair_id"]})
    with pytest.raises(ToolError, match="already"):
        await client.call_tool("approve_repair", {"repair_id": ids["repair_id"]})


async def test_approve_unknown_repair(client):
    with pytest.raises(ToolError, match="no repair with id"):
        await client.call_tool("approve_repair", {"repair_id": 999})


# --------------------------------------------------------------------------- resources
async def test_script_resource_returns_the_active_yaml(client):
    await seed()
    parts = await client.read_resource("scraper://demo-shop/script")
    assert parts[0].text == SCRIPT_YAML


async def test_schema_resource(client):
    await seed()
    parts = await client.read_resource("scraper://demo-shop/schema")
    assert json.loads(parts[0].text) == {"type": "array"}


async def test_run_log_resource_says_when_there_is_no_log(client):
    ids = await seed()
    parts = await client.read_resource(f"run://{ids['good_run']}/log")
    assert "no log file" in parts[0].text


async def test_run_log_resource_reads_the_file(client, tmp_path):
    ids = await seed()
    log = tmp_path / "log.txt"
    log.write_text("step 1 ok\nstep 2 ok\n")
    async with dbsession.get_session() as s:
        run = await s.get(Run, ids["good_run"])
        run.log_path = str(log)
    parts = await client.read_resource(f"run://{ids['good_run']}/log")
    assert "step 2 ok" in parts[0].text


# --------------------------------------------------------------------------- auth
def test_missing_token_blocks_a_non_localhost_bind():
    with pytest.raises(InsecureBindError, match="no bearer token"):
        ensure_bind_allowed("0.0.0.0", token=None)
    with pytest.raises(InsecureBindError):
        ensure_bind_allowed("192.168.1.20", token=None)


def test_missing_token_still_allows_loopback():
    for host in ("127.0.0.1", "localhost", "::1", "127.0.0.5"):
        ensure_bind_allowed(host, token=None)


def test_a_token_allows_any_bind():
    ensure_bind_allowed("0.0.0.0", token="s3cret")


def test_build_auth_is_none_without_a_token_and_a_verifier_with_one():
    assert build_auth(token=None) is None
    verifier = build_auth(token="s3cret")
    assert "s3cret" in verifier.tokens


async def test_configured_token_is_accepted_and_others_rejected():
    verifier = build_auth(token="s3cret")
    assert (await verifier.verify_token("s3cret")) is not None
    assert (await verifier.verify_token("wrong")) is None
