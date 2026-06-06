"""Tests for the adoption path — legacy installs whose tables exist but
were never tracked by Alembic get stamped + audit-rowed on `apply()`.

Per the migration plan (H2): if a plugin's declared schema structurally
matches the live DB and no `alembic_version` row tracks it, stamp the
plugin's branch head as applied and write a `system:adopt` audit row.
If the schema does not match, raise `AdoptionDriftDetected`.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.adopt import adopt_if_clean
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import apply
from hearth.kernel.migrations.audit_log import (
    SCHEMA_LOG_METADATA,
    read_applied_revisions,
)
from hearth.kernel.migrations.errors import AdoptionDriftDetected
from hearth.kernel.migrations.planner import compute_plan
from hearth.kernel.registry import PluginInfo, Registry
from hearth.primitives.actor import System


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/adopt.db")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


def _make_widgets_entity_stub() -> type:
    """Build a duck-typed entity exposing `__tablename__` and `__table__`
    matching the `example_revision_body` CREATE TABLE exactly.

    The real `Entity` metaclass wires everything through SQLAlchemy's
    declarative machinery — that's not needed here. `adopt_if_clean`
    only touches `ent.__tablename__` and `ent.__table__.to_metadata(target)`,
    so a stub with a hand-built `sa.Table` is the cheapest match.
    """
    metadata = sa.MetaData()
    table = sa.Table(
        "widgets",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.String, nullable=True),
    )

    class _WidgetStub:
        __tablename__ = "widgets"
        __table__ = table

    return _WidgetStub


def _fake_plugin_info(alias: str, install_path, entities: list[type]):
    return PluginInfo(
        alias=alias,
        package=alias,
        version="0.0.1",
        module=alias,
        install_path=str(install_path),
        depends_on=[],
        extends=[],
        entities=entities,
        actions=[],
        events=[],
    )


async def test_adopt_stamps_head_when_tables_match_declared_schema(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    """Simulate the legacy `db init` path: tables already exist, no
    `alembic_version` row. After `apply()`, the head should be stamped
    and an audit row attributed to `system:adopt` should exist."""
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="widgets",
        ),
    )

    entity_stub = _make_widgets_entity_stub()

    # Create the plugin's table directly (no Alembic involvement) — this is
    # the state a pre-migrations `db init` would have left the DB in.
    async with engine.begin() as conn:
        await conn.run_sync(entity_stub.__table__.metadata.create_all)

    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent, entities=[entity_stub],
    )
    cfg = build_config(registry, str(engine.url))

    # Sanity: the plan sees 0001 as pending because no alembic_version row
    # exists yet. Adoption inside `apply()` will skip it.
    plan = await compute_plan(engine, cfg, registry)
    assert [(r.plugin, r.revision_id) for r in plan.revisions] == [
        (builder.plugin, "0001"),
    ]

    report = await apply(
        plan, config=cfg, engine=engine, actor=System(), registry=registry,
    )

    # Adoption fired; revision was NOT re-applied.
    assert report.adopted == [builder.plugin]
    assert report.applied_revisions == []
    assert report.aborted_at is None

    # Alembic now tracks the plugin's head.
    async with engine.connect() as conn:
        result = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        applied = {row[0] for row in result}
    assert applied == {"0001"}

    # Audit row exists, attributed to system:adopt, with the `[adopted]` marker.
    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert len(rows) == 1
    assert rows[0].revision_id == "0001"
    assert rows[0].applied_by == "system:adopt"
    assert rows[0].destructive_ops_acked == []
    assert rows[0].description.endswith("[adopted]")


async def test_adopt_raises_when_live_schema_drifts_from_declared(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    """If the live table is missing a declared column (structural drift),
    `adopt_if_clean` refuses with `AdoptionDriftDetected`."""
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="widgets",
        ),
    )

    # Declared schema includes `id` + `name`; live DB only has `id` —
    # `add_column` should fire on compare_metadata.
    entity_stub = _make_widgets_entity_stub()
    drifted_metadata = sa.MetaData()
    sa.Table(
        "widgets",
        drifted_metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
    )
    async with engine.begin() as conn:
        await conn.run_sync(drifted_metadata.create_all)

    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent, entities=[entity_stub],
    )
    cfg = build_config(registry, str(engine.url))

    with pytest.raises(AdoptionDriftDetected) as excinfo:
        await adopt_if_clean(
            engine, cfg, registry, applied_by_actor_id="system:adopt",
        )
    assert "widgets" in str(excinfo.value) or builder.plugin in str(excinfo.value)


async def test_adopt_skips_plugins_already_tracked_by_alembic(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    """A plugin whose `alembic_version` row already exists is left alone."""
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="widgets",
        ),
    )
    entity_stub = _make_widgets_entity_stub()

    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent, entities=[entity_stub],
    )
    cfg = build_config(registry, str(engine.url))

    # Normal flow: plan + apply, so alembic_version has 0001 and the audit
    # row exists.
    plan = await compute_plan(engine, cfg, registry)
    first_report = await apply(
        plan, config=cfg, engine=engine, actor=System(), registry=registry,
    )
    assert first_report.adopted == []
    assert first_report.applied_revisions == [(builder.plugin, "0001")]

    # Second apply: no plan revisions, nothing to adopt, nothing to log.
    plan2 = await compute_plan(engine, cfg, registry)
    second_report = await apply(
        plan2, config=cfg, engine=engine, actor=System(), registry=registry,
    )
    assert second_report.adopted == []
    assert second_report.applied_revisions == []

    # Audit row count is still exactly 1.
    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert len(rows) == 1
