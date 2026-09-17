"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from smartscraper.config import get_settings

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None, echo: bool = False) -> AsyncEngine:
    global _engine, _factory
    s = get_settings()
    s.ensure_dirs()
    _engine = create_async_engine(url or s.db_url, echo=echo, future=True)
    _factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    if _factory is None:
        init_engine()
    assert _factory is not None
    async with _factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    from smartscraper.db.models import Base

    eng = _engine or init_engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
