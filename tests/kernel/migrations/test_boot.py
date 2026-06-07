"""Tests for boot-time policy enforcement."""

from __future__ import annotations

import pytest

from hearth.kernel.migrations.boot import enforce_boot_policy


def _setup_boot_engine_and_registry(tmp_path, builder):
    """Helper: build engine + registry containing only `builder.plugin`."""
    from hearth.kernel._engine import make_async_engine
    from hearth.kernel.registry import PluginInfo, Registry

    engine = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/boot.db")
    registry = Registry.build()
    registry.plugins[builder.plugin] = PluginInfo(
        alias=builder.plugin,
        package=builder.plugin,
        version="0.0.1",
        module=builder.plugin,
        install_path=str(builder.package_dir),
        depends_on=[],
        extends=[],
        entities=[],
        actions=[],
        events=[],
    )
    return engine, registry


async def test_off_does_nothing_even_with_pending(
    tmp_path,
    monkeypatch,
    tmp_plugin_with_migrations,
    example_revision_body,
) -> None:
    monkeypatch.setenv("HEARTH_DB_MIGRATE", "off")
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
    engine, registry = _setup_boot_engine_and_registry(tmp_path, builder)
    try:
        # Should return cleanly even though a migration is pending.
        await enforce_boot_policy(engine, registry)
    finally:
        await engine.dispose()


async def test_strict_refuses_when_pending(
    tmp_path,
    monkeypatch,
    tmp_plugin_with_migrations,
    example_revision_body,
) -> None:
    monkeypatch.setenv("HEARTH_DB_MIGRATE", "strict")
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
    engine, registry = _setup_boot_engine_and_registry(tmp_path, builder)
    try:
        with pytest.raises(RuntimeError, match=r"strict.*pending"):
            await enforce_boot_policy(engine, registry)
    finally:
        await engine.dispose()


async def test_safe_auto_applies_when_only_safe(
    tmp_path,
    monkeypatch,
    tmp_plugin_with_migrations,
    example_revision_body,
) -> None:
    monkeypatch.setenv("HEARTH_DB_MIGRATE", "safe")
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
    engine, registry = _setup_boot_engine_and_registry(tmp_path, builder)
    try:
        async with engine.begin() as conn:
            from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA

            await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
        await enforce_boot_policy(engine, registry)
        # Assert revision was applied (table exists).
        import sqlalchemy as sa

        async with engine.connect() as conn:
            names = await conn.run_sync(lambda s: set(sa.inspect(s).get_table_names()))
        assert "t" in names
    finally:
        await engine.dispose()


async def test_safe_refuses_when_destructive_pending(
    tmp_path,
    monkeypatch,
    tmp_plugin_with_migrations,
) -> None:
    monkeypatch.setenv("HEARTH_DB_MIGRATE", "safe")
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=f"""
            from alembic import op
            from hearth.migrations import OpSummary

            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)

            HEARTH_UPGRADE_OPS = [
                OpSummary(kind="DropTable", desc="DROP TABLE t", destructive=True),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade(): op.drop_table("t")
            def downgrade(): pass
        """,
    )
    engine, registry = _setup_boot_engine_and_registry(tmp_path, builder)
    try:
        with pytest.raises(RuntimeError, match="destructive"):
            await enforce_boot_policy(engine, registry)
    finally:
        await engine.dispose()


def test_invalid_value_raises_at_read(monkeypatch) -> None:
    monkeypatch.setenv("HEARTH_DB_MIGRATE", "bogus")
    from hearth.kernel.migrations.boot import _read_policy

    with pytest.raises(ValueError):
        _read_policy()
