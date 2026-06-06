"""Tests for the apply loop (DDL-only path; data hooks added in Phase E)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import apply
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA, read_applied_revisions
from hearth.kernel.migrations.planner import compute_plan
from hearth.kernel.registry import Registry
from hearth.primitives.actor import System


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/apply.db")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


async def test_apply_safe_plan_creates_table_and_writes_audit_row(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="widgets"
        ),
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)

    report = await apply(
        plan, config=cfg, engine=engine, actor=System(), registry=registry
    )
    assert report.applied_revisions == [(builder.plugin, "0001")]
    assert report.aborted_at is None

    # Table was created
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda s: set(sa.inspect(s).get_table_names()))
    assert "widgets" in names

    # Audit row written
    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert len(rows) == 1
    assert rows[0].destructive_ops_acked == []
    assert rows[0].applied_by == "system"


async def test_apply_destructive_op_without_confirm_aborts(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    builder = tmp_plugin_with_migrations
    # 0001 creates the table (safe), 0002 drops a column (destructive).
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="t"
        ),
    )
    builder.add_revision(
        revision="0002",
        down_revision="0001",
        body="""
            from alembic import op
            from hearth.migrations import OpSummary

            revision = "0002"
            down_revision = "0001"
            depends_on = None

            HEARTH_UPGRADE_OPS = [
                OpSummary(kind="DropColumn", desc="DROP COLUMN t.name", destructive=True),
            ]
            HEARTH_DOWNGRADE_OPS = []


            def upgrade(): op.drop_column("t", "name")
            def downgrade(): pass
        """,
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)

    # confirm=None means any destructive op aborts immediately
    report = await apply(
        plan,
        config=cfg,
        engine=engine,
        actor=System(),
        registry=registry,
        confirm=None,
    )
    # 0001 applied (safe), 0002 aborted (destructive without confirm)
    assert report.applied_revisions == [(builder.plugin, "0001")]
    assert report.aborted_at == (builder.plugin, "0002")

    # 0001's audit row exists; 0002's does not.
    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert [r.revision_id for r in rows] == ["0001"]


async def test_apply_calls_confirm_for_destructive(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="t"
        ),
    )
    builder.add_revision(
        revision="0002",
        down_revision="0001",
        body="""
            from alembic import op
            from hearth.migrations import OpSummary

            revision = "0002"
            down_revision = "0001"
            HEARTH_UPGRADE_OPS = [
                OpSummary(kind="DropColumn", desc="DROP COLUMN t.name", destructive=True),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade(): op.drop_column("t", "name")
            def downgrade(): pass
        """,
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)

    confirmed: list[str] = []

    async def yes(_revision, op):
        confirmed.append(op.desc)
        return True

    report = await apply(
        plan,
        config=cfg,
        engine=engine,
        actor=System(),
        registry=registry,
        confirm=yes,
    )
    assert confirmed == ["DROP COLUMN t.name"]
    assert report.applied_revisions == [(builder.plugin, "0001"), (builder.plugin, "0002")]
    assert report.aborted_at is None


def _fake_plugin_info(alias: str, install_path):
    # Minimal stand-in for tests; real PluginInfo comes from Registry.build().
    from hearth.kernel.registry import PluginInfo
    return PluginInfo(
        alias=alias,
        package=alias,
        version="0.0.1",
        module=alias,
        install_path=str(install_path),
        depends_on=[],
        extends=[],
        entities=[],
        actions=[],
        events=[],
    )
