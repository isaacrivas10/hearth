"""Tests for partial-commit recovery."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import apply
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA, read_applied_revisions
from hearth.kernel.migrations.planner import compute_plan
from hearth.kernel.migrations.recovery import find_orphaned_revisions
from hearth.kernel.registry import Registry
from hearth.primitives.actor import System


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/recov.db")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


async def test_no_orphans_when_alembic_and_log_in_sync(
    engine, tmp_plugin_with_migrations, example_revision_body
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001",
            down_revision=None,
            branch=builder.plugin,
            table="t",
        ),
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin,
        builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    orphans = await find_orphaned_revisions(engine, cfg, registry)
    assert orphans == []


async def test_orphan_when_alembic_advanced_but_audit_missing(
    engine,
    tmp_plugin_with_migrations,
    example_revision_body,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001",
            down_revision=None,
            branch=builder.plugin,
            table="t",
        ),
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin,
        builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)
    # Simulate audit-row loss
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM _hearth_schema_log"))

    orphans = await find_orphaned_revisions(engine, cfg, registry)
    assert [(o.plugin, o.revision_id) for o in orphans] == [(builder.plugin, "0001")]


async def test_recovery_re_runs_data_upgrade_for_orphans(
    engine,
    tmp_plugin_with_migrations,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=f"""
            from alembic import op
            import sqlalchemy as sa
            from hearth.migrations import OpSummary

            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)

            HEARTH_UPGRADE_OPS = [
                OpSummary(kind="CreateTable", desc="CREATE TABLE t", destructive=False),
                OpSummary(kind="DataMigration", desc="seed t", destructive=False),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade():
                op.create_table("t",
                    sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("seed", sa.Integer),
                )

            def downgrade(): pass

            async def data_upgrade(uow):
                # Idempotent: upsert id=1
                existing = await uow.execute(sa.text("SELECT 1 FROM t WHERE id=1"))
                if existing.first() is None:
                    await uow.execute(sa.text("INSERT INTO t (id, seed) VALUES (1, 42)"))
        """,
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin,
        builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    # Simulate audit row loss (e.g. process crashed before audit commit)
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM _hearth_schema_log"))
        await conn.execute(sa.text("DELETE FROM t WHERE id=1"))  # also clear data

    # Re-run apply — recovery should detect orphan and re-execute data_upgrade.
    # Recompute the plan first (mirrors CLI flow): with Alembic still at 0001
    # and the audit row missing, the fresh plan is empty and only the recovery
    # block runs. Reusing the stale plan would double-apply 0001 in the main
    # loop after recovery wrote the audit row.
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert len(rows) == 1
    async with engine.connect() as conn:
        seed = (await conn.execute(sa.text("SELECT seed FROM t WHERE id=1"))).scalar()
    assert seed == 42  # data_upgrade ran again


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
