"""Tests for Phase H3 plugin-downgrade detection.

The detector fires when `alembic_version` references a revision that the
installed plugin package no longer ships — the kernel refuses to keep
going because the only safe path is to reinstall the package version
that introduced the revision and downgrade explicitly.
"""

from __future__ import annotations

import pytest

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import apply
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA
from hearth.kernel.migrations.errors import PluginDowngradeDetected
from hearth.kernel.migrations.planner import MigrationPlan, compute_plan
from hearth.kernel.registry import Registry
from hearth.primitives.actor import System


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/downgrade.db")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


async def test_apply_raises_when_alembic_version_points_at_missing_revision(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    """Applied revision file deleted from the package → apply() must raise.

    Models the production scenario: operator deploys an older package
    version against a database that was migrated by a newer one. The
    revision id in `alembic_version` no longer exists in the package's
    `migrations/` directory.
    """
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="t",
        ),
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    # Simulate package downgrade: the revision file vanishes but the
    # database still records it in `alembic_version` (and `_hearth_schema_log`).
    revision_file = builder.package_dir / "migrations" / "0001_test.py"
    revision_file.unlink()
    # Drop cached bytecode so Alembic does not pick up the stale module.
    pycache = builder.package_dir / "migrations" / "__pycache__"
    if pycache.exists():
        for f in pycache.iterdir():
            f.unlink()
        pycache.rmdir()

    # Rebuild config so `ScriptDirectory` reads the now-missing file fresh.
    cfg = build_config(registry, str(engine.url))
    empty_plan = MigrationPlan(revisions=[])
    with pytest.raises(PluginDowngradeDetected) as excinfo:
        await apply(
            empty_plan, config=cfg, engine=engine, actor=System(), registry=registry,
        )

    message = str(excinfo.value)
    assert builder.plugin in message
    assert "0001" in message


def _fake_plugin_info(alias: str, install_path):
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
