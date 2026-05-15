"""Postgres-backed test harness — see docs/core/testing/harness.md."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine

from hearth.testing._base import _SqlAlchemyHarness  # pyright: ignore[reportPrivateUsage]


class PostgresHarness(_SqlAlchemyHarness):
    """Test harness backed by a real Postgres instance via DATABASE_URL."""

    def __init__(self, database_url: str) -> None:
        super().__init__(create_async_engine(_to_async_url(database_url)))


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url
