"""Routers, in nav order. app.create_app() includes each one."""

from __future__ import annotations

from smartscraper.web.routes import infra, overview, records, repairs, runs, scrapers

ROUTERS = [
    overview.router,
    scrapers.router,
    runs.router,
    records.router,
    repairs.router,
    infra.router,
]

__all__ = ["ROUTERS"]
