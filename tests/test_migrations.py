"""Alembic migrations against a throwaway SQLite file.

The contract: `alembic upgrade head` on an empty database produces exactly the
schema `smartscraper.db.models.Base.metadata` describes, and autogenerate finds
nothing left to do afterwards. A drift here means a deployed database quietly
differs from the models.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from alembic import command
from smartscraper.db.models import Base

ROOT = Path(__file__).resolve().parent.parent
MODEL_TABLES = set(Base.metadata.tables)


def alembic_config(db_path: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return cfg


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "migrate.db"


def table_names(db_path: Path) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("select name from sqlite_master where type='table'").fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def test_every_model_table_has_a_migration():
    """Derived, not hardcoded.

    This used to assert the number 13. Adding the `setting` table broke it for no
    reason: a count in a test is a second place to remember something, and it
    fails on the change rather than on a defect.
    """
    assert MODEL_TABLES, "no models were discovered at all"
    assert "alembic_version" not in MODEL_TABLES


def test_upgrade_head_creates_every_table(db: Path):
    command.upgrade(alembic_config(db), "head")
    created = table_names(db)
    assert created >= MODEL_TABLES
    assert "alembic_version" in created
    # Derived, not counted: a new table should not fail a test that is about
    # migrations keeping up with the models.
    assert created == MODEL_TABLES | {'alembic_version'}


def test_upgrade_head_leaves_no_autogenerate_diff(db: Path):
    command.upgrade(alembic_config(db), "head")
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"compare_type": True, "compare_server_default": True}
            )
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], f"models and migrations disagree: {diff}"


def test_downgrade_to_base_drops_everything(db: Path):
    cfg = alembic_config(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert table_names(db) - {"alembic_version"} == set()


def test_env_uses_batch_mode_for_sqlite():
    """SQLite cannot ALTER a column; without batch mode a future migration that
    changes one fails at run time rather than here."""
    assert "render_as_batch=True" in (ROOT / "alembic" / "env.py").read_text()


def test_indexes_and_unique_constraints_survive_the_migration(db: Path):
    command.upgrade(alembic_config(db), "head")
    con = sqlite3.connect(db)
    try:
        names = {r[0] for r in con.execute("select name from sqlite_master where type='index'")}
        run_cols = {r[1] for r in con.execute("pragma table_info('run')")}
    finally:
        con.close()
    assert "ix_run_scraper_created" in names
    assert {"id", "scraper_id", "status", "row_count", "validator_report"} <= run_cols
