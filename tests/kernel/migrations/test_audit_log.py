"""Tests for the schema-log audit overlay table."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.audit_log import (
    SCHEMA_LOG_METADATA,
    SchemaLogRow,
    read_applied_revisions,
    record_applied_revision,
)


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


async def test_schema_log_table_is_created(engine) -> None:
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda s: set(inspect(s).get_table_names()))
    assert "_hearth_schema_log" in names


async def test_record_and_read_revision(engine) -> None:
    await record_applied_revision(
        engine,
        plugin="auth",
        revision_id="0001",
        description="initial schema",
        applied_by="system:adopt",
        destructive_ops_acked=[],
    )
    rows = await read_applied_revisions(engine, plugin="auth")
    assert len(rows) == 1
    assert rows[0].revision_id == "0001"
    assert rows[0].destructive_ops_acked == []
    assert isinstance(rows[0], SchemaLogRow)


async def test_record_preserves_destructive_acknowledgments(engine) -> None:
    await record_applied_revision(
        engine,
        plugin="auth",
        revision_id="0007",
        description="drop legacy email",
        applied_by="user:42",
        destructive_ops_acked=["DROP COLUMN user.email_legacy"],
    )
    rows = await read_applied_revisions(engine, plugin="auth")
    assert rows[0].destructive_ops_acked == ["DROP COLUMN user.email_legacy"]


async def test_read_returns_rows_for_plugin_only(engine) -> None:
    await record_applied_revision(
        engine,
        plugin="auth",
        revision_id="0001",
        description="x",
        applied_by="s",
        destructive_ops_acked=[],
    )
    await record_applied_revision(
        engine,
        plugin="commerce",
        revision_id="0001",
        description="y",
        applied_by="s",
        destructive_ops_acked=[],
    )
    rows = await read_applied_revisions(engine, plugin="auth")
    assert {r.plugin for r in rows} == {"auth"}
