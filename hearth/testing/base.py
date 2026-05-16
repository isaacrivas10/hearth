"""Shared SQLAlchemy-backed test harness — see docs/core/testing/harness.md.

Both the in-memory `Harness` (SQLite) and `PostgresHarness` are thin
subclasses that supply an `AsyncEngine`; all transaction, table, and outbox
logic lives here so there is exactly one SA-backed code path.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

from sqlalchemy import Connection, Table, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hearth.kernel.persistence import METADATA, OUTBOX_TABLE
from hearth.kernel.transaction import _UnitOfWork  # pyright: ignore[reportPrivateUsage]
from hearth.primitives.action import Action
from hearth.primitives.entity import Entity
from hearth.primitives.event import Event
from hearth.primitives.identity import Identity, System


class _SqlAlchemyHarness:  # pyright: ignore[reportUnusedClass]
    """SQLAlchemy-backed test harness.

    Uses SQLAlchemy AsyncSession so plugin tests get the full ORM contract:
    identity map, dirty tracking, lazy loading. Tracks the tables it created
    so multiple harness instances don't trample each other.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)
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
        identity: Identity | None = None,
    ) -> AsyncGenerator[_UnitOfWork]:
        actor = identity if identity is not None else System()
        async with self._sessionmaker() as session, session.begin():
            uow = _UnitOfWork(session, actor)
            yield uow
            await uow._flush_events()  # pyright: ignore[reportPrivateUsage]

    async def run(self, action: Action, identity: Identity | None = None) -> Any:
        actor = identity if identity is not None else System()
        async with self.transaction(identity=actor) as uow:
            return await action.handle(uow, actor)

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
