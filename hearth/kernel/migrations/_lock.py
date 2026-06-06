"""Cross-process advisory lock so only one migration runs at a time."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations.errors import MigrationLockContention

# A fixed 64-bit integer the kernel uses for the Postgres advisory lock.
# Derived from hash("hearth-migrations") truncated to 63 bits.
_PG_LOCK_KEY = 0x4845415254484D49  # "HEARTHMI" hex

# Default age past which a SQLite sentinel-lock row is presumed stale (the
# holder crashed between INSERT and DELETE). Override with
# `HEARTH_DB_MIGRATE_LOCK_STALE_SECONDS`. Five minutes comfortably exceeds
# the longest expected migration without blocking forever on a crash.
_DEFAULT_LOCK_STALE_SECONDS = 300


@contextlib.asynccontextmanager
async def acquire_migration_lock(
    engine: AsyncEngine, *, timeout: float = 5.0
) -> AsyncGenerator[None]:
    """Hold the migration lock for the duration of the context.

    PostgreSQL: pg_try_advisory_lock polled with the given timeout.
    SQLite: BEGIN EXCLUSIVE on a sentinel table.
    Raises MigrationLockContention if not acquired in time.
    """
    dialect = engine.dialect.name
    if dialect == "postgresql":
        async for _ in _acquire_pg(engine, timeout):
            yield
            return
    elif dialect == "sqlite":
        async for _ in _acquire_sqlite(engine, timeout):
            yield
            return
    else:
        raise NotImplementedError(f"Migration lock not implemented for dialect '{dialect}'")


async def _acquire_pg(engine: AsyncEngine, timeout: float) -> AsyncGenerator[None]:
    conn = await engine.connect()
    deadline = time.monotonic() + timeout
    try:
        while True:
            got = await conn.scalar(
                sa.text("SELECT pg_try_advisory_lock(:key)").bindparams(key=_PG_LOCK_KEY)
            )
            if got:
                try:
                    yield
                finally:
                    await conn.execute(
                        sa.text("SELECT pg_advisory_unlock(:key)").bindparams(key=_PG_LOCK_KEY)
                    )
                return
            if time.monotonic() >= deadline:
                raise MigrationLockContention(
                    "another migration is in progress; retry once it completes"
                )
            await asyncio.sleep(0.1)
    finally:
        await conn.close()


async def _acquire_sqlite(engine: AsyncEngine, timeout: float) -> AsyncGenerator[None]:
    """Sentinel-row lock for SQLite, with stale-row self-healing.

    Earlier versions used `BEGIN EXCLUSIVE` on a dedicated connection, but
    SQLite's EXCLUSIVE is a file-level write lock — it blocks every other
    connection (including the separate sync connection Alembic opens
    inside `command.upgrade`) for the entire duration of the migration.
    Apply-time DDL therefore deadlocked.

    The sentinel-row pattern preserves cross-process single-writer
    semantics without taking a file lock: acquirer INSERTs a row with a
    fixed primary key in its own committed transaction; contention shows
    up as a UNIQUE-violation, polled until timeout. Release deletes the
    row. The migration body runs between acquire and release with no
    write transaction held on the lock connection — Alembic and the audit
    write are free to use the same file from any other connection.

    Self-healing: each acquire stamps `created_at`. If a contended row is
    older than the stale threshold (default 5 min, overridable via
    `HEARTH_DB_MIGRATE_LOCK_STALE_SECONDS`), the prior holder is presumed
    crashed and the row is deleted before the next INSERT attempt. Without
    this, a crash between INSERT and DELETE would wedge the database
    permanently.
    """
    conn = await engine.connect()
    try:
        try:
            await conn.execute(
                sa.text(
                    "CREATE TABLE IF NOT EXISTS _hearth_migration_lock ("
                    "id INTEGER PRIMARY KEY, "
                    "created_at TIMESTAMP NOT NULL"
                    ")"
                )
            )
            # Backfill column on pre-existing tables created before stale
            # self-healing was added. SQLite's `CREATE TABLE IF NOT EXISTS`
            # does not add columns; we ADD COLUMN unconditionally and
            # swallow the "duplicate column" error when the table already
            # has it.
            with contextlib.suppress(OperationalError):
                await conn.execute(
                    sa.text(
                        "ALTER TABLE _hearth_migration_lock "
                        "ADD COLUMN created_at TIMESTAMP"
                    )
                )
            await conn.commit()
        except OperationalError as err:
            raise MigrationLockContention(
                "another migration is in progress; retry once it completes"
            ) from err

        stale_seconds = float(
            os.environ.get(
                "HEARTH_DB_MIGRATE_LOCK_STALE_SECONDS", _DEFAULT_LOCK_STALE_SECONDS
            )
        )

        deadline = time.monotonic() + timeout
        while True:
            try:
                await conn.execute(
                    sa.text(
                        "INSERT INTO _hearth_migration_lock (id, created_at) "
                        "VALUES (1, :now)"
                    ).bindparams(now=datetime.now(UTC))
                )
                await conn.commit()
                break
            except IntegrityError:
                await conn.rollback()
                # Existing row blocks us — check if it's stale and clear it.
                if await _evict_stale_lock_row(engine, stale_seconds):
                    continue  # row was stale and just deleted; retry INSERT immediately
                if time.monotonic() >= deadline:
                    raise MigrationLockContention(
                        "another migration is in progress; retry once it completes"
                    ) from None
                await asyncio.sleep(0.05)
            except OperationalError as err:
                await conn.rollback()
                if time.monotonic() >= deadline:
                    raise MigrationLockContention(
                        "another migration is in progress; retry once it completes"
                    ) from err
                await asyncio.sleep(0.05)

        try:
            yield
        finally:
            try:
                await conn.execute(
                    sa.text("DELETE FROM _hearth_migration_lock WHERE id = 1")
                )
                await conn.commit()
            except Exception:
                # Best-effort release; surface the original error if any.
                await conn.rollback()
    finally:
        await conn.close()


async def _evict_stale_lock_row(engine: AsyncEngine, stale_seconds: float) -> bool:
    """Delete the sentinel row if its `created_at` is older than
    `stale_seconds`. Returns True if the row was actually evicted (caller
    should retry INSERT), False otherwise. Pre-stale-self-healing rows
    (no `created_at`) are treated as stale — they predate this code path
    and can only originate from a prior crash, since today's holder
    always stamps the column.

    Runs in its own short-lived transaction via `engine.begin()` so the
    caller's connection state is untouched. The `rowcount > 0` guard
    prevents racing evictors from both reporting success when only one
    actually deleted the row — without it, a losing racer would falsely
    signal "row gone, retry INSERT" and bounce off whatever fresh holder
    just won.
    """
    async with engine.begin() as conn:
        row: object = await conn.scalar(
            sa.text("SELECT created_at FROM _hearth_migration_lock WHERE id = 1")
        )
        if row is None:
            # No row at all — caller will INSERT cleanly on the next try.
            return False
        created_at: datetime | None
        if isinstance(row, datetime):
            created_at = row
        elif isinstance(row, str):
            # SQLite stores TIMESTAMP as text; aiosqlite returns it as `str`
            # when the column lacks an explicit `TIMESTAMP` declared affinity
            # (the ALTER TABLE backfill path).
            try:
                created_at = datetime.fromisoformat(row)
            except ValueError:
                created_at = None
        else:
            created_at = None

        if created_at is not None:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            age = datetime.now(UTC) - created_at
            if age < timedelta(seconds=stale_seconds):
                return False
        # Either no usable timestamp (predates self-healing) or aged out —
        # both treated as stale. Only report success if our DELETE actually
        # removed the row (another evictor may have beaten us to it).
        result = await conn.execute(
            sa.text("DELETE FROM _hearth_migration_lock WHERE id = 1")
        )
        return result.rowcount > 0
