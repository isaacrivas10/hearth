"""Adoption path for installs created under the auth spec's `db init`.

If a plugin's tables exist in the DB but no `alembic_version` row tracks
them, and the declared schema *structurally* matches the live schema,
stamp the plugin's branch head as applied and write a schema-log row
attributed to `system:adopt`. If the structural comparison fails,
refuse with `AdoptionDriftDetected`.

Cosmetic differences (server-default text, naming) are ignored; only the
ops in `_STRUCTURAL_OPS` count as drift.
"""

from __future__ import annotations

import asyncio
from typing import cast

import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations.audit_log import record_applied_revision
from hearth.kernel.migrations.errors import AdoptionDriftDetected
from hearth.kernel.registry import Registry


def _branch_head(script: ScriptDirectory, branch: str) -> str | None:
    """Return the revision id at the head of `branch`, or None if absent."""
    try:
        sc = script.get_revision(f"{branch}@head")
    except Exception:
        return None
    return sc.revision if sc else None


def _flatten_change(change: object) -> list[object]:
    """`compare_metadata` yields either a single op-tuple or a list of
    sub-op-tuples. Normalize to a flat list of `object` entries so the
    caller can iterate without worrying about the surrounding container.
    """
    if isinstance(change, list):
        return cast(list[object], change)
    return [change]


def _op_kind(entry: object) -> str | None:
    """Return the op-name (`add_table`, `modify_nullable`, …) from an
    Alembic compare-metadata entry, or None if the entry shape is unexpected.
    """
    if isinstance(entry, tuple) and len(cast(tuple[object, ...], entry)) > 0:
        head = cast(tuple[object, ...], entry)[0]
        if isinstance(head, str):
            return head
    return None


# Op classes that count as STRUCTURAL drift; others are cosmetic and ignored.
_STRUCTURAL_OPS = {
    "add_table",
    "remove_table",
    "add_column",
    "remove_column",
    "add_constraint",
    "remove_constraint",
    "modify_nullable",
    "modify_type",
}


async def adopt_if_clean(
    engine: AsyncEngine,
    config: Config,
    registry: Registry,
    *,
    applied_by_actor_id: str,
) -> list[str]:
    """Stamp legacy installs whose live schema matches the declared schema.

    For each plugin whose tables exist but whose branch head has no
    `alembic_version` row: compare declared vs. live structure. If the
    structural diff is empty, stamp `<alias>@head` as applied and write
    an audit row. If the diff is non-empty, raise
    `AdoptionDriftDetected`.

    Returns the list of aliases that were adopted (empty when there is
    nothing to do).
    """
    script = ScriptDirectory.from_config(config)

    async with engine.connect() as conn:
        live_tables = await conn.run_sync(
            lambda s: set(sa.inspect(s).get_table_names())
        )
        applied_revisions: set[str] = set()
        if "alembic_version" in live_tables:
            result = await conn.execute(
                sa.text("SELECT version_num FROM alembic_version")
            )
            applied_revisions = {row[0] for row in result}

    ordered_aliases = registry.full_plugin_order()

    branch_head_revisions: dict[str, str] = {}
    for alias in ordered_aliases:
        head = _branch_head(script, alias)
        if head is not None:
            branch_head_revisions[alias] = head

    candidates: list[str] = []
    for alias in ordered_aliases:
        head = branch_head_revisions.get(alias)
        if head is None:
            continue
        if head in applied_revisions:
            continue  # already tracked by Alembic
        plugin_tables = {
            ent.__tablename__
            for ent in registry.plugins[alias].entities
            if getattr(ent, "__tablename__", None)
        }
        if plugin_tables and plugin_tables.issubset(live_tables):
            candidates.append(alias)

    adopted: list[str] = []
    for alias in candidates:
        diff = await _structural_diff_against_live(engine, config, registry, alias)
        if diff:
            raise AdoptionDriftDetected(
                f"plugin '{alias}': declared schema does not structurally match live DB.\n"
                f"  Drift: {diff}\n"
                f"  Resolve manually before retrying `hearth db migrate`."
            )
        # Stamp Alembic's branch head as applied (sync API → offload thread).
        await asyncio.to_thread(alembic_command.stamp, config, f"{alias}@head")
        head = branch_head_revisions[alias]
        sc = script.get_revision(head)
        # Mirror recovery.py: `Script.doc` can be None for revisions
        # without a docstring; coerce to "" before concatenating so the
        # nullable=False `description` column always gets a string.
        description = ((sc.doc if sc else "") or "") + " [adopted]"
        await record_applied_revision(
            engine,
            plugin=alias,
            revision_id=head,
            description=description,
            applied_by=applied_by_actor_id,
            destructive_ops_acked=[],
        )
        adopted.append(alias)
    return adopted


async def _structural_diff_against_live(
    engine: AsyncEngine,
    config: Config,
    registry: Registry,
    alias: str,
) -> list[str]:
    """Diff the plugin's declared schema against the live DB.

    Builds a `MetaData` from the plugin's declared entities and runs
    Alembic's `compare_metadata`. Filters to `_STRUCTURAL_OPS` so cosmetic
    differences (server-default text, constraint naming, column comments)
    do not block adoption.
    """
    del config  # kept for API symmetry; the live diff goes through `engine`.
    target = sa.MetaData()
    plugin_table_names: set[str] = set()
    for ent in registry.plugins[alias].entities:
        table = getattr(ent, "__table__", None)
        if table is not None:
            table.to_metadata(target)
            plugin_table_names.add(table.name)

    diffs: list[str] = []

    def _include_object(
        obj: sa.schema.SchemaItem,
        name: str | None,
        type_: str,
        reflected: bool,
        compare_to: sa.schema.SchemaItem | None,
    ) -> bool:
        """Limit the comparison to the plugin's declared tables.

        Without this, `compare_metadata` reports kernel-owned tables
        (`_hearth_schema_log`, `_hearth_migration_lock`, `_hearth_outbox`)
        and any other plugin's tables as "remove_table" drift, because
        they are absent from the per-plugin `target` MetaData.
        """
        del reflected, compare_to  # Alembic-defined callback signature.
        if type_ == "table":
            return name in plugin_table_names
        # For columns / constraints / indexes, only consider those whose
        # parent table is one of the declared plugin tables.
        parent = getattr(obj, "table", None)
        if parent is None:
            return True
        return parent.name in plugin_table_names

    def _run(sync_conn: sa.Connection) -> None:
        ctx = MigrationContext.configure(
            connection=sync_conn,
            opts={"include_object": _include_object},
        )
        # `compare_metadata` returns an iterable of either an op-tuple
        # (`(op_name, ...)`) or a list of such tuples (when one change
        # groups multiple sub-ops). Treat each entry as `object` and read
        # `op_name` defensively via the helper below.
        for raw_change in cast(list[object], compare_metadata(ctx, target)):
            entries: list[object] = _flatten_change(raw_change)
            for entry in entries:
                kind = _op_kind(entry)
                if kind in _STRUCTURAL_OPS:
                    diffs.append(repr(entry))

    async with engine.connect() as conn:
        await conn.run_sync(_run)
    return diffs
