"""Tests for the data hook execution path."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import apply
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA, read_applied_revisions
from hearth.kernel.migrations.planner import compute_plan
from hearth.kernel.persistence import OUTBOX_TABLE
from hearth.kernel.registry import Registry
from hearth.primitives.actor import System


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/data.db")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
        # Create the outbox table so suppression tests can assert against it.
        # Use `tables=` to avoid pulling in any other plugin tables that may
        # have registered against the shared METADATA via test imports.
        await conn.run_sync(
            lambda s: OUTBOX_TABLE.create(s, checkfirst=True)
        )
    yield eng
    await eng.dispose()


async def test_data_upgrade_is_called_after_ddl_commits(
    engine, tmp_plugin_with_migrations,
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
                OpSummary(kind="CreateTable", desc="CREATE TABLE seed_test", destructive=False),
                OpSummary(kind="DataMigration", desc="seed a row", destructive=False),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade():
                op.create_table(
                    "seed_test",
                    sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("name", sa.String, nullable=False),
                )

            def downgrade(): pass

            async def data_upgrade(uow):
                await uow.execute(sa.text("INSERT INTO seed_test (id, name) VALUES (1, 'seeded')"))
        """,
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    async with engine.connect() as conn:
        row = (await conn.execute(sa.text("SELECT name FROM seed_test WHERE id = 1"))).scalar()
    assert row == "seeded"

    # Audit row recorded
    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert len(rows) == 1


async def test_audit_row_rolled_back_when_data_upgrade_raises(
    engine, tmp_plugin_with_migrations,
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
                OpSummary(kind="DataMigration", desc="seeding boom", destructive=False),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade():
                op.create_table("t", sa.Column("id", sa.Integer, primary_key=True))

            def downgrade(): pass

            async def data_upgrade(uow):
                raise RuntimeError("seed failed")
        """,
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)

    with pytest.raises(RuntimeError, match="seed failed"):
        await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    # Audit row should NOT exist (rolled back together with the failed data UoW)
    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert rows == []

    # But the DDL table DID get created (Alembic's transaction already committed)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda s: set(sa.inspect(s).get_table_names()))
    assert "t" in names  # The sharp edge the spec documents


async def test_data_upgrade_does_not_emit_outbox_events(
    engine, tmp_plugin_with_migrations,
) -> None:
    """Migrations are not user activity; the data UoW suppresses emit."""
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
                OpSummary(kind="DataMigration", desc="emit attempt", destructive=False),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade():
                op.create_table("t", sa.Column("id", sa.Integer, primary_key=True))

            def downgrade(): pass

            async def data_upgrade(uow):
                # Attempt to emit -- the suppressed UoW should swallow it.
                class _FakeEvent:
                    pass
                uow.emit(_FakeEvent())
        """,
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    # Outbox table is `_hearth_outbox` (created by the engine fixture so this
    # assertion actually runs); the migration UoW suppresses emit, so it
    # must have zero rows after apply.
    async with engine.connect() as conn:
        outbox_exists = await conn.run_sync(
            lambda s: "_hearth_outbox" in sa.inspect(s).get_table_names()
        )
        assert outbox_exists, "outbox table missing — fixture regression"
        count = (
            await conn.execute(sa.text("SELECT COUNT(*) FROM _hearth_outbox"))
        ).scalar()
        assert count == 0


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
