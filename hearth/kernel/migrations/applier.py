"""Apply a `MigrationPlan` against an engine, with single-writer locking
and per-op interactive confirmation for destructive ops.

This file handles only the DDL path (Alembic `command.upgrade` per
revision plus audit-row write). Data hooks (`data_upgrade` /
`data_downgrade`) are added in Phase E.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations._lock import acquire_migration_lock
from hearth.kernel.migrations.adopt import adopt_if_clean
from hearth.kernel.migrations.audit_log import (
    SCHEMA_LOG_TABLE,
    record_applied_revision,
    record_applied_revision_in_uow,
)
from hearth.kernel.migrations.data_uow import open_data_uow
from hearth.kernel.migrations.errors import (
    PartialCommitUnrecoverable,
    PluginDowngradeDetected,
)
from hearth.kernel.migrations.planner import MigrationPlan, PlannedRevision
from hearth.kernel.migrations.recovery import find_orphaned_revisions
from hearth.kernel.registry import Registry
from hearth.migrations import OpSummary
from hearth.primitives.actor import Actor, serialize_actor

ConfirmCallback = Callable[[PlannedRevision, OpSummary], Awaitable[bool]]


@dataclass
class AppliedReport:
    """Outcome of an `apply(...)` call.

    `applied_revisions` lists `(plugin, revision_id)` tuples for every
    revision committed in order. `aborted_at` is set when the loop stopped
    early because a destructive op was declined (or `confirm is None`);
    revisions listed in `applied_revisions` before the abort remain
    committed. `adopted` lists plugin aliases whose existing tables were
    structurally adopted (legacy `db init` installs); their pending
    revisions in `plan.revisions` are skipped because adoption stamps
    `<alias>@head` retroactively.
    """

    applied_revisions: list[tuple[str, str]] = field(default_factory=list)
    aborted_at: tuple[str, str] | None = None
    adopted: list[str] = field(default_factory=list)


async def apply(
    plan: MigrationPlan,
    *,
    config: Config,
    engine: AsyncEngine,
    actor: Actor,
    registry: Registry,
    confirm: ConfirmCallback | None = None,
) -> AppliedReport:
    """Apply `plan` under a migration lock.

    For each destructive op in each pending revision, call `confirm`. If
    `confirm` returns False (or is None and the op is destructive), abort
    the plan at that op — everything previously committed stays.
    """
    report = AppliedReport()
    actor_id = serialize_actor(actor)

    async with acquire_migration_lock(engine):
        # Phase H3: detect plugin downgrade — the `alembic_version` table
        # references a revision the currently-installed plugin package no
        # longer ships. Must run before adoption (which writes new
        # `alembic_version` rows) and before orphan recovery (which silently
        # skips unknown revisions). Cheap precheck: one indexed read.
        await _detect_plugin_downgrade(engine, config, registry)

        # Phase H2: adopt legacy installs whose tables exist but were never
        # tracked by Alembic. Runs BEFORE orphan recovery because adoption
        # writes the first `alembic_version` and audit rows for the plugin;
        # orphan recovery then sees a consistent state. Any plugin adopted
        # here has its pending revisions in `plan.revisions` skipped in the
        # main loop below — stamping `<alias>@head` retroactively marks
        # everything as applied.
        adopted_aliases = await adopt_if_clean(
            engine, config, registry, applied_by_actor_id=f"{actor_id}:adopt"
        )
        report.adopted = list(adopted_aliases)
        adopted_set = set(adopted_aliases)

        # Phase D1: detect orphaned revisions (Alembic head advanced but
        # audit row missing) and write the missing audit rows before the
        # normal walk. Phase E2c extends this to also re-run data_upgrade.
        orphans = await find_orphaned_revisions(engine, config, registry)
        for orphan in orphans:
            try:
                revision_module = _import_revision_module(config, orphan.revision_id)
                data_upgrade = getattr(revision_module, "data_upgrade", None)
                if data_upgrade is not None:
                    async with open_data_uow(engine, plugin=orphan.plugin) as uow:
                        await data_upgrade(uow)
                        await record_applied_revision_in_uow(
                            uow,
                            plugin=orphan.plugin,
                            revision_id=orphan.revision_id,
                            description=orphan.description,
                            applied_by=f"{actor_id}:recovery",
                            destructive_ops_acked=[],
                        )
                else:
                    await record_applied_revision(
                        engine,
                        plugin=orphan.plugin,
                        revision_id=orphan.revision_id,
                        description=orphan.description,
                        applied_by=f"{actor_id}:recovery",
                        destructive_ops_acked=[],
                    )
            except Exception as err:
                raise PartialCommitUnrecoverable(
                    f"failed to recover orphaned revision "
                    f"{orphan.plugin}:{orphan.revision_id}"
                ) from err

        for revision in plan.revisions:
            if revision.plugin in adopted_set:
                # The plugin's head was stamped during adoption; the live
                # schema already matches, so running this revision would
                # collide (e.g. CREATE TABLE on an existing table).
                continue
            aborted = False
            for op in revision.destructive_ops:
                if confirm is None or not await confirm(revision, op):
                    report.aborted_at = (revision.plugin, revision.revision_id)
                    aborted = True
                    break
            if aborted:
                return report

            # Apply this revision via Alembic (its own transaction).
            await _alembic_upgrade_one(config, revision.revision_id)

            # Run the revision's data_upgrade hook, if any, in its own UoW.
            # The audit-row write moves INSIDE the same UoW so the audit row
            # and the data work commit (or roll back) atomically together.
            # Revisions without a data hook keep the standalone audit-row
            # write path (its own short-lived transaction).
            revision_module = _import_revision_module(config, revision.revision_id)
            data_upgrade = getattr(revision_module, "data_upgrade", None)
            if data_upgrade is not None:
                async with open_data_uow(engine, plugin=revision.plugin) as uow:
                    await data_upgrade(uow)
                    await record_applied_revision_in_uow(
                        uow,
                        plugin=revision.plugin,
                        revision_id=revision.revision_id,
                        description=revision.description,
                        applied_by=actor_id,
                        destructive_ops_acked=[o.desc for o in revision.destructive_ops],
                    )
                # UoW exit commits; data + audit are atomic together.
            else:
                await record_applied_revision(
                    engine,
                    plugin=revision.plugin,
                    revision_id=revision.revision_id,
                    description=revision.description,
                    applied_by=actor_id,
                    destructive_ops_acked=[o.desc for o in revision.destructive_ops],
                )
            report.applied_revisions.append((revision.plugin, revision.revision_id))

    return report


async def _detect_plugin_downgrade(
    engine: AsyncEngine, config: Config, registry: Registry
) -> None:
    """Raise `PluginDowngradeDetected` if `alembic_version` references a
    revision the installed plugin package no longer ships.

    This is the user-facing failure when an operator deploys an older
    package version against a database that was migrated by a newer one.
    Alembic itself has no notion of "shipped vs applied"; it just trusts
    the version table. We catch this before adoption/recovery because both
    of those paths silently skip unknown revisions (see
    `find_orphaned_revisions` and `planner._read_alembic_version_per_branch`),
    which would mask the condition.

    Cheap precheck: one `SELECT` against `alembic_version` and one
    `script.get_revision(...)` per row (in-memory after the first call).
    """
    script = ScriptDirectory.from_config(config)
    async with engine.connect() as conn:
        exists = await conn.run_sync(
            lambda s: "alembic_version" in sa.inspect(s).get_table_names()
        )
        if not exists:
            return
        applied_revs = [
            row[0]
            for row in await conn.execute(
                sa.text("SELECT version_num FROM alembic_version")
            )
        ]
        if not applied_revs:
            return

        # Audit-log fallback for alias resolution: the missing revision file
        # is the only place branch_labels live, so once it's gone we cannot
        # recover the alias from Alembic. The audit table preserves it.
        log_exists = await conn.run_sync(
            lambda s: "_hearth_schema_log" in sa.inspect(s).get_table_names()
        )
        alias_by_rev: dict[str, str] = {}
        if log_exists:
            for row in await conn.execute(
                sa.select(SCHEMA_LOG_TABLE.c.plugin, SCHEMA_LOG_TABLE.c.revision_id)
            ):
                alias_by_rev[row.revision_id] = row.plugin

    for rev_id in applied_revs:
        sc = None
        try:
            sc = script.get_revision(rev_id)
        except Exception:
            sc = None
        if sc is not None:
            continue
        # Revision is unknown to the script directory → package downgraded
        # (or the file was deleted by hand). Build an actionable message.
        alias = alias_by_rev.get(rev_id, "<unknown>")
        shipped_head: str | None = None
        if alias != "<unknown>" and alias in registry.plugins:
            try:
                shipped = script.get_revision(f"{alias}@head")
                # Alembic's stub types `get_revision` as non-None but historically
                # it could return None; guard defensively to keep behavior aligned
                # with `_import_revision_module` above.
                shipped_head = (
                    shipped.revision
                    if shipped is not None  # pyright: ignore[reportUnnecessaryComparison]
                    else None
                )
            except Exception:
                shipped_head = None
        raise PluginDowngradeDetected(
            f"plugin '{alias}': database is at revision '{rev_id}', "
            f"which the installed package no longer ships "
            f"(current shipped head: {shipped_head or '<none>'}). "
            "Reinstall the package version that introduced "
            f"'{rev_id}', then run `hearth db migrate` to downgrade cleanly; "
            "the kernel does not auto-downgrade."
        )


async def _alembic_upgrade_one(config: Config, revision_id: str) -> None:
    """Run `alembic.command.upgrade` for one revision. Alembic uses sync
    drivers; we offload to a thread."""
    await asyncio.to_thread(alembic_command.upgrade, config, revision_id)


def _import_revision_module(config: Config, revision_id: str):
    """Return the already-loaded revision module for `revision_id`.

    Alembic's `ScriptDirectory.get_revision(...).module` lazy-loads on first
    access — we use that rather than `importlib.import_module` to avoid
    module duplication during tests that manipulate `sys.modules`.
    """
    script = ScriptDirectory.from_config(config)
    sc = script.get_revision(revision_id)
    # Alembic's stub types `get_revision` as non-None, but historically it
    # could return None for unknown revisions; guard defensively.
    if sc is None:  # pyright: ignore[reportUnnecessaryComparison]
        raise RuntimeError(f"revision {revision_id} not found in script directory")
    return sc.module


