"""Tests for the planner."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA
from hearth.kernel.migrations.planner import compute_plan
from hearth.kernel.registry import Registry


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/db.sqlite")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


async def test_empty_plan_when_no_plugins(engine, tmp_plugin_with_migrations) -> None:
    # No revisions added; planner returns empty plan.
    registry = Registry.build()
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    assert plan.revisions == []
    assert plan.has_destructive is False


async def test_plan_lists_pending_revisions(
    engine, tmp_plugin_with_migrations, example_revision_body
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="users"
        ),
    )
    # Patch registry to include our tmp plugin.
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(builder.plugin, builder.package_dir.parent)
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    assert len(plan.revisions) == 1
    assert plan.revisions[0].plugin == builder.plugin
    assert plan.revisions[0].revision_id == "0001"
    assert plan.revisions[0].ops[0].kind == "CreateTable"


async def test_plan_skips_revisions_already_applied(
    engine, tmp_plugin_with_migrations, example_revision_body
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="users"
        ),
    )
    async with engine.begin() as conn:
        await conn.execute(
            sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        )
        await conn.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES ('0001')"))
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(builder.plugin, builder.package_dir.parent)
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    assert plan.revisions == []  # nothing pending


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
