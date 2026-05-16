"""Shared test-harness base — see docs/core/testing/harness.md.

`InMemoryHarness` (SQLite-in-memory) and `PostgresHarness` are thin
subclasses that supply an `AsyncEngine`; all transaction, table, and outbox
logic lives here so there is exactly one SA-backed code path. `BaseHarness`
is the public type plugin authors use to annotate parametrized
`make_harness` fixtures so a single test body runs against either backend.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

from sqlalchemy import Connection, Table, select
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.persistence import METADATA, OUTBOX_TABLE
from hearth.kernel.transaction import (
    _UnitOfWork,  # pyright: ignore[reportPrivateUsage]
    transaction_factory,
)
from hearth.primitives.action import Action
from hearth.primitives.actor import Actor, System
from hearth.primitives.entity import Entity
from hearth.primitives.event import Event


class BaseHarness:
    """Shared base for SA-backed test harnesses.

    Concrete subclasses (`InMemoryHarness`, `PostgresHarness`) supply the engine;
    table creation/teardown, transaction scoping, and outbox introspection
    live here. Plugin authors annotate parametrized `make_harness` fixtures
    with this type so a single test body runs against both SQLite-in-memory
    and Postgres.

    Backed by SQLAlchemy AsyncSession to give plugin tests the full ORM
    contract — identity map, dirty tracking, lazy loading — but plugins
    never need to import from `sqlalchemy` to use it.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._make_uow = transaction_factory(engine)
        self._tables: list[Table] = []

    async def setup(self, entities: list[type[Entity]] | None = None) -> None:
        if entities is None:
            entities = _all_entity_subclasses(Entity)
        self._tables = [cast("Table", cls.__table__) for cls in entities]
        self._tables.append(OUTBOX_TABLE)
        async with self._engine.begin() as conn:
            await conn.run_sync(self._create_tables)

    def _create_tables(self, conn: Connection) -> None:
        METADATA.create_all(conn, tables=self._tables, checkfirst=True)

    async def teardown(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(self._drop_tables)
        await self._engine.dispose()

    def _drop_tables(self, conn: Connection) -> None:
        METADATA.drop_all(conn, tables=list(reversed(self._tables)), checkfirst=True)

    async def reset(self) -> None:
        async with self._engine.begin() as conn:
            for table in reversed(self._tables):
                await conn.execute(table.delete())

    @asynccontextmanager
    async def transaction(
        self,
        actor: Actor | None = None,
    ) -> AsyncGenerator[_UnitOfWork]:
        active_actor = actor if actor is not None else System()
        async with self._make_uow(actor=active_actor) as uow:
            yield uow

    async def run(self, action: Action, actor: Actor | None = None) -> Any:
        active_actor = actor if actor is not None else System()
        async with self.transaction(actor=active_actor) as uow:
            return await action.handle(uow, active_actor)

    async def events_of_type[E: Event](self, event_type: type[E]) -> list[E]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(OUTBOX_TABLE)
                .where(OUTBOX_TABLE.c.event_type == event_type.__name__)
                .order_by(OUTBOX_TABLE.c.id),
            )
            return [event_type.model_validate(row.payload) for row in result.mappings()]


def _all_entity_subclasses(root: type[Entity]) -> list[type[Entity]]:
    seen: set[type[Entity]] = set()
    out: list[type[Entity]] = []

    def walk(cls: type[Entity]) -> None:
        for sub in cls.__subclasses__():
            if sub in seen:
                continue
            seen.add(sub)
            if not sub.__dict__.get("__abstract__", False):
                out.append(sub)
            walk(sub)

    walk(root)
    return out
