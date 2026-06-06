"""Boot-time migration policy.

Read HEARTH_DB_MIGRATE; one of:
  - strict (default): refuse to start if any migration pending
  - safe: auto-apply safe migrations; refuse if destructive pending
  - off: skip the check entirely
"""

from __future__ import annotations

import os
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import apply
from hearth.kernel.migrations.planner import compute_plan
from hearth.kernel.registry import Registry
from hearth.primitives.actor import System

Policy = Literal["strict", "safe", "off"]


def _read_policy() -> Policy:
    raw = os.environ.get("HEARTH_DB_MIGRATE", "strict").strip().lower()
    if raw not in ("strict", "safe", "off"):
        raise ValueError(f"HEARTH_DB_MIGRATE must be strict|safe|off, got {raw!r}")
    return raw  # type: ignore[return-value]


async def enforce_boot_policy(engine: AsyncEngine, registry: Registry) -> None:
    """Apply the policy. Raises on refuse."""
    policy = _read_policy()
    if policy == "off":
        return
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    if not plan.revisions:
        return

    if policy == "strict":
        raise RuntimeError(
            f"HEARTH_DB_MIGRATE=strict; refusing to start, "
            f"{len(plan.revisions)} migration(s) pending. "
            f"Run `hearth db migrate` to apply."
        )
    # policy == "safe": auto-apply safe; refuse if destructive pending.
    if plan.has_destructive:
        raise RuntimeError(
            "HEARTH_DB_MIGRATE=safe; refusing to start, plan contains destructive ops. "
            "Run `hearth db migrate` interactively to confirm."
        )
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)
