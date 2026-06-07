"""Partial-commit recovery check.

Cross-references Alembic's `alembic_version` against `_hearth_schema_log`
to detect revisions whose DDL was applied but whose audit row never
landed (failed during data UoW commit or audit write). The applier
re-runs the data side / writes the missing audit row before the normal
walk.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations.audit_log import SCHEMA_LOG_TABLE
from hearth.kernel.registry import Registry


@dataclass(frozen=True, slots=True)
class OrphanedRevision:
    plugin: str
    revision_id: str
    description: str


async def find_orphaned_revisions(
    engine: AsyncEngine,
    config: Config,
    registry: Registry,
) -> list[OrphanedRevision]:
    """Return revisions whose Alembic head is recorded but whose audit row
    is missing. Ordered (plugin, revision_id)."""
    script = ScriptDirectory.from_config(config)

    async with engine.connect() as conn:
        exists = await conn.run_sync(lambda s: "alembic_version" in sa.inspect(s).get_table_names())
        if not exists:
            return []
        applied = [
            row[0] for row in await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        ]
        logged = {
            (row.plugin, row.revision_id)
            for row in await conn.execute(
                sa.select(SCHEMA_LOG_TABLE.c.plugin, SCHEMA_LOG_TABLE.c.revision_id)
            )
        }

    orphans: list[OrphanedRevision] = []
    for rev_id in applied:
        try:
            sc = script.get_revision(rev_id)
        except Exception:
            # Alembic head points to a revision the script directory no longer
            # knows about (e.g. plugin uninstalled). Not recoverable from here.
            continue
        for label in sc.branch_labels or []:
            if label not in registry.plugins:
                continue
            if (label, rev_id) not in logged:
                orphans.append(
                    OrphanedRevision(plugin=label, revision_id=rev_id, description=sc.doc or "")
                )
    return sorted(orphans, key=lambda o: (o.plugin, o.revision_id))
