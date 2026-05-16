"""Postgres-backed test harness — see docs/core/testing/harness.md."""

from __future__ import annotations

from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.testing.base import BaseHarness


class PostgresHarness(BaseHarness):
    """Test harness backed by a real Postgres instance via DATABASE_URL."""

    def __init__(self, database_url: str) -> None:
        super().__init__(make_async_engine(database_url))
