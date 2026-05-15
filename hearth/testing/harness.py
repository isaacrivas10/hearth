"""In-memory test harness — see docs/core/testing/harness.md.

Backed by SQLite-in-memory so plugin authors get the full SA-backed UnitOfWork
(including `query()`) without running Postgres.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from hearth.testing._base import _SqlAlchemyHarness  # pyright: ignore[reportPrivateUsage]


class Harness(_SqlAlchemyHarness):
    """In-memory plugin test harness, backed by SQLite-in-memory."""

    def __init__(self) -> None:
        # StaticPool is REQUIRED: a `:memory:` database is per-connection, so
        # without a single shared connection every checkout sees an empty DB.
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_fk(dbapi_conn: Any, _: Any) -> None:  # pyright: ignore[reportUnusedFunction]
            # SQLite ships with FK enforcement OFF; turn it on per connection
            # so ON DELETE RESTRICT and stale-FK rejection behave like Postgres.
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        super().__init__(engine)
