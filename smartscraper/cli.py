"""Console entry point."""

from __future__ import annotations

import argparse
import sys

from smartscraper.config import ROOT, get_settings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="smartscraper")
    sub = p.add_subparsers(dest="cmd")

    srv = sub.add_parser("serve", help="run the web UI, API and MCP server together")
    srv.add_argument("--host", default=None)
    srv.add_argument("--port", type=int, default=None)
    srv.add_argument("--reload", action="store_true")

    sub.add_parser("worker", help="run the scheduler and job worker")

    r = sub.add_parser("run", help="execute one scraper now, in the foreground")
    r.add_argument("scraper")
    r.add_argument("--version", type=int, default=None)

    imp = sub.add_parser("import", help="register a YAML script as a scraper")
    imp.add_argument("path")
    imp.add_argument("--name", default=None, help="defaults to a slug of the target URL")
    imp.add_argument("--goal", default="", help="what it is for, shown in the UI")

    m = sub.add_parser("mcp", help="MCP server on its own")
    m.add_argument("--stdio", action="store_true")

    sub.add_parser("init-db", help="bring the schema to head")

    a = p.parse_args(argv)
    if a.cmd is None:
        p.print_help()
        return 1
    s = get_settings()

    if a.cmd == "init-db":
        # Migrations own the schema. db.session.create_all() is for throwaway test
        # databases only; a database built that way needs `alembic stamp head` once.
        import subprocess

        rc = subprocess.run(["alembic", "upgrade", "head"], cwd=str(ROOT)).returncode
        if rc == 0:
            print("schema at head")
        return rc

    if a.cmd == "serve":
        import os

        import uvicorn

        from smartscraper.mcp.server import InsecureBindError, ensure_bind_allowed

        # The MCP mount checks `settings.host`, but the socket is bound to whatever
        # is passed here. `--host 0.0.0.0` with SS_HOST left at loopback would sail
        # through the guard and then listen publicly, so settle the effective host
        # first, publish it so every later read agrees, and check THAT.
        host = a.host or s.host
        port = a.port or s.port
        os.environ["SS_HOST"] = host
        os.environ["SS_PORT"] = str(port)
        get_settings.cache_clear()
        s = get_settings()

        from smartscraper.web.auth import InsecureWebBindError, raise_for_insecure_bind

        try:
            raise_for_insecure_bind()                       # the admin UI
            ensure_bind_allowed(s.host, token=s.mcp_bearer_token)   # the MCP endpoint
        except (InsecureWebBindError, InsecureBindError) as exc:
            print(f"refusing to start: {exc}", file=sys.stderr)
            return 4

        uvicorn.run("smartscraper.web.app:app", host=s.host, port=s.port, reload=a.reload)
        return 0

    if a.cmd == "worker":
        from smartscraper.scheduler.worker import main as worker_main

        return worker_main([])

    if a.cmd == "import":
        import asyncio

        return asyncio.run(_import_one(a.path, a.name, a.goal))

    if a.cmd == "run":
        import asyncio

        return asyncio.run(_run_one(a.scraper, a.version))

    if a.cmd == "mcp":
        from smartscraper.mcp.server import run_http, run_stdio

        if a.stdio:
            run_stdio()
        else:
            run_http(s.host, s.port)
        return 0

    print(f"'{a.cmd}' is not wired up yet", file=sys.stderr)
    return 2


async def _import_one(path: str, name: str | None, goal: str) -> int:
    """Register a script file so `run` can find it."""
    from pathlib import Path

    from smartscraper.db.session import get_session
    from smartscraper.importer import ImportRefused, import_script

    source = Path(path)
    if not source.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    try:
        async with get_session() as s:
            # The file's own name is what a person means. Falling back to a slug
            # of the URL produced names like
            # `https-books-toscrape-com-catalogue-category-books-mystery-3-index-html`.
            result = await import_script(
                s, source.read_text(encoding="utf-8"), name=name or source.stem, goal=goal
            )
    except ImportRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        from sqlalchemy.exc import OperationalError

        if isinstance(exc, OperationalError):
            print("the database has no schema yet; run `smartscraper init-db`", file=sys.stderr)
            return 4
        raise

    print(f"imported {result.name} (v{result.version}) -> {result.path}")
    if result.has_custom_python:
        print("  warning: it contains a custom_python step, which runs unsandboxed",
              file=sys.stderr)
    print(f"  run it with: smartscraper run {result.name}")
    return 0


async def _run_one(name: str, version: int | None) -> int:
    """Run one scraper in the foreground and print its verdict."""
    from sqlalchemy.exc import OperationalError

    from smartscraper import repo
    from smartscraper.db.session import get_session
    from smartscraper.scheduler.tasks import run_scraper_now

    try:
        async with get_session() as s:
            sc = await repo.get_scraper(s, name)
    except OperationalError:
        # An uninitialised database is a normal first-run state, not a crash.
        print("the database has no schema yet; run `smartscraper init-db`", file=sys.stderr)
        return 4

    if sc is None:
        print(f"no scraper named {name!r}", file=sys.stderr)
        return 2
    scraper_id = sc.id

    await run_scraper_now(scraper_id, trigger="cli", version=version)

    async with get_session() as s:
        runs = await repo.list_runs(s, scraper_id=scraper_id, limit=1)
        if not runs:
            print("no run was recorded", file=sys.stderr)
            return 3
        run = runs[0]
        report = run.validator_report or {}
        print(f"run #{run.id}  {run.status}  {run.row_count} rows  {run.engine_used or '-'}")
        if report.get("summary"):
            print(f"  {report['summary']}")
        for rule in report.get("rules", []):
            if not rule.get("passed"):
                print(f"  FAIL {rule['rule']}: measured {rule['measured']}, expected {rule['expected']}")
        return 0 if run.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
