"""End-to-end: build_config + alembic.command.upgrade actually run."""

from __future__ import annotations

import asyncio
from pathlib import Path

import sqlalchemy as sa
from alembic import command as alembic_command

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.registry import PluginInfo, Registry


async def test_alembic_upgrade_runs_through_env_py(
    tmp_path: Path,
    tmp_plugin_with_migrations: object,
    example_revision_body: str,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(  # type: ignore[attr-defined]
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001",
            down_revision=None,
            branch=builder.plugin,  # type: ignore[attr-defined]
            table="env_test",
        ),
    )
    engine = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/env.db")
    try:
        registry = Registry.build()
        registry.plugins[builder.plugin] = PluginInfo(  # type: ignore[attr-defined]
            alias=builder.plugin,  # type: ignore[attr-defined]
            package=builder.plugin,  # type: ignore[attr-defined]
            version="0.0.1",
            module=builder.plugin,  # type: ignore[attr-defined]
            install_path=str(builder.package_dir),  # type: ignore[attr-defined]
            depends_on=[],
            extends=[],
            entities=[],
            actions=[],
            events=[],
        )
        cfg = build_config(registry, str(engine.url))
        # Sync invocation — alembic uses sync drivers internally.
        await asyncio.to_thread(
            alembic_command.upgrade,
            cfg,
            f"{builder.plugin}@head",  # type: ignore[attr-defined]
        )

        async with engine.connect() as conn:
            names = await conn.run_sync(lambda s: set(sa.inspect(s).get_table_names()))
        assert "env_test" in names
    finally:
        await engine.dispose()
