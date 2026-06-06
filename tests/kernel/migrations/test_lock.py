"""Tests for the single-writer advisory lock."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from hearth.kernel.migrations._lock import acquire_migration_lock
from hearth.kernel.migrations.errors import MigrationLockContention


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[object]:
    # NullPool (not the shared StaticPool used by `make_async_engine`) so each
    # `engine.connect()` returns a distinct DBAPI connection. The kernel's
    # StaticPool would otherwise hand both lock holders the same underlying
    # connection, making BEGIN EXCLUSIVE a no-op rather than contended.
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/lock.db",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    yield eng
    await eng.dispose()


async def test_lock_acquired_when_uncontended(engine) -> None:
    async with acquire_migration_lock(engine, timeout=1.0):
        pass  # held and released


async def test_lock_contention_raises(engine) -> None:
    holding = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with acquire_migration_lock(engine, timeout=1.0):
            holding.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await holding.wait()
    with pytest.raises(MigrationLockContention):
        async with acquire_migration_lock(engine, timeout=0.5):
            pass
    release.set()
    await task


async def test_lock_recovers_from_stale_row(engine, monkeypatch) -> None:
    """Simulate a prior crash by pre-inserting an old sentinel row.
    Acquire should detect it as stale, delete it, and succeed.
    """
    # Tight stale threshold so the test pre-insert qualifies.
    monkeypatch.setenv("HEARTH_DB_MIGRATE_LOCK_STALE_SECONDS", "1")

    # Create the table + insert an "ancient" row mimicking a crashed holder.
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS _hearth_migration_lock ("
                "id INTEGER PRIMARY KEY, "
                "created_at TIMESTAMP NOT NULL"
                ")"
            )
        )
        old = datetime.now(UTC) - timedelta(hours=1)
        await conn.execute(
            sa.text(
                "INSERT INTO _hearth_migration_lock (id, created_at) VALUES (1, :ts)"
            ).bindparams(ts=old)
        )

    # Despite the pre-existing row, acquisition should succeed because the
    # row is older than the (1s) stale threshold.
    async with acquire_migration_lock(engine, timeout=1.0):
        pass

    # And the lock row should be cleared on normal release.
    async with engine.begin() as conn:
        remaining = await conn.scalar(
            sa.text("SELECT COUNT(*) FROM _hearth_migration_lock")
        )
        assert remaining == 0


async def test_lock_does_not_evict_fresh_row(engine, monkeypatch) -> None:
    """A recent sentinel row (within the stale threshold) must still block
    new acquirers — self-healing only fires for genuinely stale rows."""
    monkeypatch.setenv("HEARTH_DB_MIGRATE_LOCK_STALE_SECONDS", "3600")

    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS _hearth_migration_lock ("
                "id INTEGER PRIMARY KEY, "
                "created_at TIMESTAMP NOT NULL"
                ")"
            )
        )
        await conn.execute(
            sa.text(
                "INSERT INTO _hearth_migration_lock (id, created_at) VALUES (1, :ts)"
            ).bindparams(ts=datetime.now(UTC))
        )

    with pytest.raises(MigrationLockContention):
        async with acquire_migration_lock(engine, timeout=0.3):
            pass
