"""Compute a `MigrationPlan`: ordered list of pending revisions per plugin.

Uses Alembic's `ScriptDirectory.walk_revisions(...)` to enumerate the
chain between each plugin's applied head (from the shared `alembic_version`
table) and its declared head (the latest revision in the package). Per-
revision `HEARTH_UPGRADE_OPS` lists are read at this stage; missing lists
raise `MissingClassificationArtifact`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations.errors import MissingClassificationArtifact
from hearth.kernel.registry import Registry
from hearth.migrations import OpSummary


@dataclass(frozen=True, slots=True)
class PlannedRevision:
    plugin: str
    revision_id: str
    description: str
    ops: list[OpSummary]

    @property
    def destructive_ops(self) -> list[OpSummary]:
        return [o for o in self.ops if o.destructive]


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    revisions: list[PlannedRevision] = field(default_factory=list)

    @property
    def has_destructive(self) -> bool:
        return any(r.destructive_ops for r in self.revisions)

    def format(self, *, color: bool = False) -> str:
        """Human-readable rendering for CLI / operator review."""
        if not self.revisions:
            return "No pending migrations."
        lines: list[str] = ["Migration plan:"]
        for r in self.revisions:
            lines.append(f"  {r.plugin}: {r.revision_id} — {r.description}")
            for op in r.ops:
                marker = "[!!]" if op.destructive else "[ok]"
                lines.append(f"    {marker} {op.desc}")
        return "\n".join(lines)


async def compute_plan(engine: AsyncEngine, config: Config, registry: Registry) -> MigrationPlan:
    """Walk each plugin's pending revisions and produce a MigrationPlan."""
    revisions: list[PlannedRevision] = []
    script = ScriptDirectory.from_config(config)
    applied = await _read_alembic_version_per_branch(engine, script)

    for alias in registry.full_plugin_order():
        head = _branch_head(script, alias)
        if head is None:
            continue
        current = applied.get(alias)
        if current == head:
            # Already at head — nothing pending.
            continue
        for sc in _walk_pending(script, current, head):
            # Alembic's Script.module gives the already-loaded revision
            # module — avoid re-importing via importlib to prevent module
            # isolation issues during tests that manipulate sys.modules.
            ops = _read_hearth_upgrade_ops(sc.module, alias, sc.revision)
            revisions.append(
                PlannedRevision(
                    plugin=alias,
                    revision_id=sc.revision,
                    description=sc.doc or "",
                    ops=ops,
                )
            )
    return MigrationPlan(revisions=revisions)


async def _read_alembic_version_per_branch(
    engine: AsyncEngine, script: ScriptDirectory
) -> dict[str, str]:
    """Map each branch label to the revision currently applied for that branch.

    Alembic stores one row per active branch head in the shared
    `alembic_version` table. Each row identifies a revision; we look the
    revision up in the script directory to recover which branch it
    belongs to.
    """
    async with engine.connect() as conn:
        exists = await conn.run_sync(lambda s: "alembic_version" in sa.inspect(s).get_table_names())
        if not exists:
            return {}
        result = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        applied_revisions = [row[0] for row in result]

    head_by_branch: dict[str, str] = {}
    for rev in applied_revisions:
        try:
            sc = script.get_revision(rev)
        except Exception:
            # Row points to a revision the current script directory does not
            # know about (orphaned by a plugin uninstall, say). Skip; recovery
            # paths handle that case separately.
            continue
        for label in sc.branch_labels or []:
            head_by_branch[label] = rev
    return head_by_branch


def _branch_head(script: ScriptDirectory, branch: str) -> str | None:
    """Return the revision id at the head of `branch`, or None if the branch
    is not present in the script directory (plugin ships no migrations yet).
    """
    try:
        sc = script.get_revision(f"{branch}@head")
    except Exception:
        return None
    return sc.revision


def _walk_pending(script: ScriptDirectory, current: str | None, head: str) -> Iterator[Any]:
    """Yield Alembic Script objects from `current` (exclusive) to `head` (inclusive)."""
    revisions = list(script.walk_revisions(base=current or "base", head=head))
    # walk_revisions yields newest first; we want oldest first to apply in order.
    # When `current` is a specific revision, Alembic includes it in the range;
    # filter it out so we only yield revisions strictly after the applied head.
    if current is not None:
        revisions = [r for r in revisions if r.revision != current]
    return reversed(revisions)


def _read_hearth_upgrade_ops(module: ModuleType, plugin: str, revision_id: str) -> list[OpSummary]:
    ops = getattr(module, "HEARTH_UPGRADE_OPS", None)
    if ops is None:
        raise MissingClassificationArtifact(
            f"plugin '{plugin}' revision '{revision_id}' lacks HEARTH_UPGRADE_OPS; "
            "cannot determine destructive ops. Run `hearth migrate generate --refresh` "
            "or add the lists by hand."
        )
    return list(ops)
