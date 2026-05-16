"""Dialect-aware async engine factory — shared by the CLI, `InMemoryHarness`,
and `PostgresHarness`. Centralises the SQLite-specific quirks (StaticPool, FK
pragma) so they don't drift across consumers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite+"):
        return url
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def make_async_engine(url: str) -> AsyncEngine:
    """Build an AsyncEngine. For SQLite: StaticPool + check_same_thread=False so
    multiple async sessions share one underlying connection (required for :memory:);
    plus a connect listener that enables `PRAGMA foreign_keys=ON` (off by default
    in SQLite)."""
    async_url = _to_async_url(url)
    kwargs: dict[str, Any] = {}
    if async_url.startswith("sqlite+"):
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_async_engine(async_url, **kwargs)
    if async_url.startswith("sqlite+"):

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_fk(dbapi_conn: Any, _: Any) -> None:  # pyright: ignore[reportUnusedFunction]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine
