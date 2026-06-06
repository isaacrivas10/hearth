"""The `_hearth_schema_log` audit table.

Alembic's `alembic_version` table is the source of truth for *what's
applied*; this table is a pure audit overlay recording *who* applied
*what revision* *when*, including which destructive ops the operator
explicitly acknowledged.

Lives on its own SQLAlchemy MetaData (separate from plugin entity
METADATA) so `db init` can create it without touching plugin tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

SCHEMA_LOG_METADATA = sa.MetaData()

SCHEMA_LOG_TABLE = sa.Table(
    "_hearth_schema_log",
    SCHEMA_LOG_METADATA,
    sa.Column("plugin", sa.Text, nullable=False),
    sa.Column("revision_id", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("applied_by", sa.Text, nullable=False),
    sa.Column("destructive_ops_acked", sa.JSON, nullable=False),
    sa.PrimaryKeyConstraint("plugin", "revision_id"),
)


@dataclass(frozen=True, slots=True)
class SchemaLogRow:
    plugin: str
    revision_id: str
    description: str
    applied_at: datetime
    applied_by: str
    destructive_ops_acked: list[str]


async def record_applied_revision(
    engine: AsyncEngine,
    *,
    plugin: str,
    revision_id: str,
    description: str,
    applied_by: str,
    destructive_ops_acked: list[str],
) -> None:
    """Insert one audit row in its own transaction. Used for DDL-only
    revisions where there is no data UoW to co-commit with.

    For revisions with `data_upgrade`, use `record_applied_revision_in_uow`
    instead — that variant takes a UnitOfWork so the audit row and the
    data live or die together.
    """
    async with engine.begin() as conn:
        await conn.execute(
            SCHEMA_LOG_TABLE.insert().values(
                plugin=plugin,
                revision_id=revision_id,
                description=description,
                applied_at=datetime.now(UTC),
                applied_by=applied_by,
                destructive_ops_acked=list(destructive_ops_acked),
            )
        )


async def record_applied_revision_in_uow(
    uow: Any,
    *,
    plugin: str,
    revision_id: str,
    description: str,
    applied_by: str,
    destructive_ops_acked: list[str],
) -> None:
    """Insert one audit row using an existing UoW's session. Used for
    revisions with `data_upgrade` so the audit row commits atomically
    with the data work. Caller does NOT commit — the caller's UoW
    commits at its own scope exit.
    """
    await uow.execute(
        SCHEMA_LOG_TABLE.insert().values(
            plugin=plugin,
            revision_id=revision_id,
            description=description,
            applied_at=datetime.now(UTC),
            applied_by=applied_by,
            destructive_ops_acked=list(destructive_ops_acked),
        )
    )


async def read_applied_revisions(
    engine: AsyncEngine,
    *,
    plugin: str | None = None,
) -> list[SchemaLogRow]:
    """Return audit rows ordered by (plugin, revision_id). Filter by plugin
    when given.
    """
    async with engine.connect() as conn:
        stmt = sa.select(SCHEMA_LOG_TABLE).order_by(
            SCHEMA_LOG_TABLE.c.plugin, SCHEMA_LOG_TABLE.c.revision_id
        )
        if plugin is not None:
            stmt = stmt.where(SCHEMA_LOG_TABLE.c.plugin == plugin)
        result = await conn.execute(stmt)
        return [
            SchemaLogRow(
                plugin=row.plugin,
                revision_id=row.revision_id,
                description=row.description,
                applied_at=row.applied_at,
                applied_by=row.applied_by,
                destructive_ops_acked=list(row.destructive_ops_acked or []),
            )
            for row in result.mappings()
        ]
