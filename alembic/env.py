"""Alembic environment for SmartScraper.

The URL comes from `smartscraper.config.Settings.db_url`, so there is one place
that decides where the database lives. Two overrides exist, for tests and for
one-off work against another file:

    alembic -x db_url=sqlite+aiosqlite:////tmp/x.db upgrade head
    SS_DB_PATH=/tmp/x.db alembic upgrade head

`render_as_batch=True` is not optional here: SQLite cannot ALTER a column, so
Alembic has to rebuild the table instead.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from smartscraper.config import get_settings
from smartscraper.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    if override:
        return override
    configured = config.get_main_option("sqlalchemy.url", None)
    if configured:
        return configured
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings.db_url


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite: rebuild the table instead of ALTER
        compare_type=True,
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = _url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_async_migrations())
        return
    # Called from inside a running loop (app startup, a test, a worker task):
    # asyncio.run() would refuse, so give the migration its own loop in a thread.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(asyncio.run, run_async_migrations()).result()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
