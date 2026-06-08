"""Outbox history — kernel-internal Action for reading _hearth_outbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hearth.kernel.persistence import OUTBOX_TABLE
from hearth.primitives.action import Action
from hearth.primitives.actor import Actor
from hearth.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class ActorSnapshot:
    kind: str
    id: str | None
    meta: dict[str, str]


@dataclass(frozen=True)
class OutboxRow:
    id: str
    event_type: str
    payload: dict[str, Any]
    actor: ActorSnapshot
    created_at: datetime


@dataclass(frozen=True)
class OutboxPage:
    rows: list[OutboxRow]
    total: int
    has_next: bool
    has_prev: bool


class OutboxHistory(Action):
    """Kernel-internal Action: query the _hearth_outbox table.

    Not registered in the plugin registry. Imported directly by the admin
    events route. Uses uow.query() — the same public API as plugin Actions.
    """

    limit: int = 50
    offset: int = 0
    event_type: str | None = None

    async def handle(self, uow: UnitOfWork, actor: Actor) -> OutboxPage:
        t = OUTBOX_TABLE

        count_q = uow.query(t)
        if self.event_type is not None:
            count_q = count_q.where(t.c.event_type == self.event_type)
        total: int = await count_q.count()

        rows_q = uow.query(t).order_by(t.c.created_at.desc()).limit(self.limit).offset(self.offset)
        if self.event_type is not None:
            rows_q = rows_q.where(t.c.event_type == self.event_type)
        rows = await rows_q.mappings()

        out_rows: list[OutboxRow] = []
        for row in rows:
            _actor_dict: dict[str, Any] = row["actor"] or {}
            out_rows.append(
                OutboxRow(
                    id=row["id"],
                    event_type=row["event_type"],
                    payload=row["payload"] or {},
                    actor=ActorSnapshot(
                        kind=str(_actor_dict.get("kind", "unknown")),
                        id=_actor_dict.get("id"),
                        meta=_actor_dict.get("meta") or {},
                    ),
                    created_at=row["created_at"],
                )
            )

        return OutboxPage(
            rows=out_rows,
            total=total,
            has_next=self.offset + len(out_rows) < total,
            has_prev=self.offset > 0,
        )
